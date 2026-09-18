"""Plan and persist the three system report trigger reconciliation policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReportClockRevision:
    trigger: Any
    created: bool

    def persist(
        self, store: Any, capabilities_for_action: Callable | None = None
    ) -> None:
        from gideon.automation.triggers.arm import arm

        if capabilities_for_action is not None:
            self.trigger.capabilities = capabilities_for_action(self.trigger)
        fire_at = arm(self.trigger)
        if fire_at:
            self.trigger.next_fire_at = fire_at
        store.upsert(self.trigger)


@dataclass(frozen=True)
class ReportClock:
    identifier: str
    name: str
    provider: str
    purpose: str = ""

    def converge(self, store: Any) -> Any:
        """Collapse duplicate system copies of this clock before the caller reads the store."""
        from gideon.automation.triggers.singletons import converge

        return converge(store, self.purpose or self.provider)

    def plan(
        self, row: Any, expression: str, *, policy: str, enabled: bool = True
    ) -> ReportClockRevision | None:
        from gideon.automation.triggers.models import Trigger

        if row is not None and policy == "create":
            return None
        new = row is None
        trigger = (
            Trigger(
                id=self.identifier,
                name=self.name,
                kind="clock",
                created_by="system",
                enabled=True,
                delivery="none",
                workflow={"inline": {"provider": self.provider, "config": {}}},
            )
            if new
            else row.trigger
        )
        trigger.purpose = self.purpose or self.provider
        spec = (
            dict(trigger.spec or {})
            if policy == "cadence"
            else (dict(trigger.spec) if isinstance(trigger.spec, dict) else {})
        )
        if not new and policy == "schedule" and spec.get("expr") == expression:
            return None
        spec.update(kind="cron", expr=expression)
        trigger.spec = spec
        if policy == "cadence":
            trigger.enabled = enabled
            trigger.workflow = {"inline": {"provider": self.provider, "config": {}}}
        return ReportClockRevision(trigger, new)
