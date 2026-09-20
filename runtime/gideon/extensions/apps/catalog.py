"""App catalog — what's AVAILABLE to install, for the Store half of the App page.

The App page has two halves:
  * **Library** — what's installed (``apps.manager.list_apps``).
  * **Store** — what's available to install, which this module enumerates from two
    sources:
      1. **Native** — manifests Gideon ships under ``apps/native/`` (native)
         that aren't currently installed (e.g. a default provider the user
         force-uninstalled, or a bundled app they haven't added yet).
      2. **Git sources** — a user-managed list of git URLs (seeded with any
         Gideon-bundled defaults). Each entry is an installable app source;
         the catalog reports it as available without cloning (the clone happens at
         install time, behind the scanner gate).

A catalog entry is metadata only — installing one routes through the normal
``app_manager.install`` (path for bundled, git URL for sources), so the scanner
gate + lifecycle are unchanged.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.extensions.apps.manifest import AppManifest, version_tuple

logger = logging.getLogger(__name__)

_SOURCES_FILENAME = "app-sources.json"

_HERO_MAX_BYTES = 1_500_000
_HERO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


def resolve_hero_url(app_dir: Path, hero_rel: str) -> str:
    """Resolve a manifest ``heroImage`` (relative path) under ``app_dir`` to a
    ``data:`` URI, or ``""`` when unset / missing / disallowed. Traversal-guarded,
    type-allowlisted, and size-capped — a bad value degrades to no hero, never an
    error (the card just falls back to the icon layout)."""
    import base64

    rel = (hero_rel or "").strip()
    if not rel:
        return ""
    try:
        root = app_dir.resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            return ""
        if not target.is_file():
            return ""
        mime = _HERO_MIME.get(target.suffix.lower())
        if not mime:
            return ""
        data = target.read_bytes()
        if len(data) > _HERO_MAX_BYTES:
            logger.debug(
                "hero image %s exceeds %d bytes — skipping", target, _HERO_MAX_BYTES
            )
            return ""
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except OSError:
        logger.debug(
            "could not read hero image %r under %s", hero_rel, app_dir, exc_info=True
        )
        return ""


import os as _os

_CATALOG_SOURCES_ENV = "GIDEON_APP_CATALOG_URLS"
_REGISTRY_SOURCE_ENV = "GIDEON_APP_REGISTRY_URL"
_DEFAULT_GIT_SOURCES: tuple[str, ...] = ()
_REGISTRY_GIT_SOURCE = ""
_SEEDED_REGISTRY_KEY = "registry"


def _first_party_source() -> Path | None:
    """Locate the optional workspace app collection without contacting a remote."""
    workspace_apps = Path(__file__).resolve().parents[4] / "apps"
    return workspace_apps if workspace_apps.is_dir() else None


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


_FIRST_PARTY_ENV = "GIDEON_FIRST_PARTY_APPS_DIR"


@dataclass
class CatalogEntry:
    """One available-to-install app surfaced in the Store."""

    name: str
    displayName: str  # noqa: N815
    description: str = ""
    version: str = ""
    icon: str = ""
    heroUrl: str = (
        ""  # noqa: N815 — resolved data: URI (from manifest heroImage), "" if none
    )
    author: str = ""
    source: str = ""
    sourceKind: str = "bundled"  # noqa: N815 — "bundled" | "git"
    isProvider: bool = False  # noqa: N815
    providerType: str = ""  # noqa: N815
    providerCapabilities: list[str] = field(default_factory=list)  # noqa: N815
    tags: list[str] = field(default_factory=list)
    pointer: str = ""
    permissions: dict[str, Any] = field(default_factory=dict)
    crons: list[dict[str, Any]] = field(default_factory=list)
    hasUI: bool = False  # noqa: N815
    uiComponents: str = ""  # noqa: N815
    quality: dict[str, Any] = field(default_factory=dict)
    coreCompatibility: dict[str, Any] = field(default_factory=dict)  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SOURCE_PRECEDENCE: tuple[str, ...] = (
    "native",
    "bundled",
    "first-party",
    "local",
    "git",
)

_ORIGIN_TO_SOURCE_KIND: dict[str, str] = {
    "builtin": "bundled",
    "registry": "bundled",
    "local": "local",
    "external": "git",
}


def precedence_rank(source_kind: str) -> int:
    """Where *source_kind* sits on :data:`SOURCE_PRECEDENCE` — lower wins a collision.

    An unrecognised kind ranks last (never wins), so adding a source kind without
    placing it on the ladder degrades to "loses every collision" rather than to
    "wins by accident"."""
    try:
        return SOURCE_PRECEDENCE.index(source_kind)
    except ValueError:
        return len(SOURCE_PRECEDENCE)


def source_kind_for_origin(origin: str, *, native: bool = False) -> str:
    """The ``sourceKind`` vocabulary term for an INSTALLED app's recorded ``origin``.

    The Store speaks ``sourceKind``; the Library speaks ``origin``. Surfaces that must
    label an installed app's provenance (Settings → Tools) read this rather than
    re-deciding, which is how #2514 happened: that page badged every non-locked native
    provider ``built-in``, collapsing "shipped with the product" into "I installed it
    from somewhere". Returns ``""`` for an origin with no reading, so a caller shows
    NOTHING rather than guessing."""
    if native:
        return "native"
    return _ORIGIN_TO_SOURCE_KIND.get(origin.strip(), "")


def resolve_catalog_entries(entries: list[CatalogEntry]) -> list[CatalogEntry]:
    """THE single place that decides which copy of an app name the Store surfaces.

    Two jobs, both of which used to be spread across every scanner:

    * **Library exclusion.** An app already installed is not "available to install" —
      it lives in the Library tab. Done once here, so a scanner's CACHED result can no
      longer hide an app that was uninstalled after the cache filled.
    * **Collision resolution.** At most ONE entry per name survives, chosen by
      :data:`SOURCE_PRECEDENCE`. The wire payload therefore carries no name twice, which
      is what makes every consumer agree: no concatenation order, filter or lookup
      downstream can resolve a collision differently, because there is none left to
      resolve.

    Ties inside one rank go to the first entry seen, which preserves the existing
    listed-source order (defaults before user entries). Insertion order is preserved
    for the survivors so the Store's grouping is stable across reads.

    🔴 If you are adding a second place that picks between same-named entries, stop —
    ``test_one_owner_resolves_a_catalog_name_collision`` reds on exactly that.
    """
    installed = _installed_names()
    winners: dict[str, CatalogEntry] = {}
    for entry in entries:
        if not entry.name or entry.name in installed:
            continue
        current = winners.get(entry.name)
        if current is None:
            winners[entry.name] = entry
        elif precedence_rank(entry.sourceKind) < precedence_rank(current.sourceKind):
            winners[entry.name] = entry
    return list(winners.values())


_REGISTRY_FILENAME = "app-registry.json"
_REGISTRY_TTL_SECS = 3600.0
_registry_cache: dict[str, tuple[float, list["RegistryPointer"]]] = {}

_GIT_SCAN_TTL_SECS = 300.0
_git_scan_cache: dict[str, tuple[float, list["CatalogEntry"]]] = {}
_catalog_build_lock = threading.Lock()


@dataclass
class RegistryPointer:
    """One entry in a source's ``app-registry.json`` — a pointer to an installable
    app, resolved to a CatalogEntry card without cloning. ``repo``/``subdirectory``
    build the install pointer; the display fields are index-provided hints (the
    authoritative manifest is only read at install time)."""

    name: str
    repo: str = ""
    branch: str = ""
    subdirectory: str = ""
    displayName: str = ""  # noqa: N815 — index hint
    description: str = ""
    version: str = ""
    icon: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegistryPointer | None":
        name = str(d.get("name", "")).strip()
        if not name:
            return None
        return cls(
            name=name,
            repo=str(d.get("repo", "")).strip(),
            branch=str(d.get("branch", "")).strip(),
            subdirectory=str(d.get("subdirectory", "")).strip(),
            displayName=str(d.get("displayName", "")).strip(),
            description=str(d.get("description", "")).strip(),
            version=str(d.get("version", "")).strip(),
            icon=str(d.get("icon", "")).strip(),
            author=str(d.get("author", "")).strip(),
            tags=[str(t) for t in (d.get("tags") or []) if str(t).strip()],
        )


def _parse_registry(text: str) -> list[RegistryPointer]:
    """Parse ``app-registry.json`` content → pointer list. Tolerant: accepts either a
    bare array of pointers or an object ``{"apps": [...]}``; drops malformed entries;
    returns [] on any parse error (caller falls back to the scan path)."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("app registry: unparseable index", exc_info=True)
        return []
    raw = data.get("apps", []) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out: list[RegistryPointer] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        p = RegistryPointer.from_dict(item)
        if p is None or p.name in seen:
            continue
        seen.add(p.name)
        out.append(p)
    return out


