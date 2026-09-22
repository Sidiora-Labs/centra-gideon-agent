"""Skills loader — markdown skill files for agent capabilities."""

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from gideon.core.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)


SKILLS_DIR_NAME = "skills"
_MIN_TRIGGER_OVERLAP = 0.7


AUTO_SKILL_NAMESPACE = "auto"

AUTO_SKILL_SOURCE_VALUE = "auto"

TAUGHT_SKILL_SOURCE_VALUE = "taught"

AUTO_SKILL_MAX_PROCEDURE_CHARS = 10_240

_AUTO_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

_BUILTIN_SKILLS_DIR = Path(__file__).parent / "bundled"


@dataclass(frozen=True)
class AutoSkillProvenance:
    """Immutable provenance record for an auto-generated skill.

    Serialized into the SKILL.md YAML frontmatter (``source: auto``,
    ``session_key``, ``created_at``, ``refined_at``, ``reuse_count``) so
    operators can always see how a skill was produced and when it was
    last refined.  Absence of ``source: auto`` identifies the skill as
    hand-authored.
    """

    session_key: str
    created_at: str
    refined_at: str = ""
    reuse_count: int = 0

    @staticmethod
    def now_iso() -> str:
        """Return the current time as an ISO 8601 UTC string."""
        return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

    def to_frontmatter_lines(self) -> list[str]:
        """Serialize to the YAML key/value lines used in SKILL.md frontmatter."""
        lines = [
            f"source: {AUTO_SKILL_SOURCE_VALUE}",
            f"session_key: {self.session_key}",
            f"created_at: {self.created_at}",
        ]
        if self.refined_at:
            lines.append(f"refined_at: {self.refined_at}")
        if self.reuse_count:
            lines.append(f"reuse_count: {self.reuse_count}")
        return lines


def _auto_name_from_title(raw: str) -> str:
    """Convert a free-form title into a safe ``auto/<slug>`` skill name.

    Strategy:
    - lowercase
    - replace any run of non-alphanumerics with a single hyphen
    - strip leading/trailing hyphens
    - truncate to 62 chars (leaves room for uniqueness suffix)

    Returns the slug component only; caller prepends the namespace.
    Returns an empty string if the input can't be sanitized.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:62].rstrip("-")
    if not _AUTO_NAME_PATTERN.match(slug):
        return ""
    return slug


def _build_auto_skill_content(
    *,
    slug: str,
    description: str,
    triggers: str,
    procedure_md: str,
    provenance: AutoSkillProvenance,
) -> str:
    """Render a complete ``SKILL.md`` body for an auto-generated skill.

    Layout::

        ---
        name: auto/<slug>
        description: <description>
        triggers: <comma-separated triggers>
        source: auto
        session_key: <session>
        created_at: <iso8601>
        refined_at: <iso8601>      # omitted if empty
        reuse_count: <int>         # omitted if 0
        ---

        # <slug> (auto-generated)

        <procedure_md>

    The leading ``---`` keeps this compatible with existing frontmatter
    parsing in ``ProcedureLibrary._parse_frontmatter``.  YAML values are
    single-line and newline-stripped to stay within the parser's
    ``key: value`` line format.
    """
    name = f"{AUTO_SKILL_NAMESPACE}/{slug}"
    desc_safe = re.sub(r"\s+", " ", description or "").strip() or name
    triggers_safe = re.sub(r"\s+", " ", triggers or "").strip()
    header_lines = [
        "---",
        f"name: {name}",
        f"description: {desc_safe}",
    ]
    if triggers_safe:
        header_lines.append(f"triggers: {triggers_safe}")
    header_lines.extend(provenance.to_frontmatter_lines())
    header_lines.append("---")
    body = procedure_md.replace("\r\n", "\n").strip()
    return "\n".join(header_lines) + "\n\n" + body + "\n"


def _project_skills_dir() -> Path | None:
    """Return project-level skills/ dir from GIDEON_PROJECT_DIR, or None."""
    val = os.environ.get("GIDEON_PROJECT_DIR")
    if val:
        p = Path(val) / "skills"
        if p.is_dir():
            return p
    return None


def iter_skill_files(base: Path) -> list[tuple[str, Path]]:
    """Recursively find all SKILL.md files under *base*.

    Returns ``(relative_name, skill_file_path)`` pairs sorted by name.
    The relative name uses ``/`` as separator (e.g. ``utils/tiny-url``).
    """
    results: list[tuple[str, Path]] = []
    if not base.exists():
        return results
    for skill_file in sorted(base.rglob("SKILL.md")):
        rel = skill_file.parent.relative_to(base)
        name = str(rel).replace("\\", "/")
        results.append((name, skill_file))
    return results


def _ensure_builtin_skills(base: Path) -> None:
    """Sync built-in skills: copy new/updated, remove stale.

    Supports nested directories (e.g. ``utils/tiny-url/SKILL.md``).
    Copies the entire skill directory (scripts, assets, etc.), not just SKILL.md.
    Removes skills from *base* that no longer exist in any source.
    """
    source_names: set[str] = set()
    for src_root in (_project_skills_dir(), _BUILTIN_SKILLS_DIR):
        if not src_root or not src_root.exists():
            continue
        for name, src_file in iter_skill_files(src_root):
            source_names.add(name)
            src_dir = src_file.parent
            dest_dir = base / name
            dest_file = dest_dir / "SKILL.md"
            if (
                not dest_file.exists()
                or src_file.stat().st_mtime > dest_file.stat().st_mtime
            ):
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                shutil.copytree(src_dir, dest_dir)
                logger.info("Synced skill: %s", name)

    stale_builtins = {"learn", "subagent", "cron", "gideon-core"}
    if base.exists():
        for name in stale_builtins:
            stale = base / name
            if stale.is_dir():
                shutil.rmtree(stale)
                logger.info("Removed stale builtin skill: %s", name)


def skills_dir() -> Path:
    return config_dir() / SKILLS_DIR_NAME


def _suppressed_producers() -> set[tuple[str, str]]:
    """Feedback-Signal's withholding set (FS-6), fetched fail-open.

    ``feedback.suppressed_producers`` already fail-opens internally (config off →
    empty, any error → empty); this wrapper adds a second belt so an import/attr
    fault here degrades to *suppress nothing* rather than empty the turn's skills.
    A feedback fault must never silence the assistant's capabilities."""
    try:
        from gideon.cognition import feedback

        return feedback.suppressed_producers()
    except Exception:
        logger.debug(
            "suppressed_producers fetch failed — suppressing nothing", exc_info=True
        )
        return set()


