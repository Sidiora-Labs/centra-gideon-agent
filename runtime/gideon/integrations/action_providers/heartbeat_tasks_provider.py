"""Run owner-approved HEARTBEAT.md tasks from the visible system trigger."""

from __future__ import annotations

import json
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import ActionClock


class HeartbeatTasksActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "heartbeat-tasks"

    @property
    def display_name(self) -> str:
        return "Heartbeat tasks"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        del action_config, ctx, timeout
        clock = ActionClock()
        from gideon.integrations.action_providers.services import get_action_services

        services = get_action_services()
        heartbeat = getattr(services, "heartbeat", None) if services else None
        if heartbeat is None:
            return ActionResult(
                False,
                error="heartbeat task service is unavailable",
                stderr="the gateway did not attach its HeartbeatService",
            )
        try:
            result = await heartbeat.run_tasks()
        except Exception as error:
            return ActionResult(
                False,
                error=f"heartbeat task pass failed: {error}",
                stderr="the authorized HEARTBEAT.md task pass did not complete",
            )
        count = int(result.get("processed", 0) or 0)
        return clock.result(
            True,
            outcome="ran" if count else "skip",
            stdout=json.dumps(result, sort_keys=True),
        )


def create_provider() -> HeartbeatTasksActionProvider:
    return HeartbeatTasksActionProvider()


def reconcile_heartbeat_tasks_trigger(store: Any) -> None:
    """Install the stable system trigger without overwriting the owner's off switch."""
    from gideon.automation.triggers.arm import arm
    from gideon.automation.triggers.models import Trigger
    from gideon.automation.triggers.screen import capabilities_for_action

    identity = "system:heartbeat-tasks"
    try:
        existing = store.get(identity)
    except Exception:
        return
    if existing is not None:
        return
    trigger = Trigger(
        id=identity,
        name="Heartbeat tasks",
        kind="clock",
        created_by="system",
        enabled=True,
        delivery="none",
        spec={"kind": "interval", "interval_secs": 60},
        workflow={"inline": {"provider": "heartbeat-tasks", "config": {}}},
    )
    trigger.capabilities = capabilities_for_action(trigger)
    trigger.next_fire_at = arm(trigger) or ""
    store.upsert(trigger)
