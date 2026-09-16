"""Persist source availability transitions for subscriptions bound to one app."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gideon.automation.event_triggers import EventTrigger, EventTriggerStore

logger = logging.getLogger(__name__)
_GLOB_CHARS = frozenset("*?[]")


def bound_app(event_glob: str) -> str:
    from gideon.automation.trigger_sources.registry import NAMESPACE_PREFIX

    segments = (event_glob or "").strip().split(":", 2)
    if len(segments) != 3 or segments[0] != NAMESPACE_PREFIX:
        return ""
    candidate = segments[1]
    return candidate if candidate and _GLOB_CHARS.isdisjoint(candidate) else ""


def bound_triggers(triggers: list[EventTrigger], app: str) -> list[EventTrigger]:
    from gideon.automation.event_triggers import APP_EVENT

    eligible = (trigger for trigger in triggers if trigger.pattern == APP_EVENT)
    return [trigger for trigger in eligible if bound_app(trigger.event_glob) == app]


class SourceAvailability:
    def __init__(self, store: EventTriggerStore, app: str):
        self.store, self.app = store, app
        self.records = store.load()

    def apply(
        self, *, restore: bool, state: str, reason: str, retry_after: float
    ) -> list[str]:
        from gideon.automation.triggers.models import TriggerState

        changed = []
        for record in bound_triggers(self.records, self.app):
            parked = record.state == TriggerState.PARKED.value
            if parked != restore:
                continue
            record.state, record.park_reason, record.park_retry_after = (
                state,
                reason,
                retry_after,
            )
            changed.append(record.id)
        if changed:
            self.store.save(self.records)
            logger.info(
                "%s %d event trigger(s) bound to app %r: %s",
                "un-parked" if restore else "parked",
                len(changed),
                self.app,
                ", ".join(changed),
            )
        return changed


def park_for_app(store: EventTriggerStore, app: str, *, now: float = 0.0) -> list[str]:
    from gideon.automation.triggers.autopause import ExitType, evaluate

    decision = evaluate(
        exit_type=ExitType.TRANSPORT_UNAVAILABLE.value, consecutive_failures=0, now=now
    )
    cause = f"{decision.reason}: the {app!r} app that supplies its events is disabled or uninstalled"
    return SourceAvailability(store, app).apply(
        restore=False,
        state=decision.state,
        reason=cause,
        retry_after=decision.retry_after,
    )


def unpark_for_app(store: EventTriggerStore, app: str) -> list[str]:
    from gideon.automation.triggers.models import TriggerState

    return SourceAvailability(store, app).apply(
        restore=True, state=TriggerState.ACTIVE.value, reason="", retry_after=0.0
    )


def _default_store() -> Any:
    from gideon.automation.event_triggers import EventTriggerStore
    from gideon.core.config.loader import config_dir

    path = config_dir().joinpath("event_triggers.json")
    return EventTriggerStore(path)
