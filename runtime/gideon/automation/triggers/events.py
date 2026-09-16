"""Lifecycle event availability and supported trigger operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

PARITY_OPERATIONS: tuple[str, ...] = (
    "list",
    "get",
    "create",
    "update",
    "delete",
    "toggle",
    "run",
    "test",
    "history",
)

PARITY_EXEMPTIONS: dict[str, dict[str, str]] = {
    "lifecycle": {
        "run": "a lifecycle trigger fires on an agent event; /test executes its action once"
    },
    "schedule": {
        "test": "a schedule trigger's action is its run; /run?dry_run=1 previews it"
    },
}


class EventStatus(str, Enum):
    LIVE = "live"
    DORMANT = "dormant"


DORMANT_EVENTS: frozenset[str] = frozenset()

AGENT_SCOPED_EVENTS: frozenset[str] = frozenset(
    {
        "SessionStart",
        "AgentSpawn",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "Error",
    }
)

DORMANCY_NOTES: dict[str, str] = {}

_DORMANT_SUFFIX = (
    "configurable, but nothing fires it yet — a hook on this event saves and never runs"
)


@dataclass
class EventInfo:
    name: str
    status: str
    note: str = ""

    @property
    def dormant(self) -> bool:
        return EventStatus.DORMANT.value == self.status

    def to_dict(self) -> dict[str, Any]:
        return dict(name=self.name, status=self.status, note=self.note)


class LifecycleCatalog:
    def __init__(self, declared=None):
        from gideon.engine.hooks import HOOK_EVENTS

        self.names = set(HOOK_EVENTS if declared is None else declared)

    def describe(self, name: str) -> EventInfo:
        if name not in DORMANT_EVENTS:
            return EventInfo(name, EventStatus.LIVE.value)
        note = DORMANCY_NOTES.get(name, "")
        detail = "; ".join(filter(None, (note, _DORMANT_SUFFIX)))
        return EventInfo(name, EventStatus.DORMANT.value, detail)

    def rows(self) -> list[EventInfo]:
        return list(map(self.describe, sorted(self.names)))

    def dormancy_conflicts(self) -> tuple[list[str], list[str]]:
        return sorted(DORMANT_EVENTS - self.names), sorted(
            set(DORMANCY_NOTES) - DORMANT_EVENTS
        )

    def scope_conflicts(self) -> list[str]:
        from gideon.automation.triggers.lifecycle_fire import BUILDERS

        global_events = set(BUILDERS)
        overlapping = AGENT_SCOPED_EVENTS & global_events
        unowned = (
            self.names
            - AGENT_SCOPED_EVENTS
            - global_events
            - {"TaskComplete"}
            - DORMANT_EVENTS
        )
        return [
            f"{name}: claimed agent-scoped but lifecycle_fire.BUILDERS fires it globally"
            for name in sorted(overlapping)
        ] + [
            f"{name}: on neither fire path and not declared dormant"
            for name in sorted(unowned)
        ]


def verify_agent_scoping() -> list[str]:
    return LifecycleCatalog().scope_conflicts()


def event_status() -> list[EventInfo]:
    return LifecycleCatalog().rows()


def dormant_events() -> list[str]:
    return [row.name for row in event_status() if row.dormant]


def live_events() -> list[str]:
    return [row.name for row in event_status() if not row.dormant]


def configurable_but_dead() -> list[str]:
    from gideon.assurance.validation import ALLOWED_HOOK_EVENTS

    return sorted(set(dormant_events()).intersection(ALLOWED_HOOK_EVENTS))


def verify_dormancy(
    declared: set[str] | frozenset[str] | None = None,
) -> tuple[list[str], list[str]]:
    return LifecycleCatalog(declared).dormancy_conflicts()


def missing_operations(kind: str, supported: set[str] | list[str]) -> list[str]:
    required = set(PARITY_OPERATIONS).difference(PARITY_EXEMPTIONS.get(kind, {}))
    return sorted(required.difference(supported))


def parity_report(support: dict[str, set[str] | list[str]]) -> dict[str, list[str]]:
    rows = (
        (kind, missing_operations(kind, operations))
        for kind, operations in support.items()
    )
    return {kind: missing for kind, missing in rows if missing}


def unsupported_response(kind: str, operation: str) -> tuple[str, int]:
    message = f"{kind} triggers do not support /{operation}"
    detail = PARITY_EXEMPTIONS.get(kind, {}).get(operation, "")
    return message + (f": {detail}" if detail else ""), 400