def _read_git_registry(url: str) -> str | None:
    """Fetch ONLY ``app-registry.json`` from a git source, cheaply — a shallow
    treeless clone (blob:none, depth 1) then read the one file, no full checkout of
    every app. Returns the file text, "" if the source has no index, or None on a
    git/timeout error (caller falls back to clone-then-scan). Never raises."""
    import subprocess
    import tempfile

    tmp = tempfile.mkdtemp(prefix="gideon-registry-")
    try:
        proc = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--no-checkout",
                "--",
                url,
                tmp,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            logger.debug(
                "app registry: git fetch failed for %s: %s",
                url,
                (proc.stderr or "")[-200:],
            )
            return None
        show = subprocess.run(
            ["git", "-C", tmp, "show", f"HEAD:{_REGISTRY_FILENAME}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return show.stdout if show.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.debug("app registry: git fetch errored for %s", url, exc_info=True)
        return None
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_registry_index(
    source: str, *, is_git: bool, now: float
) -> list[RegistryPointer] | None:
    """Return a source's registry-index pointers, cached ~1h. None = the source has
    NO usable index (caller keeps the clone-then-scan / dir-scan path). Never raises.

    ``now`` (epoch secs) is injected so the TTL is deterministic in tests."""
    cached = _registry_cache.get(source)
    if cached is not None and (now - cached[0]) < _REGISTRY_TTL_SECS:
        return cached[1] or None
    text: str | None
    if is_git:
        text = _read_git_registry(source)
        if text is None:
            return None
    else:
        p = Path(source).expanduser() / _REGISTRY_FILENAME
        try:
            text = p.read_text(encoding="utf-8") if p.is_file() else ""
        except OSError:
            return None
    pointers = _parse_registry(text) if text else []
    _registry_cache[source] = (now, pointers)
    return pointers or None


def _pointer_to_entry(source: str, p: RegistryPointer, *, is_git: bool) -> CatalogEntry:
    """Build a Store card from a registry pointer. The install POINTER is the repo the
    pointer names (falling back to the source itself), with a ``#subdirectory`` suffix
    when the app lives in a subdir — the exact string install hands to source.resolve.
    """
    repo = p.repo or source
    pointer = repo + (f"#{p.subdirectory}" if p.subdirectory else "")
    return CatalogEntry(
        name=p.name,
        displayName=p.displayName or p.name,
        description=p.description,
        version=p.version,
        icon=p.icon,
        author=p.author,
        source=source,
        sourceKind="git" if is_git else "local",
        tags=list(p.tags),
        pointer=pointer,
    )


def _scan_registries(*, now: float) -> list[CatalogEntry]:
    """Enumerate apps from every configured source's registry index (git + local),
    as install cards — WITHOUT cloning each app. Sources with no index contribute
    nothing here (their apps still surface via the existing git-URL list / local
    dir-scan). Skips apps already installed or already surfaced by a dir-scan.

    That last sentence is now TRUE, and it is :func:`resolve_catalog_entries` that makes
    it true. This function used to claim it while carrying a private ``seen`` set that
    only knew about its own two loops — and because the git loop runs first and shared
    that set, a REMOTE pointer silently dropped the LOCAL pointer for the same name
    (#2528 finding 2), the exact opposite of the promise. Enumeration and precedence are
    separate jobs now: this one lists everything it can see, and the resolver decides.
    """
    out: list[CatalogEntry] = []
    for url in list_git_sources():
        for p in _fetch_registry_index(url, is_git=True, now=now) or []:
            out.append(_pointer_to_entry(url, p, is_git=True))
    for root in list_local_sources():
        for p in _fetch_registry_index(root, is_git=False, now=now) or []:
            out.append(_pointer_to_entry(root, p, is_git=False))
    return out


def _scan_git_source(url: str, *, now: float) -> list[CatalogEntry]:
    """Shallow-clone a git source, scan immediate subdirs for ``app.json``,
    and return installable CatalogEntry objects (with ``pointer=url#subdir``).

    Returns cached results within the TTL. Returns [] on any clone/scan error
    (resilient — a bad source degrades to invisible, never an error page).
    Skips sources that have a registry index (handled by ``_scan_registries``).

    Enumeration only: install-state and name-collision filtering belong to
    :func:`resolve_catalog_entries`. Keeping them out of the CACHED result is also a
    fix — a cache filled while an app was installed used to keep hiding that app for
    up to the TTL after it was uninstalled.
    """
    import shutil
    import subprocess
    import tempfile

    cached = _git_scan_cache.get(url)
    if cached is not None and (now - cached[0]) < _GIT_SCAN_TTL_SECS:
        return cached[1]

    entries: list[CatalogEntry] = []
    tmp = tempfile.mkdtemp(prefix="gideon-gitscan-")
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--", url, tmp],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0:
            logger.debug(
                "git scan: clone failed for %s: %s",
                url,
                (proc.stderr or "")[-200:],
            )
            _git_scan_cache[url] = (now, [])
            return []

        root = Path(tmp)

        if (root / _REGISTRY_FILENAME).is_file():
            _git_scan_cache[url] = (now, [])
            return []

        if (root / "app.json").is_file():
            _git_scan_cache[url] = (now, [])
            return []

        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            manifest_file = entry / "app.json"
            if not manifest_file.is_file():
                continue
            try:
                m = AppManifest.from_json_file(manifest_file)
            except Exception:
                logger.debug(
                    "git scan: bad manifest %s in %s",
                    entry.name,
                    url,
                    exc_info=True,
                )
                continue
            _perms, _crons = _manifest_consent(m)
            entries.append(
                CatalogEntry(
                    name=m.name,
                    displayName=m.displayName or m.name,
                    description=m.description,
                    version=m.version,
                    icon=m.icon,
                    heroUrl=resolve_hero_url(entry, m.heroImage),
                    author=m.author,
                    source=url,
                    sourceKind="git",
                    isProvider=bool(m.provider),
                    providerType=(m.provider.type if m.provider else ""),
                    providerCapabilities=(
                        list(m.provider.capabilities) if m.provider else []
                    ),
                    tags=list(m.tags),
                    quality=(m.quality.to_dict() if m.quality else {}),
                    pointer=f"{url}#{entry.name}",
                    permissions=_perms,
                    crons=_crons,
                    hasUI=bool(m.ui.pages),
                    uiComponents=m.ui.components,
                    coreCompatibility=m.core_compatibility().to_dict(),
                )
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.debug(
            "git scan: error scanning %s",
            url,
            exc_info=True,
        )
        entries = []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _git_scan_cache[url] = (now, entries)
    return entries


def _scan_git_sources(*, now: float) -> list[CatalogEntry]:
    """Scan all configured git sources that lack a registry index, returning
    discovered multi-app subdirectory entries. Sources WITH a registry index
    are skipped (already handled by ``_scan_registries``).

    Enumeration only — :func:`resolve_catalog_entries` owns install-state and
    name-collision filtering."""
    out: list[CatalogEntry] = []
    for url in list_git_sources():
        out.extend(_scan_git_source(url, now=now))
    return out


def _sources_path() -> Path:
    return config_dir() / "apps" / _SOURCES_FILENAME


def _git_source_key(url: str) -> str:
    """The identity of a git source for de-duplication.

    One repository typed two ways is ONE source: GitHub serves the published apps repo
    at both ``…/GideonApps`` and ``…/GideonApps.git``, so comparing raw
    strings lets a user "add" a repo that already ships as a default and pay a second
    full shallow clone per catalog refresh for zero extra apps.

    A comparison key ONLY — the original string is what gets cloned, because the suffix
    is load-bearing for some remotes (a bare repo at ``file:///…/apps.git`` does not
    exist without it). Case is preserved: some hosts serve case-sensitive paths."""
    return url.strip().rstrip("/").removesuffix(".git")


def _source_flag(name: str, *, fallback: bool) -> bool:
    from gideon.core.config.loader import AppConfig

    try:
        return bool(getattr(AppConfig.load().apps, name))
    except Exception:
        logger.warning("Could not read apps.%s; using %s", name, fallback)
        return fallback


def bundled_source_enabled() -> bool:
    """Whether operator-configured catalog defaults participate in reads."""
    return _source_flag("bundled_source_enabled", fallback=True)


def _ordered_sources(values: Any, *, git: bool = False) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str) or not (source := value.strip()):
            continue
        result.setdefault(_git_source_key(source) if git else source, source)
    return list(result.values())


def _configured_git_sources(variable: str, fallback: tuple[str, ...]) -> list[str]:
    raw = _os.environ.get(variable)
    if raw is None:
        candidates: Any = fallback
    elif variable == _REGISTRY_SOURCE_ENV:
        candidates = [raw]
    elif raw.lstrip().startswith(("[", "{", '"')):
        try:
            candidates = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed JSON in %s", variable)
            return []
        if not isinstance(candidates, list):
            logger.warning(
                "%s must contain a JSON list or comma-separated URLs", variable
            )
            return []
    else:
        candidates = raw.split(",")
    accepted: list[str] = []
    for position, candidate in enumerate(_ordered_sources(candidates, git=True)):
        try:
            accepted.append(_validate_git_source(candidate))
        except ValueError:
            logger.warning("Ignoring invalid source %d in %s", position + 1, variable)
    return accepted


def configured_catalog_sources() -> list[str]:
    """Read explicit operator URLs on every call; absence means no remote defaults."""
    return _configured_git_sources(_CATALOG_SOURCES_ENV, _DEFAULT_GIT_SOURCES)


def configured_registry_source() -> str:
    sources = _configured_git_sources(_REGISTRY_SOURCE_ENV, (_REGISTRY_GIT_SOURCE,))
    return sources[0] if sources else ""


def _active_catalog_sources() -> list[str]:
    return configured_catalog_sources() if bundled_source_enabled() else []


def list_git_sources() -> list[str]:
    """Configured catalog defaults precede persisted rows, with stable repository identity."""
    return _ordered_sources(
        [*_active_catalog_sources(), *_read_sources()["git"]], git=True
    )


def network_source_hosts() -> list[str]:
    """The remote HOSTS a Store read contacts, de-duped, in listed order.

    The disclosure surface for finding 1: the Store lists a shipped git source before the
    user has configured anything, so the page that triggers the fetch can NAME where it
    reaches. A ``file://`` source, an unparseable URL, or an all-local configuration
    contributes nothing — so an empty list means opening the Store touches no network,
    and the UI can say so honestly rather than always showing a warning."""
    from urllib.parse import urlsplit

    seen: set[str] = set()
    out: list[str] = []
    for url in list_git_sources():
        host = ""
        if match := _SCP_LIKE_REMOTE_RE.match(url):
            host = match.group(0).split("@", 1)[1].split(":", 1)[0]
        else:
            parts = urlsplit(url)
            if parts.scheme != "file":
                host = parts.hostname or ""
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out


def _read_sources() -> dict[str, list[str]]:
    """Normalize current and legacy source files without creating state during reads."""
    try:
        payload = json.loads(_sources_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "git": _ordered_sources(
            payload.get("git", payload.get("sources", [])), git=True
        ),
        "local": _ordered_sources(payload.get("local", [])),
        "seeded": _ordered_sources(payload.get("seeded", [])),
    }


def _write_sources(sources: dict[str, list[str]]) -> None:
    destination = _sources_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: sources.get(key, []) for key in ("git", "local", "seeded")}
    atomic_write(destination, json.dumps(payload, indent=2) + "\n")


def _read_user_sources() -> list[str]:
    """Legacy shim: the user GIT sources only (used by list_git_sources)."""
    return _read_sources()["git"]


_GIT_SOURCE_SCHEMES: frozenset[str] = frozenset({"https", "http", "ssh", "git", "file"})

_SCP_LIKE_REMOTE_RE = re.compile(r"^[A-Za-z0-9._~-]+@[A-Za-z0-9.-]+:(?!//).+")


def _validate_git_source(url: str) -> str:
    """Return *url* unchanged, or raise `ValueError` naming what is wrong with it.

    Two independent problems this closes, in one function because they are one line of defence.

    🔴 **A credential in the URL (#406).** `https://user:token@host/repo.git` was accepted and then
    written verbatim into `app-sources.json` AND into the HMAC-chained append-only audit log, where
    it cannot be cleaned up afterwards. The precedent is already shipped at
    `cli_app_new.py:_validated_template_url`, which refuses userinfo and allowlists the scheme for
    exactly this reason; this is the same three lines on the source path.

    The rule distinguishes a USERNAME from a SECRET, because `ssh://git@github.com/owner/repo.git`
    is an ordinary remote and refusing all userinfo would break it:
      * userinfo containing `:` is a password, and is refused for every scheme;
      * for `http`/`https`, ANY userinfo is refused — nobody puts a bare username in an https git
        remote except to carry a token (`https://<PAT>@github.com/…` is the documented GitHub form).

    🔴 **Any string at all was accepted (#280).** `not-a-git-url` persisted silently and then
    rendered in the Store as its own source group with no apps under it and no error, which reads as
    a working source that happens to be empty. It is refused at the point of entry now.

    NOT covered here, deliberately: a syntactically valid but UNREACHABLE source still renders as an
    empty group rather than an errored one. That is #280's other half and it is a Store rendering
    question, not a validation one.
    """
    from urllib.parse import urlsplit

    u = url.strip()
    if not u:
        raise ValueError("empty source URL")

    if _SCP_LIKE_REMOTE_RE.match(u):
        return u

    parts = urlsplit(u)
    if parts.scheme not in _GIT_SOURCE_SCHEMES:
        raise ValueError(
            f"not a git remote: scheme {parts.scheme or '(none)'!r} is not one of "
            f"{', '.join(sorted(_GIT_SOURCE_SCHEMES))} — expected something like "
            "https://github.com/owner/repo.git or git@github.com:owner/repo.git"
        )
    if parts.scheme != "file" and not parts.hostname:
        raise ValueError("not a git remote: the URL names no host")
    if parts.password:
        raise ValueError(
            "the source URL carries a password in its userinfo — remove it and use a credential "
            "helper or an ssh key. A URL stored here is written to the audit log, which is "
            "append-only and cannot be cleaned up afterwards."
        )
    if parts.username and parts.scheme in ("http", "https"):
        raise ValueError(
            "the source URL carries credentials in its userinfo — an https git remote needs no "
            "username, and a token placed there would be persisted and audit-logged. Use a "
            "credential helper, or an ssh remote."
        )
    return u


def add_git_source(url: str) -> list[str]:
    """Persist a validated user remote unless an active source already names that repo."""
    source = _validate_git_source(url)
    state = _read_sources()
    existing = {
        _git_source_key(value) for value in [*_active_catalog_sources(), *state["git"]]
    }
    if _git_source_key(source) not in existing:
        state["git"].append(source)
        _write_sources(state)
    return state["git"]


def remove_git_source(url: str) -> list[str]:
    """Remove persisted rows while retaining seed history and configured catalog defaults."""
    key = _git_source_key(url)
    state = _read_sources()
    state["git"] = [source for source in state["git"] if _git_source_key(source) != key]
    _write_sources(state)
    return state["git"]


def seed_default_git_sources() -> list[str]:
    """Persist an explicitly configured registry once; removal survives all later starts."""
    source = configured_registry_source()
    if not source or not _source_flag("registry_source_enabled", fallback=False):
        return []
    state = _read_sources()
    if _SEEDED_REGISTRY_KEY in state["seeded"]:
        return []
    existing = {
        _git_source_key(value) for value in [*_active_catalog_sources(), *state["git"]]
    }
    added = [] if _git_source_key(source) in existing else [source]
    state["git"].extend(added)
    state["seeded"].append(_SEEDED_REGISTRY_KEY)
    _write_sources(state)
    return added


def default_git_sources() -> list[str]:
    defaults = {_git_source_key(source) for source in configured_catalog_sources()}
    registry = configured_registry_source()
    if registry:
        defaults.add(_git_source_key(registry))
    return [
        source for source in list_git_sources() if _git_source_key(source) in defaults
    ]


def builtin_git_sources() -> list[str]:
    defaults = {_git_source_key(source) for source in _active_catalog_sources()}
    return [
        source for source in list_git_sources() if _git_source_key(source) in defaults
    ]


def _default_local_sources() -> list[str]:
    """Always-present, read-only local sources: the FIRST-PARTY apps dir.

    Resolution: if the env override is SET, it wins exclusively — a valid dir is the
    source, any other value (incl. a nonexistent path) DISABLES the default (this is
    how tests neutralize it). If the env is unset, fall back to the resolved workspace
    ``apps/`` (dev); empty if that doesn't exist (a shipped install without the tree).
    """
    if _FIRST_PARTY_ENV in _os.environ:
        env = _os.environ[_FIRST_PARTY_ENV].strip()
        p = Path(env).expanduser() if env else None
        return [str(p)] if (p and p.is_dir()) else []
    fp = _first_party_source()
    return [str(fp)] if fp else []


def first_party_sources() -> set[str]:
    """Paths that are first-party defaults — always present, NOT user-removable."""
    return set(_default_local_sources())


def list_local_sources() -> list[str]:
    """Collect first-party directories before persisted local sources, in stable order."""
    return _ordered_sources([*_default_local_sources(), *_read_sources()["local"]])


def add_local_source(path: str) -> list[str]:
    """Add a local app-source directory; returns the updated local list. Rejects a
    non-directory (a source must be a dir of app subdirs, not a single app or a file).
    """
    from pathlib import Path

    p = path.strip()
    if not p:
        raise ValueError("empty source path")
    if not Path(p).expanduser().is_dir():
        raise ValueError(f"not a directory: {p}")
    src = _read_sources()
    if p not in src["local"]:
        src["local"].append(p)
        _write_sources(src)
    return src["local"]


def remove_local_source(path: str) -> list[str]:
    """Remove a USER-added local app-source directory. A first-party default source
    is read-only (always present) and cannot be removed."""
    p = path.strip()
    if p in first_party_sources():
        raise ValueError("cannot remove a first-party (built-in) app source")
    src = _read_sources()
    src["local"] = [x for x in src["local"] if x != p]
    _write_sources(src)
    return src["local"]


def _consent_timezone() -> str:
    """The configured timezone a manifest cron's clock times should be read in — the same
    one the Schedule page renders. Empty on any config trouble, which only costs the
    cadence text its timezone suffix."""
    try:
        from gideon.core.config.loader import AppConfig

        return str(AppConfig.load().timezone or "")
    except Exception:
        return ""


def _humanized_cadence(expr: str, tz_name: str) -> str:
    """The human reading of a 5-field cron expression, for install consent.

    🔴 DELEGATES to the shipped ``schedule.format_schedule`` for the same reason
    ``triggers/schedule_view.describe_cadence`` does: a second formatter drifts from the
    one the rest of the UI reads, and a consent screen that spells a schedule differently
    from the Schedule page hands the user a third fact to reconcile. ``cron-descriptor``
    is already a hard dependency, so this is reuse, not a new capability.

    Empty when the expression cannot be described (``format_schedule`` hands the raw
    expression back), so the caller can fall back to showing the expression itself rather
    than inventing a cadence it does not know."""
    if not expr.strip():
        return ""
    try:
        from gideon.automation.schedule import ScheduleDefinition, format_schedule

        text = format_schedule(
            ScheduleDefinition(kind="cron", cron_expr=expr), tz_name=tz_name
        )
    except Exception:
        logger.debug("could not describe cron cadence %r", expr, exc_info=True)
        return ""
    return "" if text.strip() == expr.strip() else text.strip()


def _manifest_consent(m: AppManifest) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(permissions, crons) an app declares — the P29 install-consent surface, extracted
    from a scanned manifest so the Store can show what the app will be granted + what
    recurring jobs it will run BEFORE install. Best-effort; empty on any shape surprise.
    """
    try:
        perms = m.permissions.to_dict() if m.permissions else {}
    except Exception:
        perms = {}
    crons: list[dict[str, Any]] = []
    try:
        tz_name = ""
        if any(getattr(c, "cron_expr", "") for c in m.crons or []):
            tz_name = _consent_timezone()
        for c in m.crons or []:
            cd = c.to_dict() if hasattr(c, "to_dict") else {}
            crons.append(
                {
                    "name": cd.get("name", ""),
                    "every": cd.get("every", 0),
                    "cron_expr": cd.get("cron_expr", ""),
                    "cadence": _humanized_cadence(
                        str(cd.get("cron_expr", "")), tz_name
                    ),
                    "agent": cd.get("agent", ""),
                    "message": cd.get("message", ""),
                }
            )
    except Exception:
        crons = []
    return perms, crons


def _scan_local_sources() -> list[CatalogEntry]:
    """Scan each configured local source dir for immediate subdirs with a valid
    ``app.json``, surfacing them as one-click-installable catalog entries (mirrors
    ``available_bundled``'s manifest read).

    Enumeration only — :func:`resolve_catalog_entries` owns install-state and
    name-collision filtering. Two local roots carrying the same app name are resolved
    there too: ``first-party`` outranks a user-added ``local`` dir by rule, where it
    used to depend on this function's iteration order."""
    from pathlib import Path

    out: list[CatalogEntry] = []
    for root in list_local_sources():
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            manifest_file = entry / "app.json" if entry.is_dir() else None
            if not manifest_file or not manifest_file.is_file():
                continue
            try:
                m = AppManifest.from_json_file(manifest_file)
            except Exception:
                logger.warning("catalog: bad local manifest %s", entry, exc_info=True)
                continue
            kind = "first-party" if root in first_party_sources() else "local"
            _perms, _crons = _manifest_consent(m)
            out.append(
                CatalogEntry(
                    name=m.name,
                    displayName=m.displayName or m.name,
                    description=m.description,
                    version=m.version,
                    icon=m.icon,
                    heroUrl=resolve_hero_url(entry, m.heroImage),
                    author=m.author,
                    source=str(entry),
                    sourceKind=kind,
                    isProvider=bool(m.provider),
                    providerType=(m.provider.type if m.provider else ""),
                    providerCapabilities=(
                        list(m.provider.capabilities) if m.provider else []
                    ),
                    tags=list(m.tags),
                    quality=(m.quality.to_dict() if m.quality else {}),
                    permissions=_perms,
                    crons=_crons,
                    hasUI=bool(m.ui.pages),
                    uiComponents=m.ui.components,
                    coreCompatibility=m.core_compatibility().to_dict(),
                )
            )
    return out


def _bundled_dir() -> Path:
    from gideon.extensions.providers.loader import BUNDLED_DIR

    return BUNDLED_DIR


def _installed_names() -> set[str]:
    from gideon.extensions.apps.manager import list_apps

    return {a.get("name", "") for a in list_apps()}


def installed_logger_roots() -> tuple[str, ...]:
    """Top-level logger namespaces that ENABLED installed apps log under (their own
    root, not ``gideon``) — read from each app's manifest ``loggerRoots``.

    This is the runtime replacement for the hard-coded ``constants.APP_LOGGER_ROOTS``:
    the set of app log roots is derived from what's actually installed + enabled, so
    log-level plumbing (CLI boot + the /api/logs/level endpoint) applies the level +
    file handler to each app's logger too — no source edit when an app ships a new root.

    Manifest-only (reads ``list_apps()``'s scanned manifest dict — no app import/exec),
    enabled apps only, de-duped preserving first-seen order. Returns ``()`` when no apps
    dir exists yet (a fresh install), so callers degrade to just ``gideon``."""
    from gideon.extensions.apps.manager import apps_dir, list_apps

    if not apps_dir().is_dir():
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for app in list_apps():
        if not app.get("enabled", True):
            continue
        manifest = app.get("manifest") or {}
        for root in manifest.get("loggerRoots") or []:
            r = str(root).strip()
            if r and r not in seen:
                seen.add(r)
                out.append(r)
    return tuple(out)


def available_bundled() -> list[CatalogEntry]:
    """Native manifests not currently in the Library — installable from
    their on-disk path.

    Native apps are seeded ENABLED at first run and are locked-on (can't be
    uninstalled), so in normal operation none are ever "available but absent" and
    this returns empty. It stays as a defensive self-heal: if a native app's
    installed record is somehow missing (a corrupted state), it resurfaces here so
    the seed path (or a manual re-add) can restore it — native apps are mandatory.

    Enumeration only: :func:`resolve_catalog_entries` drops the ones already in the
    Library, so the "available but absent" filter lives in one place with every other
    source's."""
    bundled = _bundled_dir()
    if not bundled.is_dir():
        return []
    out: list[CatalogEntry] = []
    for entry in sorted(bundled.iterdir()):
        manifest_file = entry / "app.json" if entry.is_dir() else None
        if not manifest_file or not manifest_file.is_file():
            continue
        try:
            m = AppManifest.from_json_file(manifest_file)
        except Exception:
            logger.warning("catalog: bad native manifest %s", entry.name, exc_info=True)
            continue
        if not m.native:
            continue
        _perms, _crons = _manifest_consent(m)
        out.append(
            CatalogEntry(
                name=m.name,
                displayName=m.displayName or m.name,
                description=m.description,
                version=m.version,
                icon=m.icon,
                heroUrl=resolve_hero_url(entry, m.heroImage),
                author=m.author,
                source=str(entry),
                sourceKind="native",
                isProvider=bool(m.provider),
                providerType=(m.provider.type if m.provider else ""),
                providerCapabilities=(
                    list(m.provider.capabilities) if m.provider else []
                ),
                tags=list(m.tags),
                quality=(m.quality.to_dict() if m.quality else {}),
                permissions=_perms,
                crons=_crons,
                hasUI=bool(m.ui.pages),
                uiComponents=m.ui.components,
                coreCompatibility=m.core_compatibility().to_dict(),
            )
        )
    return out


_APP_UPDATES_ENTITY = "app_updates"


def _latest_local_versions() -> dict[str, str]:
    """``{app_name: highest version}`` discoverable across the configured LOCAL sources.

    Unlike ``_scan_local_sources`` (which OMITS installed apps, since it feeds the Store's
    "available to install" list), this includes every app a local source declares — because
    the whole point here is to compare an INSTALLED app against the newer copy its source now
    carries. On-disk manifest reads only; a bad manifest is skipped, never fatal."""
    from pathlib import Path

    latest: dict[str, str] = {}
    for root in list_local_sources():
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            manifest_file = entry / "app.json" if entry.is_dir() else None
            if not manifest_file or not manifest_file.is_file():
                continue
            try:
                m = AppManifest.from_json_file(manifest_file)
            except Exception:
                logger.debug(
                    "update check: bad local manifest %s", entry, exc_info=True
                )
                continue
            if not m.name or not m.version:
                continue
            current = latest.get(m.name)
            if current is None or version_tuple(m.version) > version_tuple(current):
                latest[m.name] = m.version
    return latest


def updates_available() -> list[dict[str, Any]]:
    """Installed apps whose local source now offers a NEWER version.

    Compares each installed app's on-disk version against the highest version the configured
    local sources declare for that app, using the single app-version comparator
    (``manifest.version_tuple``). Returns one entry per out-of-date app::

        {"name", "displayName", "installedVersion", "latestVersion", "source"}

    Pure + cheap (on-disk reads, no network, no side effects) — safe to call on the
    ``/api/apps`` read path. An app with no newer version, or with no source-side manifest,
    is simply absent."""
    from gideon.extensions.apps.manager import list_apps

    latest = _latest_local_versions()
    out: list[dict[str, Any]] = []
    for app in list_apps():
        name = app.get("name", "")
        installed_version = str(app.get("version", ""))
        latest_version = latest.get(name)
        if not name or not latest_version:
            continue
        if version_tuple(latest_version) > version_tuple(installed_version):
            manifest = app.get("manifest") or {}
            out.append(
                {
                    "name": name,
                    "displayName": manifest.get("displayName") or name,
                    "installedVersion": installed_version,
                    "latestVersion": latest_version,
                    "source": app.get("source", ""),
                }
            )
    return out


def _load_notified() -> dict[str, str]:
    """The per-app high-water mark of the latest version we've already notified about
    (``entity_settings/app_updates.json`` → ``{"notified": {name: version}}``). Tolerant:
    an unreadable/corrupt file means we've announced nothing (fail open — a duplicate
    notification is a lesser evil than a silently-swallowed one)."""
    try:
        from gideon.extensions.providers.entity_routes import _load_entity_settings

        data = _load_entity_settings(_APP_UPDATES_ENTITY)
        notified = data.get("notified") if isinstance(data, dict) else None
        return (
            {str(k): str(v) for k, v in notified.items()}
            if isinstance(notified, dict)
            else {}
        )
    except Exception:
        logger.debug("app-update notified state unreadable", exc_info=True)
        return {}


def _save_notified(notified: dict[str, str]) -> None:
    from gideon.extensions.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_APP_UPDATES_ENTITY, {"notified": notified})


def surface_app_updates(state: Any) -> list[dict[str, Any]]:
    """Compute available updates AND emit ONE notification per newly-available version.

    The dedup contract (APE-7): a notification fires the first time an app's source offers a
    given ``latestVersion``, and never again for that version — even after the inbox row is
    dismissed — because the high-water mark is persisted OUTSIDE the inbox
    (``entity_settings/app_updates.json``), keyed by ``name``. Only a version strictly newer
    than the one last announced re-fires. Emission routes through the registered
    ``apps/update`` attention kind via ``emit_attention_item`` (dual-honesty: even if the
    kind's delivery rule is muted, the inbox row still lands and ``state.notify`` still runs —
    the rules layer, not this code, decides whether to toast).

    Returns the same list as :func:`updates_available` so a caller on the read path can attach
    it to its response without recomputing. Best-effort: a persistence/emit error is logged and
    never breaks the read path."""
    updates = updates_available()
    if state is None:
        return updates
    try:
        notified = _load_notified()
    except Exception:
        notified = {}
    changed = False
    for u in updates:
        name = u["name"]
        latest_version = u["latestVersion"]
        already = notified.get(name, "")
        if version_tuple(latest_version) > version_tuple(already):
            _emit_app_update(state, u)
            notified[name] = latest_version
            changed = True
    if changed:
        try:
            _save_notified(notified)
        except Exception:
            logger.warning("could not persist app-update notified state", exc_info=True)
    return updates


def _emit_app_update(state: Any, update: dict[str, Any]) -> None:
    from gideon.integrations.inbox import emit_attention_item

    name = update["name"]
    display = update.get("displayName") or name
    latest_version = update["latestVersion"]
    installed_version = update.get("installedVersion", "")
    try:
        emit_attention_item(
            state,
            source="apps",
            kind="update",
            title=f"Update available for {display}",
            body=f"Version {latest_version} is available (you have {installed_version}).",
            refs={"app": name, "latest_version": latest_version},
            dedup_key=f"app_update:{name}:{latest_version}",
        )
    except Exception:
        logger.warning("app-update notification failed for %s", name, exc_info=True)


def available_catalog() -> dict[str, Any]:
    """The full Store catalog: available bundled apps + configured git sources +
    local sources (with their scanned, one-click-installable apps).

    Git sources are returned as-is (URL list) — resolving each to a manifest means
    cloning, which we defer to install time (behind the scanner gate). Local sources
    ARE scanned (cheap on-disk manifest read) so their apps surface as install cards,
    like the bundled section. The UI lists sources as 'add by source' + offers direct
    install (by URL for git, by discovered card for local).

    Every app list below is filtered through :func:`resolve_catalog_entries`, so the
    payload carries AT MOST ONE entry per app name across all four lists. That is the
    contract the Store's card, its detail panel, its consent modal and the onboarding
    step all depend on: with no name in two lists, no consumer can resolve a collision
    differently from another (#2528).
    """
    with _catalog_build_lock:
        return _build_available_catalog()


def _build_available_catalog() -> dict[str, Any]:
    import time

    now = time.time()
    bundled_entries = available_bundled()
    local_entries = _scan_local_sources()
    registry_entries = _scan_registries(now=now)
    git_entries = _scan_git_sources(now=now)
    winners = resolve_catalog_entries(
        [*bundled_entries, *local_entries, *registry_entries, *git_entries]
    )
    winner_for = {e.name: e for e in winners}

    def _kept(entries: list[CatalogEntry]) -> list[dict[str, Any]]:
        return [e.to_dict() for e in entries if winner_for.get(e.name) is e]

    return {
        "bundled": _kept(bundled_entries),
        "gitSources": list_git_sources(),
        "defaultGitSources": default_git_sources(),
        "builtinGitSources": builtin_git_sources(),
        "localSources": list_local_sources(),
        "firstPartySources": sorted(first_party_sources()),
        "localApps": _kept(local_entries),
        "remoteApps": _kept(registry_entries),
        "gitApps": _kept(git_entries),
        "networkSources": network_source_hosts(),
    }
