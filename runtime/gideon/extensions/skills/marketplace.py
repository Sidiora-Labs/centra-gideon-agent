"""Skills marketplace — abstract base, SkillsRegistry, and local skill discovery.

The agentskills.io format (https://agentskills.io) is the standard:
  - A skill is a directory containing a SKILL.md file with YAML frontmatter.
  - Frontmatter fields: name, description, license, compatibility, metadata, allowed-tools.
  - The body is Markdown loaded on demand by the LLM.

Discovery paths (loaded by ``_all_skill_paths()`` in ``agent.py``):
  - ``~/.agents/skills/``        — agentskills.io cross-client standard
  - ``GIDEON_PROJECT_DIR/skills/``  — project-level
  - ``~/.gideon/skills/``      — user-created

``SkillsRegistry`` holds named ``SkillsMarketplace`` implementations.
Additional marketplaces (skills.sh, custom registries) register via
``get_default_skills_registry().register(name, marketplace)``.
"""

import builtins
import hashlib
import json
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.core.record_ids import record_path

logger = logging.getLogger(__name__)

_SKILL_FILENAME = "SKILL.md"
LOCK_FILENAME = ".gideon-lock.json"


class _SkillDiscoveryPaths:
    """Resolve skill discovery roots when a caller iterates the registry."""

    def __iter__(self):
        return iter(_skill_discovery_paths())


_DEFAULT_SKILL_DISCOVERY_PATHS = _SkillDiscoveryPaths()
SKILL_DISCOVERY_PATHS = _DEFAULT_SKILL_DISCOVERY_PATHS


def _skill_discovery_paths() -> list[Path]:
    # Tests and callers historically replace this exported list to isolate
    # discovery. Preserve that override while resolving defaults at call time.
    if SKILL_DISCOVERY_PATHS is not _DEFAULT_SKILL_DISCOVERY_PATHS:
        return list(SKILL_DISCOVERY_PATHS)

    from gideon.extensions.skills.loader import skills_dir

    return [skills_dir()]


def default_skills_install_path() -> Path:
    """Return the active Gideon home skills directory at call time."""
    from gideon.extensions.skills.loader import skills_dir

    return skills_dir()


@dataclass
class SkillEntry:
    """Metadata for a skill returned from a marketplace search."""

    id: str
    name: str
    description: str
    source: str
    url: str = ""
    installs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "url": self.url,
            "installs": self.installs,
        }


@dataclass
class SkillDetail:
    """Full skill contents returned from marketplace fetch.

    Each ``files`` entry is ``{path, contents}`` for a text file or ``{path, data}``
    (raw ``bytes``) for a binary — the whole tree is carried so nothing is dropped
    before the scan/commit/lock. Use :func:`read_skill_file_entry` to build entries."""

    id: str
    name: str
    files: list[dict[str, Any]] = field(default_factory=list)
    audit_status: str = "unknown"

    def skill_md(self) -> str | None:
        """Return the SKILL.md content, or None if not present."""
        for f in self.files:
            if f.get("path", "").endswith("SKILL.md"):
                return f.get("contents", "")
        return None


class SkillsMarketplace(ABC):
    """Abstract skills marketplace."""

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[SkillEntry]:
        """Search the marketplace for skills matching *query*."""

    @abstractmethod
    def fetch(self, skill_id: str) -> SkillDetail:
        """Fetch full skill detail (including SKILL.md contents) for *skill_id*.

        A marketplace is a read-only SOURCE: it only searches and fetches. Installing
        is not its job — :meth:`SkillsRegistry.install_guarded` stages the fetched
        payload to quarantine, scans it at this marketplace's trust tier, and commits
        the exact scanned bytes via the shared ``install_skill_files`` writer. That one
        chokepoint is the only path that writes to the live skills tree, so a fetch
        never has to be trusted to write."""

    @property
    def marketplace_type(self) -> str:
        return "unknown"

    @property
    def trust_tier(self) -> str:
        """Provenance tier that modulates the scan verdict (S2). Bundled/native content
        is trusted; an arbitrary community registry (skills.sh) gets the full gate.
        Returns a :class:`~gideon.security.supply_chain.TrustTier` value string."""
        return "community"