def _agent_slug(agent: str) -> str:
    """Filesystem-safe slug for an agent's per-agent dir name.

    Canonicalizes default-agent spellings to one key (matching agent-scoped
    memory), then sanitizes to ``[a-z0-9._-]`` so the dir name can't traverse or
    collide with control chars. Mirrors the agent-scoped-hooks convention."""
    from gideon.engine.agents.defaults import normalize_agent_name

    name = normalize_agent_name(agent) or "gideon"
    slug = re.sub(r"\.+", "-", name)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-_").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "gideon"


def agent_skills_dir(agent: str) -> Path:
    """The agent-local skills tier: ``~/.gideon/agents/<slug>/skills/``.

    Highest-precedence tier (skill-agent-local-tier) — a skill here overrides a
    same-named bundled/global one, but only for this agent. Sits under the same
    per-agent dir root as agent-scoped hooks/config."""
    return config_dir() / "agents" / _agent_slug(agent) / SKILLS_DIR_NAME


RESOURCE_MAX_BYTES = 32_768

RESOURCE_DESC_MAX_CHARS = 160


@dataclass(frozen=True)
class SkillResource:
    """One resource a skill DECLARED in its frontmatter (never its contents)."""

    path: str
    description: str = ""


@dataclass(frozen=True)
class ResourceRead:
    """A single resource read: its text plus whether the cap bit."""

    skill: str
    path: str
    text: str
    size: int
    truncated: bool


