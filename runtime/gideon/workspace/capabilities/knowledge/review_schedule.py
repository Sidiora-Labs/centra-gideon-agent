"""Review schedules use the canonical clock triggers and action dispatch."""

import hashlib
import json
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from gideon.automation.triggers.arm import arm
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.integrations.action_providers.base import ActionProvider, ActionResult

from .capture import CaptureError, request_key, text_field
from .reviews import packed, window


class ReviewSchedules:
    def __init__(self, service):
        self.service, self.db = service, service.db
        self.triggers = TriggerStore(service.home)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_review_schedules (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS capability_knowledge_review_schedule_requests (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, schedule_id TEXT NOT NULL, result TEXT);
        """)

    def get(self, identity):
        row = self.db.execute(
            "SELECT body FROM capability_knowledge_review_schedules WHERE id=?",
            (identity,),
        ).fetchone()
        if row is None:
            raise CaptureError("Review schedule not found", 404)
        result = json.loads(row[0])
        trigger = self.triggers.get(result["trigger_id"])
        return {
            **result,
            "next_fire_at": trigger.trigger.next_fire_at if trigger else "",
            "enabled": trigger.trigger.enabled if trigger else False,
        }

    def list(self):
        return {
            "items": [
                self.get(row[0])
                for row in self.db.execute(
                    "SELECT id FROM capability_knowledge_review_schedules ORDER BY rowid DESC"
                )
            ]
        }

    def save(self, body):
        required = {"request_id", "period", "timezone", "time", "weekday", "enabled"}
        if not isinstance(body, dict) or set(body) not in (
            required,
            required | {"id", "revision"},
        ):
            raise CaptureError(
                "Schedule requires request_id, period, timezone, time, weekday, enabled and optional paired id/revision"
            )
        key = request_key(body["request_id"])
        window(body["period"], "2026-01-01", body["timezone"])
        if (
            not isinstance(body["time"], str)
            or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", body["time"])
            or type(body["weekday"]) is not int
            or not 0 <= body["weekday"] <= 6
            or type(body["enabled"]) is not bool
        ):
            raise CaptureError("Schedule time, weekday or enabled flag is invalid")
        if "id" in body:
            text_field(body["id"], "id", 128)
        payload = packed(body)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT * FROM capability_knowledge_review_schedule_requests WHERE request_id=?",
                (key,),
            ).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise CaptureError(
                        "Schedule request belongs to different input", 409
                    )
                if previous["result"]:
                    self.db.commit()
                    return json.loads(previous["result"])
            self.service.assert_scope()
            if previous:
                row = self.db.execute(
                    "SELECT body FROM capability_knowledge_review_schedules WHERE id=?",
                    (previous["schedule_id"],),
                ).fetchone()
                result = json.loads(row[0])
            else:
                old = self.get(body["id"]) if "id" in body else None
                if old and (
                    type(body["revision"]) is not int
                    or old["revision"] != body["revision"]
                ):
                    raise CaptureError("Schedule changed; reload before editing", 409)
                if (
                    old
                    and self.db.execute(
                        "SELECT 1 FROM capability_knowledge_review_schedule_requests WHERE schedule_id=? AND result IS NULL",
                        (old["id"],),
                    ).fetchone()
                ):
                    raise CaptureError(
                        "A previous schedule update must be retried first", 409
                    )
                if old is None and len(self.list()["items"]) >= 100:
                    raise CaptureError("At most 100 review schedules are supported")
                identity = (
                    old["id"]
                    if old
                    else hashlib.sha256(
                        (str(self.service.home) + ":" + key).encode()
                    ).hexdigest()[:32]
                )
                result = {
                    key: body[key]
                    for key in ("period", "timezone", "time", "weekday", "enabled")
                }
                result.update(
                    id=identity,
                    revision=old["revision"] + 1 if old else 1,
                    trigger_id="knowledge-review:" + identity,
                )
                self.db.execute(
                    "INSERT INTO capability_knowledge_review_schedules VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                    (identity, packed(result)),
                )
                self.db.execute(
                    "INSERT INTO capability_knowledge_review_schedule_requests VALUES (?,?,?,NULL)",
                    (key, payload, identity),
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.db.execute("BEGIN IMMEDIATE")
        try:
            completed = self.db.execute(
                "SELECT result FROM capability_knowledge_review_schedule_requests WHERE request_id=?",
                (key,),
            ).fetchone()[0]
            if completed:
                self.db.commit()
                return json.loads(completed)
            hour, minute = map(int, result["time"].split(":"))
            weekday = (
                "*" if result["period"] == "daily" else str((result["weekday"] + 1) % 7)
            )
            trigger = Trigger(
                id=result["trigger_id"],
                name=result["period"].title() + " knowledge review",
                kind="clock",
                enabled=result["enabled"],
                created_by="knowledge-review",
                spec={
                    "kind": "cron",
                    "expr": f"{minute} {hour} * * {weekday}",
                    "timezone": result["timezone"],
                },
                workflow={
                    "provider": "knowledge-review",
                    "config": {"schedule_id": result["id"]},
                },
                capabilities={"providers": ["knowledge-review"]},
                overlap="skip",
                session="fresh",
                delivery="none",
                failure_delivery="none",
            )
            existing = self.triggers.get(trigger.id)
            if (
                existing
                and existing.trigger.spec == trigger.spec
                and existing.trigger.workflow == trigger.workflow
                and existing.trigger.enabled == trigger.enabled
            ):
                trigger = existing.trigger
            else:
                if existing:
                    trigger = replace(
                        existing.trigger,
                        name=trigger.name,
                        spec=trigger.spec,
                        workflow=trigger.workflow,
                        enabled=trigger.enabled,
                        capabilities=trigger.capabilities,
                    )
                trigger.next_fire_at = (
                    arm(trigger, now=time.time()) if trigger.enabled else ""
                )
                self.triggers.upsert(trigger)
            result = self.get(result["id"])
            self.db.execute(
                "UPDATE capability_knowledge_review_schedule_requests SET result=? WHERE request_id=? AND result IS NULL",
                (packed(result), key),
            )
            self.db.commit()
            return json.loads(
                self.db.execute(
                    "SELECT result FROM capability_knowledge_review_schedule_requests WHERE request_id=?",
                    (key,),
                ).fetchone()[0]
            )
        except Exception:
            self.db.rollback()
            raise

    def materialize(self, identity, scheduled_for=None):
        self.service.assert_scope()
        schedule = self.get(identity)
        if not schedule["enabled"]:
            raise CaptureError("Review schedule is disabled", 409)
        instant = (
            datetime.fromtimestamp(float(scheduled_for), timezone.utc)
            if scheduled_for is not None
            else datetime.now(timezone.utc)
        )
        day = (
            instant.astimezone(ZoneInfo(schedule["timezone"])).date()
            - timedelta(days=1)
        ).isoformat()
        key = "scheduled-" + identity + "-" + day
        existing = self.service.receipt(key)
        if existing:
            return existing
        preview = self.service.preview(schedule["period"], day, schedule["timezone"])
        return self.service.save(
            {
                "request_id": key,
                "period": schedule["period"],
                "date": day,
                "timezone": schedule["timezone"],
                "preview_id": preview["preview_id"],
                "reflection": "",
            },
            scheduled=True,
        )


class ReviewActionProvider(ActionProvider):
    def __init__(self, schedules):
        self.schedules = schedules

    @property
    def name(self):
        return "knowledge-review"

    @property
    def display_name(self):
        return "Materialize a scheduled knowledge review"

    async def execute(self, action_config, ctx, timeout=30):
        try:
            if not isinstance(action_config, dict) or set(action_config) != {
                "schedule_id"
            }:
                raise CaptureError(
                    "Review action requires only its registered schedule_id"
                )
            text_field(action_config["schedule_id"], "schedule_id", 128)
            result = self.schedules.materialize(
                action_config["schedule_id"], ctx.payload.get("scheduled_for")
            )
            return ActionResult(
                success=True, stdout=packed(result), outcome="completed"
            )
        except (ValueError, TypeError, OverflowError) as exc:
            return ActionResult(success=False, error=str(exc), exit_code=1)