@dataclass
class InstallResult:
    """A successful guarded install: where it landed + the scan evidence surfaced."""

    path: Path
    report: "Any"
    tier: "Any"


class SkillNotFoundError(KeyError):
    """A marketplace was asked for a skill id it does not have.

    Typed so HTTP handlers can map "no such skill" to 404 instead of the
    500 a bare ``RuntimeError`` produced. Also raised for a syntactically
    invalid id (path separators, ``..``, absolute paths) — to a caller those
    are indistinguishable from "not found", and saying more would leak
    which paths exist outside the marketplace root.
    """

    def __init__(self, skill_id: str) -> None:
        super().__init__(skill_id)
        self.skill_id = skill_id

    def __str__(self) -> str:
        return f"Skill not found: {self.skill_id!r}"


class SkillInstallRefused(Exception):
    """A guarded install was blocked by the supply-chain gate.

    ``dangerous`` distinguishes the non-overridable floor (high-confidence malice — no
    ``force`` installs it) from an overridable ``warning`` (a calculated risk the caller
    may re-attempt with ``force=True``). ``report`` carries the findings for the UX.
    """

    def __init__(self, report: "Any", *, dangerous: bool) -> None:
        self.report = report
        self.dangerous = dangerous
        cats = (
            ", ".join(sorted({f.rule for f in report.findings})) or "no specific rule"
        )
        verb = (
            "refused (dangerous, non-overridable)"
            if dangerous
            else "needs confirmation (warning)"
        )
        super().__init__(f"skill install {verb}: {cats}")


def read_skill_file_entry(path: Path, rel: str) -> "dict[str, Any]":
    """Read one skill file into a payload entry, preserving binary content.

    Text (UTF-8-decodable) files carry ``contents: str``; anything else carries
    ``data: bytes``. Binaries must NOT be dropped — an icon/asset that goes missing
    means an incomplete install AND a spurious S6 "added" finding on the untracked
    file. Both variants flow through staging, the scan, the commit, and the lock."""
    raw = path.read_bytes()
    try:
        return {"path": rel, "contents": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": rel, "data": raw}


def _entry_bytes(entry: "dict[str, Any]") -> bytes:
    """The raw bytes a file entry writes to disk — text ``contents`` UTF-8-encoded, or
    binary ``data`` verbatim. One definition shared by stage, commit, and lock so all
    three hash/write identical bytes (a fresh install verifies intact under S6)."""
    if "data" in entry:
        data = entry["data"]
        return data if isinstance(data, bytes) else str(data).encode("utf-8")
    return str(entry.get("contents", "")).encode("utf-8")


def _stage_files(files: "list[dict[str, Any]]", staged_skill: Path) -> None:
    """Write the fetched payload into a quarantine dir, path-safe, for scanning BEFORE
    it can touch the live skills tree. Rejects traversal (mirrors install_skill_files).
    """
    staged_skill.mkdir(parents=True, exist_ok=True)
    for entry in files:
        rel = entry.get("path", "")
        if ".." in rel or rel.startswith("/"):
            raise ValueError(f"Rejected unsafe file path: {rel!r}")
        out = staged_skill / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_entry_bytes(entry))


