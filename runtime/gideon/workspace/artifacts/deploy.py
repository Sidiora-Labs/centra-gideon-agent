"""Local static artifact deploy — the webapp serve registry and path spine (PEP-8).

Deploying an artifact makes its own bytes reachable at a stable in-gateway URL
(``/artifacts/serve/<slug>/``) so an html/widget artifact can be opened and driven
as a real page instead of only rendered inside a chat bubble. Local-only: public
exposure is explicitly out of scope, so the route sits behind the same session auth
as every other gateway path (``/artifacts/`` is in no auth-bypass prefix).

Three properties are load-bearing here, because this route serves
**model- or user-authored HTML** rather than shipped assets:

*Containment.* A request path is resolved and asserted to live under the artifact's
own files root — never string-matched against ``..``. Marker rejection
(``..``/absolute/backslash/percent-encoded traversal) is a cheap first gate; the
resolve-and-contain assertion is the one that actually holds, and a symlink is
refused outright so the served set can never point outside the root.

*The CSP fence.* The served document is fenced like a widget iframe:
``connect-src 'none'`` means the page cannot call the gateway ``/api`` at all,
and ``form-action``/``base-uri``/``object-src`` are shut so it cannot exfiltrate
by navigation either. It is the response header (not a ``<meta>`` the document
could omit) that carries the fence, because the document is untrusted input.

*Teardown removes the route.* aiohttp freezes its router at startup, so the
"route" a user can tear down is this registry: the handler serves nothing for a
slug that is not deployed, and deleting an artifact tears its deployment down.
An artifact deleted but still reachable is precisely the defect the teardown
clause exists to prevent.

Persistence lives at ``<home>/artifacts/deployments.json`` — inside the artifacts
tree, so the existing ``artifacts`` durability inventory entry already covers it
and the provider's directory-only ``list`` ignores it (same bargain as
``folders.json``).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.security.security import is_sensitive_path
from gideon.workspace.artifacts.models import is_valid_slug


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

SERVE_URL_PREFIX = "/artifacts/serve"

DEFAULT_ENTRY = "index.html"

DEPLOYABLE_KINDS = frozenset({"widget", "html", "react"})

MAX_DEPLOYMENTS = 200

FILES_SUBDIR = "webapp"

ARTIFACT_SERVE_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
    "https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'self'"
)

SERVE_HEADERS: dict[str, str] = {
    "Content-Security-Policy": ARTIFACT_SERVE_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArtifactDeployment:
    """One deployed artifact: the slug, its entry document, and when it went up."""

    slug: str
    entry: str = DEFAULT_ENTRY
    created_at: str = ""

    @property
    def url(self) -> str:
        return f"{SERVE_URL_PREFIX}/{self.slug}/"

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "entry": self.entry,
            "created_at": self.created_at,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ArtifactDeployment":
        return cls(
            slug=str(d.get("slug", "")),
            entry=str(d.get("entry", "") or DEFAULT_ENTRY),
            created_at=str(d.get("created_at", "")),
        )


_Deployments = list[ArtifactDeployment]


class ArtifactDeployStore:
    """Flat-JSON registry of deployed artifacts.

    Reads re-load from disk on every call (no cache), so a store constructed fresh
    against the same tree sees what another instance wrote — the same reload
    contract the folder store keeps.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root else (config_dir() / "artifacts")
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._root / "deployments.json"

    def files_root(self, slug: str) -> Path:
        """The static-file root for *slug*. Raises on an invalid slug.

        This is the containment boundary: nothing outside this directory may be
        served for *slug*, and the assertion is made against its resolved form.
        """
        if not is_valid_slug(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        return self._root / slug / FILES_SUBDIR

    def _load(self) -> _Deployments:
        path = self.path
        if is_sensitive_path(str(path)) or not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning("corrupt artifact deployments file: %s", path)
            return []
        if not isinstance(raw, list):
            return []
        out: _Deployments = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            dep = ArtifactDeployment.from_dict(entry)
            if dep.slug and is_valid_slug(dep.slug):
                out.append(dep)
        return out

    def _save(self, deployments: _Deployments) -> None:
        root = self._root
        if is_sensitive_path(str(root)):
            raise PermissionError("artifact root resolves to a sensitive path")
        root.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self.path, json.dumps([d.to_dict() for d in deployments], indent=2)
        )

    def list(self) -> _Deployments:
        """Every deployment, newest first (the deployed-app listing's order)."""
        with self._lock:
            deployments = self._load()
        deployments.sort(key=lambda d: d.created_at, reverse=True)
        return deployments

    def get(self, slug: str) -> ArtifactDeployment | None:
        if not slug or not is_valid_slug(slug):
            return None
        with self._lock:
            return next((d for d in self._load() if d.slug == slug), None)

    def is_deployed(self, slug: str) -> bool:
        return self.get(slug) is not None

    def deploy(self, slug: str, *, entry: str = "") -> ArtifactDeployment:
        """Register *slug* as deployed (idempotent — re-deploying refreshes entry)."""
        if not is_valid_slug(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        clean_entry = (entry or "").strip() or DEFAULT_ENTRY
        if rejects_path(clean_entry):
            raise ValueError(f"invalid entry: {entry!r}")
        with self._lock:
            deployments = self._load()
            existing = next((d for d in deployments if d.slug == slug), None)
            if existing is not None:
                existing.entry = clean_entry
                self._save(deployments)
                return existing
            if len(deployments) >= MAX_DEPLOYMENTS:
                raise ValueError(f"too many deployed artifacts (max {MAX_DEPLOYMENTS})")
            dep = ArtifactDeployment(slug=slug, entry=clean_entry, created_at=_now())
            deployments.append(dep)
            self._save(deployments)
            return dep

    def teardown(self, slug: str) -> bool:
        """Remove *slug*'s deployment. Returns whether anything was removed.

        Content is untouched: teardown un-publishes, it never destroys the artifact.
        """
        if not slug:
            return False
        with self._lock:
            deployments = self._load()
            remaining = [d for d in deployments if d.slug != slug]
            if len(remaining) == len(deployments):
                return False
            self._save(remaining)
            return True


_REJECT_MARKERS = ("..", "\\", "\x00", "//", ":")


def rejects_path(rel_path: str) -> bool:
    """Whether *rel_path* is refused before any filesystem work.

    Cheap first gate only — containment below is the assertion that holds. Rejects
    absolute paths, home expansion, backslash and NUL, dot-dot in either raw or
    percent-decoded form, and ``:`` (a Windows drive or a URL scheme).
    """
    if not rel_path:
        return True
    candidates = [rel_path]
    once = unquote(rel_path)
    if once != rel_path:
        candidates.append(once)
    for candidate in candidates:
        if candidate.startswith("/") or candidate.startswith("~"):
            return True
        if any(marker in candidate for marker in _REJECT_MARKERS):
            return True
    return False


def resolve_served_file(files_root: Path, rel_path: str) -> Path | None:
    """Resolve *rel_path* under *files_root*, or ``None`` if it must be refused.

    Refuses: a rejected path shape (:func:`rejects_path`), anything whose resolved
    location is not contained by the resolved root, any symlinked component, and
    anything that is not a regular file (so a directory never yields an index).
    """
    if rejects_path(rel_path):
        return None
    try:
        root = files_root.resolve()
        candidate = root / rel_path
        real = candidate.resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if real != root and root not in real.parents:
        return None
    probe = candidate
    while True:
        try:
            if probe.is_symlink():
                return None
        except OSError:
            return None
        if probe == root or root not in probe.parents:
            break
        probe = probe.parent
    try:
        if not real.is_file():
            return None
    except OSError:
        return None
    return real


def content_type_for(path: Path) -> str:
    """MIME type for a served file. ``.html`` is pinned rather than guessed."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return "text/html"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
