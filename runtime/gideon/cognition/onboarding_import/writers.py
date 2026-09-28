"""Per-category writers — the only code in the import path that touches our home.

One writer per :class:`~.model.ImportCategory`, dispatched through an exhaustive
``_WRITERS`` map (an unmapped category raises rather than silently importing
nothing). Every writer obeys the same two rules:

- **The destination is the source of truth.** Before writing, the writer asks the
  destination whether this thing is already there. Identical → ``existing``.
  Present and DIFFERENT → ``conflict``: the existing thing is left byte-identical
  and the conflict is reported for review. No writer resolves a conflict by
  overwriting the user's state.
- **The import ledger answers "ours or theirs", not "is it there".**
  ``onboarding/import_state.json`` records the fingerprints WE wrote, which is how
  a skill dir we installed (``existing``) is told apart from a skill of the same
  name the user wrote themselves (``conflict``). Deriving presence from the ledger
  instead of the destination would report ``existing`` for something a user had
  since deleted.

Destinations
============

===================  ==========================================================
``instructions``     ``workspace/memory/imported/<source>/<key>.md`` + a memory
``memories``         record through the filesystem memory provider
``mcp_servers``      ``mcp.json`` → ``mcpServers`` (the user-owned override file
                     ``agent.py`` already merges at highest priority)
``skills``           ``skills/imported/<source>/<name>/`` via ``install_scanned``
                     — the same supply-chain gate as a Store skill
``settings``         ``onboarding/staged/<source>-<key>.json`` — a REVIEW QUEUE.
                     Foreign settings never reach live config, so for this
                     category ``imported`` means "staged for a human", which is
                     that category's destination.
===================  ==========================================================
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import replace
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.cognition.onboarding_import.destination_commit import (
    DocumentCommit,
    ImportBatch,
    ImportLedger,
    SkillCommit,
    WriteReceipt,
)
from gideon.cognition.onboarding_import.floors import refuses
from gideon.cognition.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ItemState,
    Plan,
    ImportReport,
    WriteOutcome,
    WriteResult,
    withheld_notes,
)
from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.extensions.skills.marketplace import (
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    read_skill_file_entry,
)


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_STATE_REL = Path("onboarding") / "import_state.json"
_STAGED_REL = Path("onboarding") / "staged"
_IMPORTED_DIRNAME = "imported"
_SUMMARY_CHARS = 220


def state_path() -> Path:
    return config_dir() / _STATE_REL


def _load_state() -> dict[str, Any]:
    return ImportLedger.load(sys.modules[__name__])


def _ours(fingerprint: str) -> bool:
    """True when THIS importer wrote the thing at that fingerprint."""
    return fingerprint in _load_state()["items"]


def _record(item: ImportItem, destination: str) -> None:
    """Record an ``imported`` outcome. Only imports are recorded: recording a
    conflict would make the next run report ``existing`` for something we never
    wrote."""
    ImportLedger.record(sys.modules[__name__], item, destination)


def imported_fingerprints() -> set[str]:
    """Everything this importer has written — what the onboarding step shows as
    already-imported on re-entry."""
    return set(_load_state()["items"])


def _audit(
    operation: str, outcome: str, *, resources: str = "", error: str = ""
) -> None:
    """One SEL event per write (best-effort; audit never breaks an import).

    ``resources`` carries the source/category/key — never a value, so an audit log
    can't become the place a skipped secret leaks.
    """
    WriteReceipt.audit(sys.modules[__name__], operation, outcome, resources, error)


def _slug(text: str) -> str:
    fragments = re.split(r"[^A-Za-z0-9._-]+", text.strip())
    slug = "-".join(fragments).strip("-._")
    return slug if slug else "item"


def _result(
    item: ImportItem,
    outcome: WriteOutcome,
    destination: str = "",
    detail: str = "",
) -> WriteResult:
    return WriteReceipt.create(
        sys.modules[__name__], item, outcome, destination, detail
    )


def _rel_to_home(path: Path) -> str:
    try:
        return str(path.relative_to(config_dir()))
    except ValueError:  # pragma: no cover - a destination is always under the home
        return str(path)


def _memory_doc_path(item: ImportItem) -> Path:
    from gideon.cognition.memory import memory_dir

    name = _slug(item.key)
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    return memory_dir() / _IMPORTED_DIRNAME / _slug(item.source) / name


def _write_memory(item: ImportItem) -> WriteResult:
    """Write the redacted doc under the memory dir and add one memory record.

    The document keeps full fidelity on disk; the record is what makes it a
    *memory* (searchable through the store's own projection) rather than a loose
    file. Both are idempotent: an identical doc is a no-op, and the provider's
    append dedupes the record line.
    """
    return DocumentCommit.memory(sys.modules[__name__], item)


def mcp_config_path() -> Path:
    return config_dir() / "mcp.json"


def _write_mcp_server(item: ImportItem) -> WriteResult:
    from gideon.core.config.secret_refs import SecretOwner, purge_unused
    from gideon.extensions.providers.mcp_instances import store_server_credentials

    safe_item = replace(item, payload=store_server_credentials(item.key, item.payload))
    result = DocumentCommit.server(sys.modules[__name__], safe_item)
    try:
        config = json.loads(mcp_config_path().read_text(encoding="utf-8"))
        stored = (config.get("mcpServers") or {}).get(item.key, {})
    except (OSError, ValueError):
        stored = {}
    retained = {
        **(stored.get("env") if isinstance(stored, dict) and isinstance(stored.get("env"), dict) else {}),
        **(stored.get("headers") if isinstance(stored, dict) and isinstance(stored.get("headers"), dict) else {}),
        **(stored.get("oauth") if isinstance(stored, dict) and isinstance(stored.get("oauth"), dict) else {}),
    }
    purge_unused(SecretOwner("MCP", item.key), retained)
    return result


def imported_skills_dir(source: str) -> Path:
    from gideon.extensions.skills.loader import skills_dir

    return skills_dir() / _IMPORTED_DIRNAME / _slug(source)


def _write_skill(item: ImportItem) -> WriteResult:
    """Install a foreign skill through the shared supply-chain gate.

    Namespaced under ``imported/<source>/`` so a re-import or a removal is scoped
    and reversible, and routed through ``install_scanned`` so a foreign skill gets
    exactly the quarantine → scan → commit treatment a Store skill gets. A
    DANGEROUS verdict is ``rejected``, never force-installed.
    """
    return SkillCommit.install(sys.modules[__name__], item)


def _imported_skills_marketplace_files(skill_dir: Path) -> list[dict[str, Any]]:
    return SkillCommit.files(sys.modules[__name__], skill_dir)


class _ImportedSkillsMarketplace(SkillsMarketplace):
    """A transient, single-directory skills source rooted at a foreign skill dir.

    Not registered in the shared registry — another tool's skills dir is not a
    marketplace. It exists only so an imported skill flows through the exact same
    :func:`install_scanned` gate (quarantine → scan → commit → lock) as any other
    install, at the ``community`` trust tier (foreign, unsigned content).
    """

    def __init__(self, skill_dir: Path) -> None:
        self._skill_dir = Path(skill_dir)

    @property
    def marketplace_type(self) -> str:
        return "onboarding_import"

    @property
    def trust_tier(self) -> str:
        return "community"

    def search(
        self, query: str, limit: int = 20
    ) -> list[SkillEntry]:  # pragma: no cover
        return []

    def fetch(self, skill_id: str) -> SkillDetail:
        return SkillCommit.detail(sys.modules[__name__], self, skill_id)


def staged_settings_path(source: str, key: str) -> Path:
    return config_dir() / _STAGED_REL / f"{_slug(source)}-{_slug(key)}.json"


def _write_settings(item: ImportItem) -> WriteResult:
    """Stage foreign settings for human review. Never merge them into config.

    Another tool's settings keys are not ours, so an automatic merge could only
    guess. The destination for this category IS the review queue: ``imported``
    means "staged", and a differing staged file is a ``conflict`` rather than an
    overwrite.
    """
    return DocumentCommit.settings(sys.modules[__name__], item)


def _write_conversation(item: ImportItem) -> WriteResult:
    from gideon.cognition.onboarding_import.transcripts import write_transcript

    return write_transcript(item, sys.modules[__name__])


_WRITERS: dict[ImportCategory, Callable[[ImportItem], WriteResult]] = {
    ImportCategory.INSTRUCTIONS: _write_memory,
    ImportCategory.MEMORIES: _write_memory,
    ImportCategory.MCP_SERVERS: _write_mcp_server,
    ImportCategory.SKILLS: _write_skill,
    ImportCategory.SETTINGS: _write_settings,
    ImportCategory.CONVERSATIONS: _write_conversation,
}


def write_item(item: ImportItem) -> WriteResult:
    plan = plan_item(item)
    if plan.state is not ItemState.NEW:
        return _result(item, WriteOutcome(plan.state.value), plan.destination, plan.detail)
    return ImportBatch.dispatch(sys.modules[__name__], item)


def write_items(items: list[ImportItem]) -> list[WriteResult]:
    return ImportBatch.write(sys.modules[__name__], items)


def import_report(items: list[ImportItem], *, secrets_skipped: int = 0) -> ImportReport:
    """Write every item and report outcomes plus what was withheld."""
    report = ImportBatch.report(sys.modules[__name__], items, secrets_skipped)
    report.notes.extend(
        withheld_notes(secrets_skipped=secrets_skipped, redactions=report.redactions)
    )
    return report


def _file_plan(path: Path, content: str) -> Plan:
    destination = _rel_to_home(path)
    if not path.exists():
        return Plan(ItemState.NEW, destination)
    try:
        same = path.is_file() and path.read_text(encoding="utf-8") == content
    except OSError:
        same = False
    return Plan(ItemState.EXISTING if same else ItemState.CONFLICT, destination,
                "already imported, unchanged" if same else "different content is already here; the existing item is kept")


def plan_item(item: ImportItem) -> Plan:
    if item.category in (ImportCategory.INSTRUCTIONS, ImportCategory.MEMORIES):
        return _file_plan(_memory_doc_path(item), item.text + ("" if item.text.endswith("\n") else "\n"))
    if item.category is ImportCategory.SETTINGS:
        content = json.dumps({"source": item.source, "key": item.key, "settings": item.payload}, indent=2, sort_keys=True) + "\n"
        return _file_plan(staged_settings_path(item.source, item.key), content)
    if item.category is ImportCategory.MCP_SERVERS:
        destination = f"{_rel_to_home(mcp_config_path())}#mcpServers.{item.key}"
        try:
            data = json.loads(mcp_config_path().read_text(encoding="utf-8")) if mcp_config_path().exists() else {}
            if not isinstance(data, dict):
                raise ValueError("invalid MCP configuration")
        except (OSError, ValueError):
            return Plan(ItemState.CONFLICT, destination, "the existing mcp.json could not be parsed; it is kept")
        servers = data.get("mcpServers")
        existing = servers.get(item.key) if isinstance(servers, dict) else None
        if existing is None:
            return Plan(ItemState.NEW, destination)
        try:
            from gideon.extensions.providers import mcp_instances
            resolver = getattr(mcp_instances, "resolve_server_credentials", None)
            same = (resolver(item.key, existing) == resolver(item.key, item.payload)) if resolver else existing == item.payload
        except (ValueError, PermissionError):
            same = False
        return Plan(ItemState.EXISTING if same else ItemState.CONFLICT, destination,
                    "already configured identically" if same else "an MCP server of this name is configured differently; it is kept")
    if item.category is ImportCategory.SKILLS:
        target = imported_skills_dir(item.source) / item.key
        destination = _rel_to_home(target)
        source = Path(item.path) if item.path else None
        if source is None or not source.is_dir() or refuses(source):
            return Plan(ItemState.REJECTED, destination, "source skill directory is missing or sensitive")
        if target.exists():
            ours = _ours(item.fingerprint)
            return Plan(ItemState.EXISTING if ours else ItemState.CONFLICT, destination,
                        "already imported" if ours else "a skill of this name is already here; it is kept")
        from gideon.security.supply_chain import TrustTier, Verdict, scan_dir
        verdict = scan_dir(source, TrustTier.COMMUNITY).verdict
        if verdict in (Verdict.WARNING, Verdict.DANGEROUS):
            return Plan(ItemState.REJECTED, destination, "the skill supply-chain scan requires review before import")
        return Plan(ItemState.NEW, destination)
    if item.category is ImportCategory.CONVERSATIONS:
        from gideon.cognition.history import ConversationLog
        rows = item.payload.get("messages")
        if not isinstance(rows, list) or not rows:
            return Plan(ItemState.REJECTED, "", "conversation has no readable messages")
        path = ConversationLog()._path(f"imported_{item.source}_{item.fingerprint}")
        destination = _rel_to_home(path)
        if not path.exists():
            return Plan(ItemState.NEW, destination)
        try:
            with path.open(encoding="utf-8") as handle:
                metadata = json.loads(handle.readline())
            same = metadata.get("import_source") == item.source and metadata.get("import_key") == item.key
        except (OSError, ValueError, AttributeError):
            same = False
        return Plan(ItemState.EXISTING if same else ItemState.CONFLICT, destination,
                    "already imported" if same else "a conversation is already here; it is kept")
    raise KeyError(f"no planner for import category {item.category!r}")
