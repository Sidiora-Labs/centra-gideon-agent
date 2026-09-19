"""Project context artifacts, append-only ledgers and attributed handoff projections."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gideon.automation.workflows.containers import LEDGERS, ledger_entry, project_block
from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)

OVERVIEW_FILE = "overview.md"

LEDGER_FILES = {
    "decisions": "decisions.md",
    "fog": "not-yet-specified.md",
    "out_of_scope": "out-of-scope.md",
}

MAX_OVERVIEW_CHARS = 8000

MAX_LEDGER_LINE = 400

#: The bullet a ledger entry is written with — and the only line shape a read admits. A heading
#: or prose a person left in the file is context around the entries, not an entry.
_BULLET = "- "


def _render_entry(entry: dict[str, Any]) -> str:
    fragments = [_BULLET, entry["text"]]
    for name, pattern in (("reason", " — _{}_"), ("link", " ([run]({}))")):
        value = entry.get(name)
        if value:
            fragments.append(pattern.format(value))
    return "".join(fragments)


def _ledger_header(kind: str) -> str:
    """The title + purpose block a ledger file gets when it is first created."""
    return f"# {kind.replace('_', ' ').title()}\n\n{LEDGERS[kind]}\n\n"


class _ProjectContext:
    """A resolved project's context directory, and every read/write performed over it."""

    __slots__ = ("directory", "project", "project_id")

    def __init__(self, project_id: str, directory: Path | None, project: Any) -> None:
        self.project_id = project_id
        self.directory = directory
        self.project = project

    @classmethod
    def resolve(cls, project_id: str) -> "_ProjectContext | None":
        """The handle for ``project_id``, or ``None`` when the project does not exist."""
        if not project_id:
            return None
        from gideon.engine.tasks.hierarchy import HierarchyStore

        store = HierarchyStore()
        project = store.get_project(project_id)
        if project is None:
            return None
        try:
            directory = store.context_dir(project_id)
        except Exception:
            logger.debug("context dir lookup failed for %s", project_id, exc_info=True)
            directory = None
        return cls(project_id, directory, project)

    def artifact(self, filename: str) -> Path | None:
        """A context artifact's path, or None when the project's dir never resolved."""
        if self.directory is None:
            return None
        return self.directory / filename

    def _read(self, filename: str) -> str | None:
        """The artifact's text, or ``None`` when it is absent or unreadable."""
        target = self.artifact(filename)
        if target is None:
            return None
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return None
        except Exception:
            logger.debug(
                "context read failed for %s/%s",
                self.project_id,
                filename,
                exc_info=True,
            )
            return None

    def read_overview(self) -> str:
        return (self._read(OVERVIEW_FILE) or "").strip()

    def write_overview(self, text: str) -> bool:
        target = self.artifact(OVERVIEW_FILE)
        if target is None:
            return False
        body = (text or "").strip()[:MAX_OVERVIEW_CHARS]
        try:
            atomic_write(target, body)
            return True
        except Exception:
            logger.debug("overview write failed for %s", self.project_id, exc_info=True)
            return False

    def append_ledger(
        self, kind: str, text: str, *, link: str = "", reason: str = ""
    ) -> bool:
        filename = LEDGER_FILES.get(kind)
        if filename is None:
            return False
        target = self.artifact(filename)
        if target is None:
            return False
        try:
            entry = ledger_entry(
                kind, (text or "")[:MAX_LEDGER_LINE], link=link, reason=reason
            )
        except ValueError:
            return False
        if not entry["text"]:
            return False
        try:
            header = "" if target.exists() else _ledger_header(kind)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(header + _render_entry(entry) + "\n")
            return True
        except Exception:
            logger.debug(
                "ledger append failed for %s/%s", self.project_id, kind, exc_info=True
            )
            return False

    def read_ledger(self, kind: str) -> list[str]:
        filename = LEDGER_FILES.get(kind)
        if filename is None:
            return []
        stored = self._read(filename)
        if stored is None:
            return []
        return [
            line[len(_BULLET) :].strip()
            for line in stored.splitlines()
            if line.startswith(_BULLET)
        ]


def _open(project_id: str) -> "_ProjectContext | None":
    """Resolve tolerantly — an unresolvable project has nothing to read or write."""
    try:
        return _ProjectContext.resolve(project_id)
    except Exception:
        logger.debug("context dir lookup failed for %s", project_id, exc_info=True)
        return None


def _context_dir(project_id: str) -> Path | None:
    """The project's context dir, or None when the project does not resolve."""
    view = _open(project_id)
    return view.directory if view is not None else None


def inlined_context_files(project_id: str) -> frozenset[str]:
    candidates: list[tuple[str, object]] = [(OVERVIEW_FILE, read_overview(project_id))]
    candidates.extend(
        (filename, read_ledger(project_id, kind))
        for kind, filename in LEDGER_FILES.items()
    )
    return frozenset(filename for filename, content in candidates if content)


def read_overview(project_id: str) -> str:
    """The living overview, or "" when there is none. Never raises."""
    view = _open(project_id)
    return view.read_overview() if view is not None else ""


def write_overview(project_id: str, text: str) -> bool:
    """Replace the overview. In place, atomically."""
    view = _open(project_id)
    return view.write_overview(text) if view is not None else False


def append_ledger(
    project_id: str, kind: str, text: str, *, link: str = "", reason: str = ""
) -> bool:
    """Append one line to a wayfinder ledger. Append-only, by construction."""
    view = _open(project_id)
    if view is None:
        return False
    return view.append_ledger(kind, text, link=link, reason=reason)


def read_ledger(project_id: str, kind: str) -> list[str]:
    """A ledger's lines, newest last (append order). "" -safe and never raises."""
    view = _open(project_id)
    return view.read_ledger(kind) if view is not None else []


def context_block(project_id: str) -> str:
    """The project block for any session inside the project."""
    if not project_id:
        return ""
    try:
        view = _ProjectContext.resolve(project_id)
        if view is None:
            return ""
        return project_block(
            brief=str(getattr(view.project, "brief", "") or ""),
            overview=view.read_overview(),
            instructions=str(
                getattr(view.project, "agent_instructions_template", "") or ""
            ),
        )
    except Exception:
        logger.debug("project context block skipped for %s", project_id, exc_info=True)
        return ""


def handoff_snapshot(project_id: str) -> dict[str, Any]:
    fog = read_ledger(project_id, "fog")
    snapshot: dict[str, Any] = {}
    fields = (
        (
            "focus",
            lambda: read_overview(project_id)[:600],
            "overview.md (revised as runs complete)",
        ),
        ("open_questions", lambda: fog, "not-yet-specified ledger"),
        (
            "decisions",
            lambda: read_ledger(project_id, "decisions")[-10:],
            "decisions ledger (append-only, newest last)",
        ),
        (
            "out_of_scope",
            lambda: read_ledger(project_id, "out_of_scope"),
            "out-of-scope ledger",
        ),
    )
    for name, read, source in fields:
        snapshot[name] = read()
        snapshot[name + "_source"] = source
    snapshot.update(
        next_actions=[],
        next_actions_note="supplied by the caller from live run/task state",
    )
    return snapshot