def file_digests(skill_dir: Path) -> dict[str, str]:
    """Each file in *skill_dir* and the sha256 of its bytes, by its path in the folder.

    The install record itself is left out: it describes the files, it is not one of them. A file
    that cannot be read is left out too, so a comparison with a record reads it as missing.
    """
    skill_dir = Path(skill_dir)
    out: dict[str, str] = {}
    for f in sorted(skill_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(skill_dir).as_posix()
        if rel == LOCK_FILENAME:
            continue
        try:
            out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def files_digest(sha256: Mapping[str, str]) -> str:
    """One digest for a set of files: sha256 over their sorted ``<path>\\0<sha256>`` lines.

    Two folders hold the same files exactly when their digests are equal, whatever their files'
    times say. The algorithm is a stable contract: :data:`shipped.EARLIER_VERSIONS` holds digests
    of versions that shipped before install records were kept.
    """
    lines = "".join(f"{rel}\0{sha256[rel]}\n" for rel in sorted(sha256))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InstallRecord:
    """What a skill folder's install record says was installed: its source, and each file."""

    source: str
    sha256: dict[str, str]

    @property
    def digest(self) -> str:
        return files_digest(self.sha256)


class DamagedInstallRecord(ValueError):
    """A skill's install record is there and cannot be read as one, so nothing can say what was
    installed in its folder."""


def install_record(skill_dir: Path) -> InstallRecord | None:
    """The install record in *skill_dir*, or ``None`` when it has none (nothing installed it).

    Raises :class:`DamagedInstallRecord` when ``.gideon-lock.json`` is there but is not a record:
    unreadable, not JSON, not an object, or without a ``sha256`` map of file paths to digests.
    """
    path = Path(skill_dir) / LOCK_FILENAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DamagedInstallRecord(f"{path.name} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise DamagedInstallRecord(f"{path.name} is not an object")
    digests = data.get("sha256")
    if not isinstance(digests, dict) or not all(
        isinstance(rel, str) and isinstance(digest, str)
        for rel, digest in digests.items()
    ):
        raise DamagedInstallRecord(
            f"{path.name} holds no digest of the files installed"
        )
    source = data.get("source")
    return InstallRecord(
        source=source if isinstance(source, str) else "", sha256=dict(digests)
    )


def write_install_record(
    skill_dir: Path,
    *,
    skill_id: str,
    source: str,
    trust_tier: str,
    verdict: str,
    sha256: Mapping[str, str],
) -> None:
    """Record in *skill_dir* that *sha256* (each file and its digest) was installed there from
    *source*, scanned at *trust_tier* with *verdict*. Atomic; a failure to write it is logged and
    leaves the skill without a record, which reads as unverified."""
    import time

    from gideon.core.atomic_write import atomic_json_write

    record = {
        "id": skill_id,
        "source": source,
        "trust_tier": trust_tier,
        "verdict": verdict,
        "sha256": dict(sha256),
        "installed_at": time.time(),
    }
    try:
        atomic_json_write(Path(skill_dir) / LOCK_FILENAME, record)
    except OSError:
        logger.warning(
            "could not write the install record of %s", skill_dir, exc_info=True
        )


def payload_digests(files: "list[dict[str, Any]]") -> dict[str, str]:
    """Each file of an install payload and the sha256 of the bytes it writes
    (:func:`_entry_bytes`), the install record left out."""
    return {
        str(entry.get("path", "")): hashlib.sha256(_entry_bytes(entry)).hexdigest()
        for entry in files
        if entry.get("path") and entry.get("path") != LOCK_FILENAME
    }


def _write_lock(
    target_dir: Path, detail: "SkillDetail", source: str, tier: "Any", report: "Any"
) -> None:
    """Record what an install wrote (:func:`write_install_record`): the payload's files, which
    are exactly the folder's files (:func:`install_skill_files`), from *source* at *tier*.
    """
    skill_dir = Path(target_dir) / (detail.name or detail.id)
    if not skill_dir.is_dir():
        return
    write_install_record(
        skill_dir,
        skill_id=detail.id,
        source=source,
        trust_tier=getattr(tier, "value", str(tier)),
        verdict=getattr(report.verdict, "value", str(report.verdict)),
        sha256=payload_digests(detail.files),
    )


#: The four answers to "are this skill's files what was installed?". ``intact``: exactly what its
#: record holds. ``edited``: changed since (a file changed, added or removed), whoever changed
#: it: the owner's save in the skill editor, a change she made to the files, Gideon's own
#: writers. ``tampered``: its record is there and cannot be read (:class:`DamagedInstallRecord`),
#: so nothing can say what was installed; an edit never touches the record. ``unverified``: no
#: record, so nothing installed it, or it was installed before records were kept.
INTACT = "intact"
EDITED = "edited"
TAMPERED = "tampered"
UNVERIFIED = "unverified"


@dataclass
class IntegrityReport:
    """One skill's files compared with its install record. ``state`` is one of the four answers
    above; ``mutated`` / ``missing`` / ``added`` name what changed, for an ``edited`` skill.
    """

    skill: str
    state: str = UNVERIFIED
    mutated: list[str] = field(
        default_factory=list
    )  # a recorded file whose bytes changed
    missing: list[str] = field(default_factory=list)  # a recorded file now gone
    added: list[str] = field(default_factory=list)  # a file the record does not hold
    #: The digest of the folder's files as they are (:func:`files_digest`), when it was read.
    digest: str = ""

    @property
    def ok(self) -> bool:
        return self.state == INTACT

    @property
    def unlocked(self) -> bool:
        return self.state == UNVERIFIED

    def summary(self) -> str:
        if self.state == UNVERIFIED:
            return f"{self.skill}: no install record (unverifiable)"
        if self.state == TAMPERED:
            return f"{self.skill}: its install record is damaged, so what was installed is unknown"
        if self.state == INTACT:
            return f"{self.skill}: intact"
        parts = []
        if self.mutated:
            parts.append(f"{len(self.mutated)} changed")
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.added:
            parts.append(f"{len(self.added)} added")
        return f"{self.skill}: edited ({', '.join(parts)})"


def verify_skill_integrity(skill_dir: Path) -> IntegrityReport:
    """Compare a skill's files with its install record (:func:`install_record`).

    Its owner's edit reads ``edited``, with what changed, and never as tampering: Gideon
    cannot tell who changed a file in the home, and a change to a skill there is the owner's to
    make. A damaged record is the one finding that is not an edit; it reads ``tampered`` and is
    written to the security log.
    """
    skill_dir = Path(skill_dir)
    name = skill_dir.name
    try:
        record = install_record(skill_dir)
    except DamagedInstallRecord as exc:
        rep = IntegrityReport(skill=name, state=TAMPERED)
        try:
            from gideon.security.sel import sel

            sel().log_api_access(
                caller="skills.verify_integrity",
                operation="skill_integrity",
                outcome="tampered",
                source="skills",
                resources=name,
                error=str(exc),
            )
        except Exception:
            logger.debug("integrity SEL audit failed", exc_info=True)
        return rep
    on_disk = file_digests(skill_dir)
    if record is None:
        return IntegrityReport(
            skill=name, state=UNVERIFIED, digest=files_digest(on_disk)
        )
    rep = IntegrityReport(skill=name, digest=files_digest(on_disk))
    for rel, want in record.sha256.items():
        got = on_disk.get(rel)
        if got is None:
            rep.missing.append(rel)
        elif got != want:
            rep.mutated.append(rel)
    rep.added = [rel for rel in on_disk if rel not in record.sha256]
    rep.state = EDITED if (rep.mutated or rep.missing or rep.added) else INTACT
    return rep


def _audit_install(
    source: str,
    skill_id: str,
    tier: "Any",
    report: "Any",
    *,
    outcome: str,
    rules: str = "",
) -> None:
    """Emit a SEL audit event for a scan/install/refuse (best-effort)."""
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=f"skills.install_guarded:{source}",
            operation="skill_install",
            outcome=outcome,
            source="skills",
            resources=f"{source}/{skill_id}",
            error=f"tier={getattr(tier, 'value', tier)} verdict={getattr(report.verdict, 'value', report.verdict)}"
            + (f" rules={rules}" if rules else ""),  # noqa: E501
        )
    except Exception:
        logger.debug("skill install SEL audit failed", exc_info=True)


class SkillsRegistry:
    """Holds named ``SkillsMarketplace`` implementations."""

    def __init__(self) -> None:
        self._marketplaces: dict[str, SkillsMarketplace] = {}
        self._lock = threading.RLock()

    def register(self, name: str, marketplace: SkillsMarketplace) -> None:
        from gideon.extensions.apps.code_provenance import keep

        with self._lock:
            self._marketplaces[name] = marketplace
            keep(lambda: self._forget(name, marketplace))

    def _forget(self, name: str, marketplace: SkillsMarketplace) -> None:
        """A retired owner's callback cannot remove a replacement registration."""
        with self._lock:
            if self._marketplaces.get(name) is marketplace:
                del self._marketplaces[name]

    def unregister(self, name: str) -> None:
        """Remove a registered marketplace. Idempotent — a name that was never registered
        is a no-op. Used by transient, single-operation sources (a pack import registers a
        ``PackMarketplace`` for one commit, then unregisters it — it is not a public
        marketplace and must not outlive the import)."""
        with self._lock:
            self._marketplaces.pop(name, None)

    def get(self, name: str) -> SkillsMarketplace:
        with self._lock:
            mp = self._marketplaces.get(name)
        if mp is None:
            raise KeyError(f"No skills marketplace registered as {name!r}")
        return mp

    def list(self) -> "list[str]":
        with self._lock:
            return sorted(self._marketplaces)

    # `builtins.list`, not `list`: this class defines a method named `list` (just above), which
    # shadows the builtin inside its own annotation scope — so `"list[dict[str, str]]"` resolved
    # to the METHOD and mypy read the return type as uniterable. That was silenced with a
    # `type: ignore[valid-type]` and went unnoticed for as long as nobody iterated the result;
    # the first caller that did got `"list?[dict[str, str]]" has no attribute "__iter__"`.
    # Qualifying the name states the type correctly instead of suppressing the complaint.
    def info(self) -> "builtins.list[dict[str, str]]":
        with self._lock:
            rows = sorted(self._marketplaces.items())
        return [
            {"name": n, "type": mp.marketplace_type, "trust_tier": mp.trust_tier}
            for n, mp in rows
        ]

    def install_guarded(
        self,
        marketplace_name: str,
        skill_id: str,
        target_dir: Path,
        *,
        force: bool = False,
    ) -> "InstallResult":
        """The install CHOKEPOINT (S3): every install routes through here so one gate
        covers all marketplaces and each ``install()`` stays a dumb file-writer.

        Resolves the registered marketplace by name and delegates to the shared gate
        :func:`install_scanned`. Raises :class:`SkillInstallRefused` on a blocked
        verdict; returns an :class:`InstallResult` on success."""
        return install_scanned(
            self.get(marketplace_name),
            marketplace_name,
            skill_id,
            target_dir,
            force=force,
        )


def warnings_consent(detail: "SkillDetail", report: "Any") -> str:
    """What a person accepts when they install a skill over a WARNING verdict: its warnings, and
    the exact files that were scanned for them. ``""`` for any other verdict.

    A digest of both, so an acceptance is bound to what was read: the same bytes scanned twice
    give the same value, and a file that changes after the scan (a second command appended to a
    script the scan had already flagged once) gives another, and is not installed on it.
    """
    import hashlib

    from gideon.security.supply_chain import Verdict

    if report.verdict is not Verdict.WARNING:
        return ""
    digest = hashlib.sha256(f"{detail.id}\0{detail.name}\n".encode("utf-8"))
    for entry in sorted(detail.files, key=lambda e: str(e.get("path", ""))):
        body = entry.get("data")
        raw = (
            body
            if isinstance(body, bytes)
            else str(entry.get("contents", "")).encode("utf-8")
        )
        digest.update(
            f"{entry.get('path', '')}\0{hashlib.sha256(raw).hexdigest()}\n".encode()
        )
    warned = [f for f in report.findings if f.severity is Verdict.WARNING]
    for line in sorted(f"{f.rule}\0{f.path}\0{f.evidence}" for f in warned):
        digest.update(f"{line}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


def scan_before_install(
    marketplace: "SkillsMarketplace", skill_id: str
) -> "tuple[Any, str]":
    import shutil
    import tempfile

    from gideon.core.config.locations import active_home
    from gideon.security.supply_chain import TrustTier, scan_dir

    try:
        tier = TrustTier(marketplace.trust_tier)
    except ValueError:
        tier = TrustTier.COMMUNITY
    detail = marketplace.fetch(skill_id)
    home = active_home()
    home.mkdir(parents=True, exist_ok=True)
    staged_root = Path(tempfile.mkdtemp(prefix=".gideon-skill-quarantine-", dir=home))
    try:
        staged_skill = record_path(
            staged_root, detail.name or skill_id, suffix="", kind="skill name"
        )
        _stage_files(detail.files, staged_skill)
        report = scan_dir(staged_skill, tier)
        return report, warnings_consent(detail, report)
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)


def install_scanned(
    marketplace: "SkillsMarketplace",
    source: str,
    skill_id: str,
    target_dir: Path,
    *,
    force: bool = False,
    accepted_warnings: str | None = None,
) -> "InstallResult":
    """The single supply-chain install gate — used by both registered-marketplace
    installs (:meth:`SkillsRegistry.install_guarded`) and app-owned skill seeding
    (:mod:`gideon.extensions.apps.skill_seed`), which passes a transient marketplace
    rooted at the app dir. One gate implementation, so nothing writes to the live
    skills tree without passing through it.

    fetch → stage to quarantine → whole-dir scan at the marketplace's trust tier →
    decide → commit the scanned bytes + ``.gideon-lock.json`` provenance + SEL audit.

    - ``clean`` / ``low`` → commit.
    - ``warning`` → refuse unless ``force`` (a calculated, explicit override).
    - ``dangerous`` → REFUSE; ``force`` does NOT override (the load-bearing floor).

    Quarantine-first means dangerous content never lands in the live skills tree.
    Raises :class:`SkillInstallRefused` on a blocked verdict; returns an
    :class:`InstallResult` on success."""
    import shutil
    import tempfile

    from gideon.core.config.locations import active_home
    from gideon.security.supply_chain import TrustTier, Verdict, scan_dir

    home = active_home()
    destination = Path(target_dir).expanduser().resolve()
    try:
        destination.relative_to(home)
    except ValueError as exc:
        raise ValueError(
            "skill installs must stay inside the active Gideon home"
        ) from exc
    target_dir = destination

    home.mkdir(parents=True, exist_ok=True)

    try:
        tier = TrustTier(marketplace.trust_tier)
    except ValueError:
        tier = TrustTier.COMMUNITY

    detail = marketplace.fetch(skill_id)
    staged_root = Path(tempfile.mkdtemp(prefix=".gideon-skill-quarantine-", dir=home))
    try:
        staged_skill = record_path(
            staged_root, detail.name or skill_id, suffix="", kind="skill name"
        )
        _stage_files(detail.files, staged_skill)

        report = scan_dir(staged_skill, tier)
        _audit_install(source, skill_id, tier, report, outcome="scanned")

        if report.verdict is Verdict.DANGEROUS:
            _audit_install(source, skill_id, tier, report, outcome="refused")
            raise SkillInstallRefused(report, dangerous=True)
        if report.verdict is Verdict.WARNING:
            if not force and accepted_warnings != warnings_consent(detail, report):
                _audit_install(source, skill_id, tier, report, outcome="needs_confirm")
                raise SkillInstallRefused(report, dangerous=False)
            rules = ",".join(
                sorted(
                    {
                        finding.rule
                        for finding in report.findings
                        if finding.severity is Verdict.WARNING
                    }
                )
            )
            _audit_install(
                source, skill_id, tier, report, outcome="accepted", rules=rules
            )

        from gideon.extensions.skills.loader import hold_library

        with hold_library():
            written = install_skill_files(
                detail.files, detail.name or skill_id, target_dir
            )
            _write_lock(target_dir, detail, source, tier, report)
        _audit_install(source, skill_id, tier, report, outcome="installed")
        return InstallResult(path=written, report=report, tier=tier)
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)


