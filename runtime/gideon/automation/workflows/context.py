"Bounded iteration handoffs, carryover facts and settled decisions."

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_BUCKET_ITEMS = 50

MAX_HANDOFF_FIELD = 2000

SESSION_FRESH = "fresh"
SESSION_CONTINUOUS = "continuous"
SESSION_POLICIES = (SESSION_FRESH, SESSION_CONTINUOUS)


def session_policy(node_config: dict[str, Any] | None) -> str:
    normalized = str((node_config or {}).get("session", "") or "").strip().lower()
    return next(
        (policy for policy in SESSION_POLICIES if policy == normalized), SESSION_FRESH
    )


@dataclass
class Handoff:
    """What one iteration tells the next (WF2-R6).

    The four fields are not arbitrary — each answers a question the next iteration would otherwise
    have to re-derive from a transcript it no longer has:

    * `verified_state` — what is known to be TRUE, having been checked. The expensive part to
      rebuild, and the part a summary renders as plausible-but-unchecked.
    * `changes` — what this iteration actually altered. Without it the next iteration cannot tell
      its own effects from the world's.
    * `unverified` — what is broken, assumed, or was not reached. The field that stops a loop
      reporting success over an unexamined gap.
    * `next_action` — what to do first. A handoff that describes state without naming the next move
      makes the reader re-plan from scratch.
    """

    verified_state: str = ""
    changes: str = ""
    unverified: str = ""
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {name: _clip(getattr(self, name)) for name, _ in _HANDOFF_FIELDS}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Handoff:
        document = d or {}
        return cls(
            **{name: str(document.get(name, "") or "") for name, _ in _HANDOFF_FIELDS}
        )

    @property
    def empty(self) -> bool:
        return all(not getattr(self, name) for name, _ in _HANDOFF_FIELDS)

    def render(self) -> str:
        return "\n\n".join(
            label + "\n" + _clip(getattr(self, name))
            for name, label in _HANDOFF_FIELDS
            if getattr(self, name)
        )


@dataclass
class Carryover:
    """Typed facts that survive a session reset (WF2-R6).

    Structure, not narrative — that is the entire point. A prose handoff summarized twice loses the
    line spans and the file names; a list of `{path, lines}` does not, because there is nothing in
    it for a summarizer to compress away.

    Bounded and deduped. An unbounded bucket is a transcript with extra steps, and it would
    reintroduce the context exhaustion this mechanism exists to prevent.
    """

    files_touched: list[dict[str, Any]] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    spawned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {name: list(getattr(self, name)) for name in _CARRYOVER_FIELDS}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Carryover:
        document = d or {}
        fields: dict = {
            name: list(map(str, document.get(name) or []))
            for name in ("verified", "spawned")
        }
        fields["files_touched"] = [
            entry
            for entry in (document.get("files_touched") or [])
            if isinstance(entry, dict)
        ]
        return cls(**fields)

    @property
    def empty(self) -> bool:
        return all(not getattr(self, name) for name in _CARRYOVER_FIELDS)

    def merge(self, other: Carryover) -> Carryover:
        combined: dict = {}
        for name in _CARRYOVER_FIELDS:
            entries = getattr(self, name) + getattr(other, name)
            unique = (
                _dedupe_dicts(entries, key="path")
                if name == "files_touched"
                else _dedupe(entries)
            )
            combined[name] = unique[-MAX_BUCKET_ITEMS:]
        return Carryover(**combined)

    def render(self) -> str:
        blocks = []
        for name, label, format_entries in (
            (
                "files_touched",
                "Files already touched: ",
                lambda values: ", ".join(map(_file_label, values)),
            ),
            (
                "verified",
                "Already verified:\n",
                lambda values: "\n".join("- " + value for value in values),
            ),
            ("spawned", "Children already spawned: ", lambda values: ", ".join(values)),
        ):
            entries = getattr(self, name)
            if entries:
                blocks.append(label + format_entries(entries[-12:]))
        return "\n\n".join(blocks)


@dataclass
class Decision:
    """A settled choice and WHY (WF2-R6).

    `rejected` is the load-bearing field. Compaction keeps "we used Postgres" and drops "we
    rejected SQLite because the write concurrency did not fit", so a resumed or forked run
    re-proposes SQLite and nothing in its context says that was already considered. A decision
    record with no rejected alternatives is a note; with them it is a constraint.
    """

    choice: str = ""
    reason: str = ""
    rejected: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict = {
            name: _clip(getattr(self, name)) for name in ("choice", "reason")
        }
        for field_name, key in (
            ("rejected", "rejected_alternatives"),
            ("constraints", "constraints"),
        ):
            result[key] = [
                _clip(str(value), 300) for value in getattr(self, field_name)[:12]
            ]
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Decision:
        document = d or {}
        fields: dict = {
            name: str(document.get(name, "") or "") for name in ("choice", "reason")
        }
        fields.update(
            rejected=list(
                map(
                    str,
                    document.get("rejected_alternatives")
                    or document.get("rejected")
                    or [],
                )
            ),
            constraints=list(map(str, document.get("constraints") or [])),
        )
        return cls(**fields)

    @property
    def empty(self) -> bool:
        return not self.choice.strip()

    def render(self) -> str:
        result = ["Decided: " + _clip(self.choice)]
        if self.reason:
            result.append("  because " + _clip(self.reason, 400))
        for name in ("rejected", "constraints"):
            entries = getattr(self, name)
            if entries:
                result.append("  " + name + ": " + "; ".join(map(str, entries[:6])))
        return "\n".join(result)


def render_context(
    *,
    handoff: Handoff | None = None,
    carryover: Carryover | None = None,
    decisions: list[Decision] | None = None,
) -> str:
    selected = [decision for decision in (decisions or []) if not decision.empty][-6:]
    blocks = []
    if selected:
        blocks.append(
            "[SETTLED DECISIONS — do not re-litigate these]\n"
            + "\n\n".join(decision.render() for decision in selected)
        )
    for record, title in (
        (carryover, "[CARRIED OVER]"),
        (handoff, "[HANDOFF FROM THE PREVIOUS ITERATION]"),
    ):
        if record is not None and not record.empty:
            blocks.append(title + "\n" + record.render())
    return "\n\n".join(blocks)


def _clip(text: str, limit: int = MAX_HANDOFF_FIELD) -> str:
    value = str(text or "").strip()
    return value[: limit - 1] + "…" if len(value) > limit else value


def _dedupe(items: list[str]) -> list[str]:
    normalized = (str(item).strip() for item in items)
    return list(dict.fromkeys(value for value in normalized if value))


def _dedupe_dicts(items: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    indexed = {}
    for entry in items:
        if isinstance(entry, dict) and (
            identity := str(entry.get(key, "") or "").strip()
        ):
            indexed[identity] = entry
    return list(indexed.values())


def _file_label(entry: dict[str, Any]) -> str:
    path = str(entry.get("path", "") or "?")
    return path + (":" + str(entry["lines"]) if entry.get("lines") else "")


_HANDOFF_FIELDS = (
    ("verified_state", "Verified so far:"),
    ("changes", "Changed by the previous iteration:"),
    ("unverified", "NOT verified (do not assume these hold):"),
    ("next_action", "Start with:"),
)
_CARRYOVER_FIELDS = ("files_touched", "verified", "spawned")
