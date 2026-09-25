"""Conditional schedule preview from actual trigger and workflow state."""

import copy
import json
import time
from urllib.parse import quote

from gideon.automation.triggers import arm, claims, firepath, service
from gideon.automation.triggers.routing import routed
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import config_dir
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.workspace.capabilities.platform.cadence import projection


async def view(*, horizon=3600, now=None):
    if type(horizon) is not int or not 60 <= horizon <= 86400:
        raise ValueError("Forecast horizon must be 60..86400 seconds")
    now = time.time() if now is None else now
    store = routed(TriggerStore(config_dir()))
    records = store.load()
    triggers = [copy.deepcopy(row.trigger) for row in records if row.ok]
    service._unpark_ready(store, triggers, now=now, persist=False)
    slots = claims.slot_holders(store, now=now, base_dir=config_dir())
    planner = service.TickPass(store, now, False, False, config_dir())
    events, exclusions = [], [
        {"trigger_id": row.trigger.id, "reason": "invalid_trigger"}
        for row in records
        if not row.ok
    ]
    truncated = False
    for trigger in triggers:
        if len(events) >= 500:
            truncated = True
            break
        if not trigger.enabled or trigger.state != "active":
            exclusions.append(
                {"trigger_id": trigger.id, "reason": "disabled_or_" + trigger.state}
            )
            continue
        if trigger.kind != "clock":
            exclusions.append(
                {"trigger_id": trigger.id, "reason": "event_driven", "next_at": None}
            )
            continue
        context = await planner.context_for(trigger, slots)
        admission = (await firepath.evaluate(context)).to_dict()
        cadence = projection(trigger, now=now)
        predicted = copy.deepcopy(trigger)
        if predicted.spec.get("kind") == "interval":
            predicted.spec["interval_secs"] = cadence["effective_interval"]
        due = service.to_epoch(trigger.next_fire_at)
        candidate = (
            arm.CalendarExclusions(predicted).advance(max(now, due))
            if due > 0
            else arm.next_fire(predicted, now=now)
        )
        count = 0
        while 0 < candidate <= now + horizon and count < 20 and len(events) < 500:
            expiry = service.to_epoch(trigger.expires_at)
            if expiry and candidate >= expiry:
                break
            events.append(
                {
                    "trigger_id": trigger.id,
                    "name": trigger.name,
                    "at": candidate,
                    "conditional": True,
                    "admission_now": admission,
                    "cadence_reason": cadence["reason"],
                    "edit_url": "#/triggers?open=" + quote(trigger.id, safe=""),
                    "duration_seconds": None,
                }
            )
            count += 1
            following = arm.next_fire(
                predicted, now=candidate + 0.001, last_fire=candidate
            )
            if following <= candidate:
                break
            candidate = following
        if count >= 20 and candidate <= now + horizon:
            truncated = True
    dependencies = []
    for path in sorted(
        (config_dir() / "capabilities/platform/maintenance").glob("*.json")
    ):
        row = json.loads(path.read_text())
        if row["status"] not in {"running", "cancelling", "failed"}:
            continue
        tasks, total = await NativeTaskProvider().list_tasks(
            task_list_id=row["task_list_id"], limit=1000
        )
        pending = sum(task.status.value not in {"done", "cancelled"} for task in tasks)
        dependencies.append(
            {
                "maintenance_id": row["id"],
                "project_id": row["project_id"],
                "status": row["status"],
                "child_id": row["child_id"],
                "pending_issues": pending,
                "issue_count_complete": total <= 1000,
                "next_at": None,
            }
        )
    events.sort(key=lambda row: (row["at"], row["trigger_id"]))
    return {
        "version": 1,
        "as_of": now,
        "horizon": horizon,
        "events": events,
        "excluded": exclusions,
        "dependencies": dependencies,
        "truncated": truncated,
        "qualification": "Conditional clock forecast with current admission only; future state and durations are unknown.",
    }