_DEFAULT_REGISTRY: SkillsRegistry | None = None


def get_default_skills_registry() -> SkillsRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SkillsRegistry()
    return _DEFAULT_REGISTRY


def list_local_skills(extra_paths: list[Path] | None = None) -> list[dict[str, str]]:
    """Scan all skill discovery paths and return a list of skill metadata dicts.

    Each dict contains: ``{name, description, path, source}``.
    The ``source`` field is the discovery directory name.
    """
    search_paths = _skill_discovery_paths()
    if extra_paths:
        search_paths.extend(extra_paths)

    skills: list[dict[str, str]] = []
    seen_names: set[str] = set()

    from gideon.extensions.skills.loader import iter_skill_files

    for base in search_paths:
        if not base.is_dir():
            continue
        for name, skill_md in iter_skill_files(base):
            if name in seen_names:
                continue
            seen_names.add(name)
            description = _parse_description(skill_md)
            skills.append(
                {
                    "name": name,
                    "description": description,
                    "path": str(skill_md),
                    "source": str(base),
                }
            )

    from gideon.extensions.skills.loader import _outside_skill_dirs

    for base in _outside_skill_dirs():
        if not base.is_dir():
            continue
        for name, skill_md in iter_skill_files(base):
            if name in seen_names:
                continue
            seen_names.add(name)
            description = _parse_description(skill_md)
            skills.append(
                {
                    "name": name,
                    "description": description,
                    "path": str(skill_md),
                    "source": base.name,
                }
            )

    return skills


