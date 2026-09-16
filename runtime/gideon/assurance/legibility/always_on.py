"""Always-on conventions inventory — the legibility surface for what EVERY session receives.

Gideon already has both halves of the always-on layer: skills carry an ``always: true``
frontmatter flag (``skills/loader.py:get_always_skills``, rendered into the prompt by
``ProcedureLibrary.get_context``) and a project's context dir supplies instruction docs that
``project_context`` inlines into every session bound to that project. This module adds only the
missing **legibility** surface. It deliberately does NOT introduce a parallel always-on "steering"
store: a second always-on mechanism competing with always:true-skills + project instructions would
violate one-path-per-concern, and the two would drift.

**The viewer renders slices of the session's own text, it does not re-walk the tree.**
That is the whole design constraint. A viewer that computed its own idea of the conventions would
be worse than no viewer, because it drifts silently while the user trusts it. So:

* The always-on skill tier is **parsed out of** ``ProcedureLibrary.get_context(agent=...)`` — the exact
  string a session receives. Nothing here iterates the skills dirs or re-reads frontmatter to decide
  what is always-on; if ``get_context`` stops emitting a skill, it leaves the viewer in the same
  breath.
* The project tier comes from ``project_context.inlined_context_files`` — the repo's own answer to
  "which context files did the project block ACTUALLY inline", CONTENT-based rather than name-based
  — plus the same ``read_overview``/``read_ledger`` readers the session preamble calls.

**Why this module does not assemble a session prompt at runtime.** The full composer
(``PromptAssembler.build_session_context``) is not a pure read: it routes the skill block through the
budgeted ambient allocator, which calls ``_record_ambient_measurements`` and persists a
budget-utilization sample plus (on a daily cadence) an ablation sweep. A read-only viewer that
mutated learning telemetry on every page open would be a defect. So the runtime path reads the
producers, and ``tests/test_legibility_always_on.py`` closes the remaining gap by asserting every
item this module reports is a substring of a really-assembled ``build_session_context`` output —
with a vacuity floor, so an empty-vs-empty match can never pass for agreement.

Provenance is reported as ``scope`` (global vs project — what the plan asks the user to see) plus a
finer ``source`` (bundled / user / agent:<name> / project:<id>).

Editability follows the underlying contract rather than convenience: the project **overview** is
current state, revised in place, so it is editable; the **ledgers** are append-only history, so they
are read-only here. Rewriting history through a viewer would silently destroy the one tier whose
value is that it is not rewritten.

List responses carry a credential-redacted ``preview``; the editor round-trip (single-doc GET and
PUT) carries the body verbatim, because redacting a body the user is about to save back would write
the redaction over their real text.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from gideon.cognition import project_context

logger = logging.getLogger(__name__)

#: The literal framing ``ProcedureLibrary.get_context`` wraps its block in.
SKILLS_OPEN = "[Skills:]"
SKILLS_CLOSE = "[End of skills]"
SKILL_HEADER_RE = re.compile(r"(?m)^### Skill: (.+)$")
INDEX_HEADER = "## Available Skills"
PART_SEPARATOR = "\n\n---\n\n"

PREVIEW_CHARS = 400

_LEDGER_BY_FILE = {
    filename: kind for kind, filename in project_context.LEDGER_FILES.items()
}


@dataclass
class AlwaysOnItem:
    """One always-on convention in effect right now."""

    id: str
    kind: str
    name: str
    scope: str
    source: str
    body: str
    path: str = ""
    editable: bool = False
    read_only_reason: str = ""
    project_id: str = ""

    def to_dict(self, *, include_body: bool = False) -> dict[str, Any]:
        from gideon.security.security import redact_credentials

        preview, _found = redact_credentials(self.body[:PREVIEW_CHARS])
        row: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "scope": self.scope,
            "source": self.source,
            "path": self.path,
            "chars": len(self.body),
            "editable": self.editable,
            "read_only_reason": self.read_only_reason,
            "project_id": self.project_id,
            "preview": preview,
        }
        if include_body:
            row["body"] = self.body
        return row


@dataclass
class AlwaysOnInventory:
    """Everything injected into every session, with provenance."""

    items: list[AlwaysOnItem] = field(default_factory=list)
    project_id: str = ""
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        skills = [i for i in self.items if i.kind == "always_skill"]
        project = [i for i in self.items if i.kind == "project_instruction"]
        return {
            "items": [i.to_dict() for i in self.items],
            "project_id": self.project_id,
            "counts": {
                "total": len(self.items),
                "always_skills": len(skills),
                "project_instructions": len(project),
            },
            "always_skill_mechanism": "always: true in a skill's SKILL.md frontmatter",
        }

    def by_id(self, item_id: str) -> AlwaysOnItem | None:
        for item in self.items:
            if item.id == item_id:
                return item
        return None


def parse_always_skill_parts(context_text: str) -> list[tuple[str, str]]:
    """Split ``ProcedureLibrary.get_context()`` output into ``(name, body)`` for the always-on tier.

    Parsing the producer's own string is the point — see the module docstring. The on-demand
    index part (``## Available Skills``) is excluded: those skills are loaded on request, not
    injected, so listing them here would misreport what a session receives.
    """
    text = context_text or ""
    if not text.strip():
        return []
    if text.startswith(SKILLS_OPEN):
        text = text[len(SKILLS_OPEN) :]
    close = text.find(SKILLS_CLOSE)
    if close != -1:
        text = text[:close]
    cut = text.find(PART_SEPARATOR + INDEX_HEADER)
    if cut == -1:
        cut = text.find("\n" + INDEX_HEADER)
    if cut != -1:
        text = text[:cut]

    matches = list(SKILL_HEADER_RE.finditer(text))
    parts: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        stop = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:stop]
        lines = body.rstrip().split("\n")
        while lines and lines[-1].strip() in ("", "---"):
            lines.pop()
        parts.append((name, "\n".join(lines).strip()))
    return parts


def _skill_provenance(path: str, *, agent_local: bool, agent: str | None) -> str:
    if agent_local and agent:
        return f"agent:{agent}"
    normalized = (path or "").replace(os.sep, "/")
    if "/gideon/skills/bundled/" in normalized:
        return "bundled"
    return "user"


def collect_always_on(
    *,
    project_id: str = "",
    agent: str | None = None,
    skills_loader: Any = None,
) -> AlwaysOnInventory:
    """Everything a session receives unconditionally, sliced out of the session's own producers.

    ``skills_loader`` is injectable for tests only; production passes nothing and gets a plain
    :class:`~gideon.extensions.skills.loader.ProcedureLibrary`, i.e. the same class the composer holds.
    """
    inventory = AlwaysOnInventory(project_id=project_id)

    skills_context = ""
    try:
        if skills_loader is None:
            from gideon.extensions.skills.loader import ProcedureLibrary

            skills_loader = ProcedureLibrary()
        skills_context = skills_loader.get_context(agent=agent) or ""
    except Exception:
        logger.debug("always-on: skills context read failed", exc_info=True)
        skills_context = ""
    inventory.sources["skills_context"] = skills_context

    meta_by_name: dict[str, dict] = {}
    try:
        for row in skills_loader.list_skills() if skills_loader is not None else []:
            meta_by_name[str(row.get("name", ""))] = row
            meta_by_name.setdefault(str(row.get("key", "")), row)
    except Exception:
        logger.debug("always-on: skill metadata read failed", exc_info=True)

    for name, body in parse_always_skill_parts(skills_context):
        meta = meta_by_name.get(name, {})
        path = str(meta.get("path", "") or "")
        inventory.items.append(
            AlwaysOnItem(
                id=f"always_skill:{name}",
                kind="always_skill",
                name=name,
                scope="global",
                source=_skill_provenance(
                    path, agent_local=bool(meta.get("agent_local")), agent=agent
                ),
                body=body,
                path=path,
                editable=False,
                read_only_reason=(
                    "Edit the skill in the Skills area — a skill's body is its SKILL.md, "
                    "and it carries frontmatter this viewer must not rewrite."
                ),
            )
        )

    if project_id:
        try:
            inlined = project_context.inlined_context_files(project_id)
        except Exception:
            logger.debug("always-on: inlined context lookup failed", exc_info=True)
            inlined = frozenset()
        context_dir = project_context._context_dir(project_id)
        for filename in sorted(inlined):
            if filename == project_context.OVERVIEW_FILE:
                body = project_context.read_overview(project_id)
                editable, reason = True, ""
            else:
                kind = _LEDGER_BY_FILE.get(filename, "")
                entries = project_context.read_ledger(project_id, kind) if kind else []
                body = "\n".join(entries)
                editable = False
                reason = (
                    "Append-only history — a ledger records what happened. Rewriting it "
                    "through a viewer would destroy the one tier whose value is that it "
                    "is not rewritten."
                )
            inventory.items.append(
                AlwaysOnItem(
                    id=f"project_instruction:{filename}",
                    kind="project_instruction",
                    name=filename,
                    scope="project",
                    source=f"project:{project_id}",
                    body=body,
                    path=str(context_dir / filename) if context_dir is not None else "",
                    editable=editable,
                    read_only_reason=reason,
                    project_id=project_id,
                )
            )
        try:
            from gideon.interfaces.dashboard.chat_utils import _project_context_preamble

            inventory.sources["project_preamble"] = (
                _project_context_preamble(project_id) or ""
            )
        except Exception:
            logger.debug("always-on: project preamble read failed", exc_info=True)
            inventory.sources["project_preamble"] = ""

    return inventory


class InstructionWriteError(Exception):
    """A project-instruction write was refused or failed. Carries a user-facing reason.

    Raised rather than returned because the underlying ``write_overview`` reports failure as a
    bare ``False``: a caller that ignored it would render "Saved" over an edit that never
    landed. Silently discarding a user's text is the exact failure this atom's
    "round-trips safely" clause is about.
    """

    def __init__(self, reason: str, *, status: int = 500):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def read_instruction(
    item_id: str, *, project_id: str, agent: str | None = None
) -> AlwaysOnItem:
    """The verbatim body of one always-on item (editor round-trip — no redaction)."""
    inventory = collect_always_on(project_id=project_id, agent=agent)
    item = inventory.by_id(item_id)
    if item is None:
        raise InstructionWriteError(
            f"No always-on item {item_id!r} in effect", status=404
        )
    return item


def write_instruction(item_id: str, body: str, *, project_id: str) -> AlwaysOnItem:
    """Replace an editable project-instruction doc, atomically, and return the re-read item.

    Security discipline mirrored from the skills/instruction editors: the write is contained to
    the project's own context dir, the leaf must not be a symlink (a symlink leaf would let an
    edit land outside the trust base), and the write is atomic so a failure cannot leave a torn
    doc that reads as complete. The return value is a RE-READ, not the submitted text — the
    caller should render what the store now holds, not what it hoped it wrote.
    """
    if not project_id:
        raise InstructionWriteError(
            "project_id is required to edit a project instruction", status=400
        )
    inventory = collect_always_on(project_id=project_id)
    item = inventory.by_id(item_id)
    if item is None:
        if item_id != f"project_instruction:{project_context.OVERVIEW_FILE}":
            raise InstructionWriteError(
                f"No editable always-on item {item_id!r}", status=404
            )
    elif not item.editable:
        raise InstructionWriteError(item.read_only_reason or "Not editable", status=403)

    context_dir = project_context._context_dir(project_id)
    if context_dir is None:
        raise InstructionWriteError(f"Unknown project {project_id!r}", status=404)
    target = context_dir / project_context.OVERVIEW_FILE
    try:
        resolved_dir = context_dir.resolve()
        if target.exists() and target.resolve().parent != resolved_dir:
            raise InstructionWriteError(
                "Refusing to write outside the project's context dir", status=403
            )
    except InstructionWriteError:
        raise
    except OSError as exc:
        raise InstructionWriteError(
            f"Could not resolve the instruction path: {exc}"
        ) from exc
    if target.is_symlink():
        raise InstructionWriteError(
            "Refusing to write through a symlinked instruction file", status=403
        )

    if not project_context.write_overview(project_id, body):
        raise InstructionWriteError(
            "The instruction could not be written — your edit was NOT saved. "
            "Check the project's context directory permissions."
        )
    refreshed = collect_always_on(project_id=project_id).by_id(
        f"project_instruction:{project_context.OVERVIEW_FILE}"
    )
    if refreshed is None:
        return AlwaysOnItem(
            id=f"project_instruction:{project_context.OVERVIEW_FILE}",
            kind="project_instruction",
            name=project_context.OVERVIEW_FILE,
            scope="project",
            source=f"project:{project_id}",
            body="",
            path=str(target),
            editable=True,
            project_id=project_id,
        )
    return refreshed


__all__ = [
    "AlwaysOnInventory",
    "AlwaysOnItem",
    "InstructionWriteError",
    "collect_always_on",
    "parse_always_skill_parts",
    "read_instruction",
    "write_instruction",
]
