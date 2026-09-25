"""Agent and native clock-action adapters for local life calendar records."""

import asyncio
import json
from datetime import datetime, timezone

from gideon.core.config.loader import config_dir
from gideon.integrations.action_providers.base import ActionProvider, ActionResult
from gideon.integrations.action_providers.services import get_action_services
from gideon.integrations.inbox import live_store

from .life_calendar import LifeCalendarStore
from .store import MeasurementError


class WellbeingReminderAction(ActionProvider):
    name = "wellbeing-reminder"
    display_name = "Daily cognitive completion reminder"

    def __init__(self, home=None, inbox=None):
        self.home, self.inbox = home, inbox

    async def execute(self, action_config, ctx, timeout=30):
        try:
            if action_config != {}:
                raise MeasurementError(
                    "Reminder action accepts no path or content overrides"
                )
            home = self.home if self.home is not None else config_dir()
            scheduled = ctx.payload.get("scheduled_for")
            as_of = (
                datetime.fromtimestamp(float(scheduled), timezone.utc).isoformat()
                if scheduled is not None
                else None
            )
            services = get_action_services()
            inbox = (
                self.inbox
                if self.inbox is not None
                else live_store(services.state) if services else None
            )
            result = await asyncio.to_thread(
                LifeCalendarStore(home).check_reminder, as_of, inbox
            )
            return ActionResult(
                success=True, stdout=json.dumps(result), outcome="completed"
            )
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            return ActionResult(success=False, exit_code=1, error=str(exc))


def create_reminder_provider(config=None):
    return WellbeingReminderAction()


async def invoke_life(home, arguments):
    store = LifeCalendarStore(home)
    operation = arguments.get("operation", "").removeprefix("life_")
    payload, identity = arguments.get("payload", {}), arguments.get("id")
    methods = {
        "configure": store.configure,
        "config": store.get_config,
        "config_history": store.config_history,
        "projection": store.projection,
        "event_create": store.create_event,
        "event_update": store.update_event,
        "events": store.list_events,
        "event_history": store.history_event,
        "reminder_check": store.check_reminder,
    }
    if operation not in methods:
        raise MeasurementError("Unknown life calendar operation")
    method = methods[operation]
    if operation in ("configure", "event_create"):
        result = await asyncio.to_thread(method, payload)
    elif operation == "event_update":
        result = await asyncio.to_thread(method, identity, payload)
    elif operation == "event_history":
        result = await asyncio.to_thread(method, identity)
    elif operation == "projection":
        result = await asyncio.to_thread(method, **payload)
    else:
        if payload:
            raise MeasurementError("This calendar operation accepts no payload")
        services = get_action_services() if operation == "reminder_check" else None
        inbox = live_store(services.state) if services else None
        result = (
            await asyncio.to_thread(method, inbox=inbox)
            if operation == "reminder_check"
            else await asyncio.to_thread(method)
        )
    return json.dumps(result, allow_nan=False)