def _parse_description(skill_md: Path) -> str:
    """Extract the description field from SKILL.md YAML frontmatter.

    Delegated to the one parser. This copy's block-scalar folding was the ONLY
    capability any duplicate had over the loader, so it was promoted into
    `_parse_frontmatter_text` rather than dropped — the behavior is preserved,
    the duplication is not.
    """
    from gideon.extensions.skills.loader import ProcedureLibrary

    try:
        text = skill_md.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    return ProcedureLibrary._parse_frontmatter_text(text).get("description", "")


def install_skill_files(
    files: list[dict[str, str]],
    skill_name: str,
    target_base: Path,
) -> Path:
    """Write skill files to ``target_base/<skill_name>/``.

    Rejects any path containing ``..``.
    Returns the path to the written SKILL.md.

    🔴 THE DIRECTORY NAME IS VALIDATED TOO. Every *file* path here was checked for ``..`` and for a
    leading ``/`` — and ``skill_name``, which names the directory all of them are written into, was
    not. So a marketplace-supplied name of ``"../../evil"`` escaped the skills tree entirely, and
    `mkdir(parents=True)` created wherever it landed (#739). Checking the leaves while the branch is
    unchecked is the whole bug: a safe relative path under an unsafe root is an unsafe path.

    Latent rather than exploited — only trusted marketplaces are registered by default — which is
    also why it is worth closing now rather than after that changes.
    """
    skill_dir = record_path(target_base, skill_name, suffix="", kind="skill name")

    from gideon.security.supply_chain import Verdict, default_scanner

    for file_entry in files:
        rel_path = file_entry.get("path", "")
        if ".." in rel_path or rel_path.startswith("/"):
            raise ValueError(f"Rejected unsafe file path: {rel_path!r}")
        if "data" in file_entry:
            continue
        contents = file_entry.get("contents", "")
        is_script = (
            rel_path.endswith((".sh", ".bash", ".py", ".js", ".rb", ".pl"))
            or "/scripts/" in f"/{rel_path}"
        )
        report = default_scanner.scan_text(
            contents,
            surface="script" if is_script else "manifest",
        )
        if report.verdict is Verdict.DANGEROUS:
            cats = (
                ", ".join(sorted({f.rule for f in report.findings}))
                or "dangerous content"
            )
            raise ValueError(
                f"skill install refused: scanner flagged {rel_path!r} as dangerous ({cats})"
            )

    import shutil
    import tempfile
    import uuid

    if not any(entry.get("path") == "SKILL.md" for entry in files):
        raise ValueError(f"No SKILL.md found in files for skill {skill_name!r}")
    target_base.mkdir(parents=True, exist_ok=True)
    if skill_dir.is_symlink():
        raise ValueError("Skill folder must not be a symbolic link")
    stage = Path(tempfile.mkdtemp(prefix=".skill-install-", dir=target_base))
    backup = target_base / f".skill-backup-{uuid.uuid4().hex}"
    try:
        _stage_files(files, stage)
        from gideon.core.atomic_write import atomic_directory_publish

        atomic_directory_publish(stage, skill_dir, backup=backup)
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return skill_dir / "SKILL.md"