class SkillResourceRefused(Exception):
    """A resource load was refused. ``reason`` is a stable, testable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _norm_declared_path(raw: str) -> str:
    """Canonical form of a declared/requested resource path, or "" if unusable.

    Returns "" for anything that must never reach the filesystem: absolute paths,
    Windows drive/UNC spellings, backslashes, NUL bytes, or a ``..`` segment.
    Rejecting (rather than sanitizing) is deliberate — a path that needed
    stripping to become safe is not the path the author declared.
    """
    val = (raw or "").strip().strip("\"'").replace("\r", "").replace("\n", "")
    if not val or "\x00" in val or "\\" in val:
        return ""
    if val.startswith("/") or val.startswith("~") or re.match(r"^[A-Za-z]:", val):
        return ""
    parts = [p for p in val.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return ""
    return "/".join(parts)


def parse_resources(content: str) -> list[SkillResource]:
    """Parse the optional ``resources:`` frontmatter block from *content*.

    The flat ``key: value`` frontmatter reader (:meth:`ProcedureLibrary._parse_frontmatter_text`)
    cannot represent a list of mappings — it documents nested mappings as skipped —
    so this is a small dedicated reader for exactly this one structured block
    rather than a second general-purpose parser. Accepted spelling:

        resources:
          - path: reference/api-notes.md
            description: one line
          - path: scripts/check.sh
          - reference/other.md

    ``description`` is optional, and the bare-string spelling (last item) is
    accepted as a path-only declaration — a skill that writes it should get the
    resource, not silence. An item with no usable path is DROPPED: a malformed or
    hostile declaration must never widen the allowlist. Like the rest of this
    frontmatter reader, an inline ``# comment`` after a value is NOT stripped (it
    would become part of the path); comment-only lines are skipped.
    """
    text = content.lstrip("﻿").lstrip().replace("\r\n", "\n")
    if not text.startswith("---"):
        return []
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return []

    out: list[SkillResource] = []
    cur: dict[str, str] | None = None
    in_block = False

    def _flush() -> None:
        if cur is None:
            return
        rel = _norm_declared_path(cur.get("path", ""))
        if rel:
            desc = " ".join(cur.get("description", "").split())[
                :RESOURCE_DESC_MAX_CHARS
            ]
            out.append(SkillResource(path=rel, description=desc))

    for raw in match.group(1).split("\n"):
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        if not in_block:
            if not raw[:1].isspace() and re.match(r"^resources\s*:\s*$", item):
                in_block = True
            continue
        if item.startswith("- "):
            _flush()
            cur = {}
            item = item[2:].strip()
            if not item:
                continue
        elif not raw[:1].isspace():
            _flush()
            cur, in_block = None, False
            continue
        if cur is None:
            continue
        key, _, value = item.partition(":")
        if key.strip().lower() in ("path", "description") and _:
            cur[key.strip().lower()] = value.strip().strip("\"'")
        elif not cur.get("path"):
            cur["path"] = item.strip().strip("\"'")
    _flush()
    return out


def parse_frontmatter(content: str) -> dict[str, str]:
    """The flat ``key: value`` frontmatter of already-read SKILL.md *content*.

    A module-level public reader beside :func:`parse_resources`, for callers that hold
    the text and need one declared field (CE2-9 reads ``context_tier`` and
    ``description``). It delegates to :meth:`ProcedureLibrary._parse_frontmatter_text` rather
    than re-implementing the line parser: a second parser would disagree about BOMs,
    block scalars and block lists exactly where the first one was carefully fixed.
    """
    return ProcedureLibrary._parse_frontmatter_text(content)


def validate_skill_md(content: str) -> list[str]:
    """Return validation errors for user-authored SKILL.md content."""
    errors: list[str] = []
    if not content.strip().startswith("---"):
        errors.append("SKILL.md must start with YAML frontmatter (---)")
        return errors
    end = content.find("\n---", 3)
    if end == -1:
        errors.append("SKILL.md frontmatter is not closed with ---")
        return errors
    frontmatter = content[3:end]
    name_m = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
    if not name_m:
        errors.append("SKILL.md frontmatter missing required 'name' field")
    else:
        name = name_m.group(1).strip().strip("\"'")
        if not _SKILL_NAME_PATTERN.match(name):
            errors.append(
                f"SKILL.md name must match ^[a-z0-9][a-z0-9-]{{0,62}}$ (got {name!r})"
            )
    if not re.search(r"^description:\s*.+$", frontmatter, re.MULTILINE):
        errors.append("SKILL.md frontmatter missing required 'description' field")
    return errors


def skill_provenance(meta: dict[str, str]) -> str:
    """Return the supported skill provenance declared in frontmatter.

    ``source`` also carries values that describe install origins.  The library
    view only exposes the two creation origins it can explain, keeping its
    public value set stable for hand-authored and third-party skills.
    """
    value = meta.get("source", "").strip().lower()
    if value in (AUTO_SKILL_SOURCE_VALUE, TAUGHT_SKILL_SOURCE_VALUE):
        return value
    return ""


class ProcedureLibrary:
    """Load skill markdown files from ~/.gideon/skills/.

    Supports nested directories. Each skill is identified by its
    relative path from the skills root (e.g. ``utils/tiny-url``).

    Directory layout::

        ~/.gideon/skills/
        ├── learn/SKILL.md
        ├── subagent/SKILL.md
        ├── code/
        │   ├── workspace-tools/SKILL.md
        │   └── code-task-generation/SKILL.md
        └── utils/
            ├── tiny-url/SKILL.md
            └── mcp-debug/SKILL.md
    """

    def __init__(
        self,
        skills_path: Path | None = None,
        install_builtins: bool = True,
        *,
        agent: str | None = None,
    ):
        self._scoped = skills_path is not None
        self._dir = skills_path or skills_dir()
        self._agent_dir: Path | None = None
        if agent is not None and not self._scoped:
            self._agent_dir = agent_skills_dir(agent)
        if install_builtins:
            _ensure_builtin_skills(self._dir)
        self._fm_cache: dict[str, tuple[float, dict[str, str]]] = {}

    def _iter(self) -> list[tuple[str, Path]]:
        """Return all ``(name, skill_file)`` pairs from this loader's directories.

        The default loader aggregates the global discovery paths; a loader
        constructed with an explicit ``skills_path`` stays confined to it.
        """
        results: list[tuple[str, Path]] = []
        if self._agent_dir is not None and self._agent_dir.is_dir():
            results.extend(iter_skill_files(self._agent_dir))
        results.extend(iter_skill_files(self._dir))
        if self._scoped:
            return results
        from gideon.extensions.skills.marketplace import SKILL_DISCOVERY_PATHS

        seen = {name for name, _ in results}
        for extra_dir in SKILL_DISCOVERY_PATHS:
            if extra_dir.is_dir() and extra_dir != self._dir:
                for name, path in iter_skill_files(extra_dir):
                    if name not in seen:
                        results.append((name, path))
                        seen.add(name)
        return results

    def _cached_frontmatter(self, path: Path) -> dict[str, str]:
        """Parse frontmatter with mtime-based caching."""
        key = str(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        cached = self._fm_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        meta = self._parse_frontmatter(path)
        self._fm_cache[key] = (mtime, meta)
        return meta

    def list_skills(
        self, *, with_usage: bool = False, with_provenance: bool = False
    ) -> list[dict]:
        """Return list of skill metadata dicts with key, name, description, path, dir, always.

        When *with_usage* is set, each dict also carries ``use_count`` and
        ``last_used_at`` from the sidecar usage counter (skill-use-counter) —
        the live use signal consumed by surfacing-ranking (#26) and the
        library curator (#27). Lazy-imported to avoid an import cycle.

        When *with_provenance* is set, each dict also carries ``created_at`` and
        ``refined_at`` from :class:`AutoSkillProvenance`'s frontmatter (empty for a
        skill that never carried provenance — a marketplace install, say). Opt-in for
        the same reason ``with_usage`` is: ``/api/skills`` has a pinned payload shape,
        and widening it for every caller to serve one panel would be a needless
        contract change. The learning-summary block (LV-3) is the consumer.
        """
        usage: dict = {}
        if with_usage:
            try:
                from gideon.extensions.skills.usage import SkillUsageStore

                usage = SkillUsageStore().all_usage()
            except Exception:
                usage = {}
        agent_root = str(self._agent_dir) if self._agent_dir is not None else None
        skills: list[dict] = []
        for name, skill_file in self._iter():
            meta = self._cached_frontmatter(skill_file)
            is_agent_local = agent_root is not None and str(skill_file).startswith(
                agent_root
            )
            row: dict = {
                "key": name,
                "name": meta.get("name", name),
                "description": meta.get("description", name),
                "triggers": meta.get("triggers", ""),
                "path": str(skill_file),
                "dir": str(skill_file.parent),
                "always": meta.get("always", "").lower() == "true",
                "status": (meta.get("status", "") or "active").lower(),
                "agent_local": is_agent_local,
            }
            if with_usage:
                u = usage.get(name)
                row["use_count"] = u.count if u else 0
                row["last_used_at"] = u.last_used_at if u else ""
            if with_provenance:
                row["created_at"] = meta.get("created_at", "")
                row["refined_at"] = meta.get("refined_at", "")
            skills.append(row)
        return skills

    @staticmethod
    def _safe_name(name: str) -> bool:
        """Return True if skill name is safe (no path traversal)."""
        return bool(name) and ".." not in name and "\\" not in name

    def _search_dirs(self) -> list[Path]:
        """Directories this loader resolves names against.

        A scoped loader (explicit ``skills_path``) confines all lookups to its
        own dir; the default loader fans out across the global discovery paths.
        """
        if self._scoped:
            return [self._dir]
        from gideon.extensions.skills.marketplace import SKILL_DISCOVERY_PATHS

        agent_dirs = [self._agent_dir] if self._agent_dir is not None else []
        return agent_dirs + [self._dir] + SKILL_DISCOVERY_PATHS

    def skill_file(self, name: str) -> Path | None:
        """The ``SKILL.md`` this loader resolves *name* to, or None.

        One resolution order for the body and its resources: a resource must come
        from the SAME directory as the SKILL.md that declared it, or a lower-
        precedence tier could lend files to a higher-precedence skill's allowlist.
        """
        if not self._safe_name(name):
            return None
        for search_dir in self._search_dirs():
            skill_file = search_dir / name / "SKILL.md"
            if skill_file.exists():
                return skill_file
        return None

    def load_skill(self, name: str) -> str | None:
        """Load a single skill's content by name, searching this loader's dirs.

        ES-7 §3.3: the ablation/bench suppression set is consulted HERE, the one place a
        body is read, so a suppressed skill's body cannot reach a prompt through the
        forced, surfaced, or ``skill_invoke`` path. ``None`` (not ``""``) so a suppressed
        skill is indistinguishable from an absent one to every caller — every one of them
        already guards with ``if content:``. Suppression is env-scoped to a throwaway eval
        child; with the env var absent this is the shipped behaviour exactly.
        """
        from gideon.extensions.skills.suppression import is_suppressed

        if is_suppressed(name):
            return None
        skill_file = self.skill_file(name)
        if skill_file is None:
            return None
        content = skill_file.read_text(encoding="utf-8")
        from gideon.extensions.skills import overlays

        return overlays.render_with_overlay(name, content)

    def resources_for(self, name: str) -> list[SkillResource]:
        """The resources *name* DECLARED — the allowlist, and the L0 catalog's input.

        Read from the on-disk ``SKILL.md`` and NOT from :meth:`load_skill`'s
        overlay-rendered text: an accepted refinement is appended content, and it
        must not be able to add a path to the allowlist. Never raises — an
        unreadable or resource-less skill yields ``[]``.
        """
        skill_file = self.skill_file(name)
        if skill_file is None:
            return []
        try:
            raw = skill_file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        return parse_resources(raw)

    def read_resource(self, name: str, path: str) -> ResourceRead:
        """Read ONE declared resource of skill *name*. Refuses anything else.

        Order matters, and each step is load-bearing:

        1. the skill resolves at all (``_safe_name`` also fences the skill name);
        2. the requested path canonicalizes (absolute / ``..`` / backslash → out);
        3. it is IN the declared allowlist — an existing-but-undeclared file is
           refused, so the tool can never become an arbitrary file read;
        4. containment is verified AFTER ``realpath``, which is the only check a
           symlink pointing out of the skill dir cannot satisfy;
        5. it is a regular file, read with a cap and a truncation flag.

        Raises :class:`SkillResourceRefused` (with a stable ``reason``) rather than
        returning a sentinel, so a refusal can never be mistaken for content.
        """
        skill_file = self.skill_file(name)
        if skill_file is None:
            raise SkillResourceRefused(
                "unknown_skill",
                f"no skill named '{name}'. Check the skill index for exact names.",
            )
        rel = _norm_declared_path(path)
        if not rel:
            raise SkillResourceRefused(
                "bad_path",
                f"'{path}' is not a usable resource path (must be relative to the "
                "skill dir, with no '..' segment).",
            )
        declared = {r.path for r in self.resources_for(name)}
        if rel not in declared:
            listed = ", ".join(sorted(declared)) or "none"
            raise SkillResourceRefused(
                "undeclared",
                f"'{rel}' is not declared in {name}'s `resources:` frontmatter "
                f"(declared: {listed}). Only declared resources can be loaded.",
            )
        base = Path(os.path.realpath(skill_file.parent))
        target = Path(os.path.realpath(base / rel))
        if target != base and base not in target.parents:
            raise SkillResourceRefused(
                "escapes_skill_dir",
                f"'{rel}' resolves outside {name}'s directory and was refused.",
            )
        if not target.is_file():
            raise SkillResourceRefused(
                "not_found",
                f"'{rel}' is declared by {name} but is not a readable file.",
            )
        try:
            size = target.stat().st_size
            with target.open("rb") as fh:
                data = fh.read(RESOURCE_MAX_BYTES)
        except OSError as exc:
            raise SkillResourceRefused(
                "not_found", f"'{rel}' could not be read: {exc.strerror or exc}"
            ) from exc
        return ResourceRead(
            skill=name,
            path=rel,
            text=data.decode("utf-8", errors="replace"),
            size=size,
            truncated=size > RESOURCE_MAX_BYTES,
        )

    @property
    def _write_dir(self) -> Path:
        """Where new skills are written. An agent-scoped loader writes into its
        agent-local tier (so create/read agree — writing global while reading
        agent-local would be a split brain); otherwise the loader's base dir."""
        return self._agent_dir if self._agent_dir is not None else self._dir

    def create_skill(self, name: str, content: str) -> bool:
        """Create a new skill directory with SKILL.md.  Returns True on success.

        Writes into the loader's write tier (agent-local when agent-scoped, else
        the base skills dir) — matching where the same loader would resolve it."""
        if not self._safe_name(name) or validate_skill_md(content):
            return False
        skill_dir = self._write_dir / name
        if skill_dir.exists():
            return False
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        logger.info("Created skill: %s", name)
        return True

    def update_skill(self, name: str, content: str) -> bool:
        """Overwrite an existing skill's SKILL.md.  Returns True if found."""
        if not self._safe_name(name) or validate_skill_md(content):
            return False
        skill_file = self._dir / name / "SKILL.md"
        if not skill_file.exists():
            return False
        skill_file.write_text(content, encoding="utf-8")
        logger.info("Updated skill: %s", name)
        return True

    def delete_skill(self, name: str) -> bool:
        """Delete a skill directory from any discovery path.  Returns True if found and removed."""
        if not self._safe_name(name):
            return False
        for search_dir in self._search_dirs():
            skill_dir = search_dir / name
            if skill_dir.is_dir():
                shutil.rmtree(skill_dir)
                logger.info("Deleted skill: %s (from %s)", name, search_dir)
                return True
        return False

    def is_auto_generated(self, name: str) -> bool:
        """Return True if *name* refers to a skill in the auto namespace.

        Cheap filesystem check (no frontmatter parse) based on the
        directory prefix.  Used for filtering and safety guards (e.g.
        refusing to overwrite a hand-authored skill from an auto-update
        path).
        """
        if not self._safe_name(name):
            return False
        return name.startswith(f"{AUTO_SKILL_NAMESPACE}/")

    def find_similar(
        self,
        description: str,
        threshold: float = 0.85,
        *,
        exclude: str = "",
    ) -> str | None:
        """Return the name of an existing skill whose description overlaps with *description*.

        Uses case-insensitive word-set Jaccard-like overlap against every
        loaded skill's ``description`` frontmatter value:

            score = |words(a) ∩ words(b)| / |words(a) ∪ words(b)|

        Intended for deduplication of auto-generated skills — we don't
        want the agent producing a near-duplicate of an existing skill.
        Returns the first skill whose score ≥ *threshold*, or ``None``
        if nothing matches.

        *exclude* lets callers suppress self-matches during refinement.
        """
        if not description:
            return None
        query_words = set(re.findall(r"\w+", description.lower()))
        if not query_words:
            return None
        best_name: str | None = None
        best_score: float = 0.0
        for name, skill_file in self._iter():
            if exclude and name == exclude:
                continue
            meta = self._cached_frontmatter(skill_file)
            existing = meta.get("description", "")
            if not existing:
                continue
            existing_words = set(re.findall(r"\w+", existing.lower()))
            if not existing_words:
                continue
            intersection = query_words & existing_words
            union = query_words | existing_words
            score = len(intersection) / len(union) if union else 0.0
            if score > best_score:
                best_score = score
                best_name = name
        if best_score >= threshold:
            return best_name
        return None

    def create_auto_skill(
        self,
        slug: str,
        *,
        description: str,
        triggers: str,
        procedure_md: str,
        provenance: AutoSkillProvenance,
    ) -> str | None:
        """Write a new auto-generated skill under ``auto/<slug>/SKILL.md``.

        Returns the full skill name (``auto/<slug>``) on success, or
        ``None`` if the slug is invalid or the skill already exists.

        Caller is responsible for:
        - Running ``find_similar()`` first to avoid near-duplicates.
        - Passing already-redacted ``procedure_md`` (sensitive data is
          the caller's responsibility — this method is pure I/O).
        - Enforcing the ``skills.auto_create_from_sessions`` config flag.
        """
        if not _AUTO_NAME_PATTERN.match(slug):
            logger.warning("Rejected auto skill: slug %r failed validation", slug)
            return None
        if len(procedure_md) > AUTO_SKILL_MAX_PROCEDURE_CHARS:
            logger.warning(
                "Rejected auto skill %s: procedure %d chars exceeds cap %d",
                slug,
                len(procedure_md),
                AUTO_SKILL_MAX_PROCEDURE_CHARS,
            )
            return None
        name = f"{AUTO_SKILL_NAMESPACE}/{slug}"
        skill_dir = self._dir / name
        if skill_dir.exists():
            logger.info("Auto skill %s already exists, skipping", name)
            return None
        content = _build_auto_skill_content(
            slug=slug,
            description=description,
            triggers=triggers,
            procedure_md=procedure_md,
            provenance=provenance,
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        logger.info("Created auto skill: %s", name)
        return name

    def update_auto_skill(
        self,
        name: str,
        *,
        description: str,
        triggers: str,
        procedure_md: str,
        provenance: AutoSkillProvenance,
    ) -> bool:
        """Update an existing auto-generated skill with a refined procedure.

        Refuses to overwrite skills NOT in the auto namespace — protects
        hand-authored skills from being clobbered by the refine path.
        Returns True on success.

        Caller is responsible for passing already-redacted ``procedure_md``.
        """
        if not self.is_auto_generated(name):
            logger.warning(
                "Refusing to auto-refine non-auto skill: %s (not in %s/)",
                name,
                AUTO_SKILL_NAMESPACE,
            )
            return False
        skill_file = self._dir / name / "SKILL.md"
        if not skill_file.exists():
            return False
        if len(procedure_md) > AUTO_SKILL_MAX_PROCEDURE_CHARS:
            logger.warning(
                "Refusing to refine %s: procedure %d chars exceeds cap %d",
                name,
                len(procedure_md),
                AUTO_SKILL_MAX_PROCEDURE_CHARS,
            )
            return False
        existing_meta = self._cached_frontmatter(skill_file)
        original_created_at = existing_meta.get("created_at")
        if original_created_at:
            provenance = replace(provenance, created_at=original_created_at)
        slug = name.split("/", 1)[1]
        content = _build_auto_skill_content(
            slug=slug,
            description=description,
            triggers=triggers,
            procedure_md=procedure_md,
            provenance=provenance,
        )
        skill_file.write_text(content, encoding="utf-8")
        logger.info("Refined auto skill: %s", name)
        return True

    def list_auto_skills(self) -> list[dict]:
        """Return metadata dicts for all skills under the auto namespace.

        Dashboard / CLI consumers use this to display provenance to
        users.  Hand-authored skills are excluded.
        """
        return [
            s
            for s in self.list_skills()
            if s["key"].startswith(f"{AUTO_SKILL_NAMESPACE}/")
        ]

    def get_always_skills(self) -> list[str]:
        """Return names of skills marked ``always: true`` in frontmatter."""
        result: list[str] = []
        for name, skill_file in self._iter():
            meta = self._cached_frontmatter(skill_file)
            if meta.get("always", "").lower() == "true":
                result.append(name)
        return result

    def get_triggered_skills(self, text: str) -> list[str]:
        """Return names of skills whose triggers match the given text.

        Uses word-overlap matching with multi-word trigger phrases and
        negative keywords.  Triggers are comma-separated phrases in the
        ``triggers`` frontmatter field.  A phrase prefixed with ``!`` is a
        negative trigger — if *any* negative trigger matches, the skill is
        excluded regardless of positive matches.

        Returns up to ``max_triggered`` skills sorted by best overlap score.
        """
        from gideon.core.config.loader import AppConfig
        from gideon.security.sel import sel

        cfg = AppConfig.load()
        text_words = set(re.findall(r"\w+", text.lower()))
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        scored: list[tuple[str, float]] = []
        for name, skill_file in self._iter():
            meta = self._cached_frontmatter(skill_file)
            if meta.get("always", "").lower() == "true":
                continue
            triggers = meta.get("triggers", "")
            if not triggers:
                continue

            negated = False
            best_overlap = 0.0
            for trigger in triggers.split(","):
                trigger = trigger.strip().lower()
                if not trigger:
                    continue
                if trigger.startswith("!"):
                    neg_words = set(re.findall(r"\w+", trigger[1:]))
                    if neg_words and neg_words <= text_words:
                        negated = True
                        break
                else:
                    trigger_words = set(re.findall(r"\w+", trigger))
                    if not trigger_words:
                        continue
                    overlap = len(trigger_words & text_words) / len(trigger_words)
                    best_overlap = max(best_overlap, overlap)

            if negated:
                sel().log_tool_invocation(
                    session_key="skills",
                    tool_name="skill_trigger",
                    tool_kind="permission",
                    outcome="not_triggered",
                    metadata={
                        "skill": name,
                        "reason": "negative_trigger",
                        "text_hash": text_hash,
                    },
                )
                continue

            outcome = (
                "triggered" if best_overlap >= _MIN_TRIGGER_OVERLAP else "not_triggered"
            )
            sel().log_tool_invocation(
                session_key="skills",
                tool_name="skill_trigger",
                tool_kind="permission",
                outcome=outcome,
                metadata={
                    "skill": name,
                    "overlap": round(best_overlap, 2),
                    "text_hash": text_hash,
                },
            )
            if best_overlap >= _MIN_TRIGGER_OVERLAP:
                scored.append((name, best_overlap))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored[: cfg.skills.max_triggered]]

    def get_surfaced_skills(self, text: str) -> list[str]:
        """Return skills for this turn via semantic ∪ keyword surfacing (#26).

        Embedding-preferred (reusing the active memory embedder), with the
        keyword trigger path as a cheap union member / no-embedder fallback;
        ranked by relevance then proven use_count (#25). Falls back to the pure
        keyword path :meth:`get_triggered_skills` on any error so a surfacing
        failure can never break a turn.
        """
        from gideon.core.config.loader import AppConfig

        try:
            from gideon.extensions.skills.surfacing import surface_skills

            cfg = AppConfig.load()
            skills = self.list_skills(with_usage=True)
            surfaced = surface_skills(
                text,
                skills,
                max_skills=cfg.skills.max_triggered,
                suppressed=_suppressed_producers(),
            )
            if surfaced:
                from gideon.security.sel import sel

                sel().log_tool_invocation(
                    session_key="skills",
                    tool_name="skill_surface",
                    tool_kind="permission",
                    outcome="surfaced",
                    metadata={
                        "skills": surfaced,
                        "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
                    },
                )
            return surfaced
        except Exception:
            logger.debug(
                "Semantic skill surfacing failed; using keyword path", exc_info=True
            )
            return self.get_triggered_skills(text)

    def get_context(self, *, agent: str | None = None) -> str:
        """Build skills context for prompt injection.

        Always-loaded skills: full content included.
        Other skills: summary with instruction to load via bash when needed.

        When ``agent`` is given AND that agent has an agent-local skills dir
        (skill-agent-local-tier), resolve through an agent-scoped view so the
        agent's own skills override same-named global/bundled ones for its turn.
        The agent-scoped loader is only built when the dir actually exists, so the
        common (no agent-local skills) path pays nothing."""
        if agent is not None and not self._scoped and not self._agent_dir:
            adir = agent_skills_dir(agent)
            if adir.is_dir() and any(adir.iterdir()):
                scoped = ProcedureLibrary(install_builtins=False, agent=agent)
                return scoped.get_context()
        always = self.get_always_skills()
        all_skills = self.list_skills()
        if not all_skills:
            return ""

        parts: list[str] = []

        for name in always:
            content = self.load_skill(name)
            if content:
                stripped = self.strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{stripped}")

        on_demand = [
            s
            for s in all_skills
            if s["name"] not in always and s.get("status") != "archived"
        ]
        if on_demand:
            summary_lines = [
                "## Available Skills",
                "",
                "If a user request relates to any skill below, load its full steps "
                "first with `skill_invoke{name}` before responding (this also records "
                "the skill as used). To run a skill's scripts, `cd` into its dir first.",
                "",
            ]
            for s in on_demand:
                summary_lines.append(
                    f"- **{s['name']}**: {s['description']} (dir: `{s['dir']}`)"
                )
            parts.append("\n".join(summary_lines))

        return "[Skills:]\n" + "\n\n---\n\n".join(parts) + "\n[End of skills]\n\n"

    @staticmethod
    def _parse_frontmatter(path: Path) -> dict[str, str]:
        """Parse YAML frontmatter from a markdown file (simple ``key: value``).

        Deliberately a line parser, not a YAML parser — see
        ``docs/reference/SKILL_FORMAT.md`` for the documented contract and its
        limits. What matters here is that an *unparseable* file must not look
        like a file with no metadata: returning ``{}`` for a skill that plainly
        has a name is the silent-failure mode this function is careful to avoid,
        because such a skill loads fine and then never matches anything.
        """
        content = path.read_text(encoding="utf-8-sig")
        return ProcedureLibrary._parse_frontmatter_text(content)

    @staticmethod
    def _parse_frontmatter_text(content: str) -> dict[str, str]:
        """Frontmatter parse over already-read text (see `_parse_frontmatter`)."""
        content = content.lstrip().replace("\r\n", "\n")
        if not content.startswith("---"):
            return {}
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return {}
        meta: dict[str, str] = {}
        pending_key = ""
        pending_sep = ""
        collected: list[str] = []

        block_scalars = ("|", ">", "|+", "|-", ">+", ">-")

        def _flush() -> None:
            if pending_key and collected:
                meta[pending_key] = pending_sep.join(collected)

        for raw in match.group(1).split("\n"):
            item = raw.strip()
            indented = bool(raw[:1].isspace())
            if pending_key and indented and item:
                if pending_sep == ", ":
                    if item.startswith("- "):
                        collected.append(item[2:].strip().strip("\"'"))
                        continue
                else:
                    collected.append(item)
                    continue
            if pending_key and pending_sep == ", " and item.startswith("- "):
                collected.append(item[2:].strip().strip("\"'"))
                continue
            if ":" in raw:
                _flush()
                pending_key, pending_sep, collected = "", "", []
                if indented:
                    continue
                key, value = raw.split(":", 1)
                key, value = key.strip(), value.strip().strip("\"'")
                if value in block_scalars:
                    pending_key, pending_sep = key, " "
                    meta[key] = ""
                    continue
                meta[key] = value
                if not value:
                    pending_key, pending_sep = key, ", "
        _flush()
        return meta

    @staticmethod
    def strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter from markdown.

        Tolerates the same BOM/leading-blank/CRLF variants as
        `_parse_frontmatter`. It must: this function's output goes into the model
        prompt, so failing to recognise a delimiter leaks the whole raw YAML
        block into context instead of dropping it.
        """
        content = content.lstrip("﻿").lstrip().replace("\r\n", "\n")
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content
