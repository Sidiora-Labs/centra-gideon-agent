"""User-declared lifetime projections, milestones and completion-aware inbox reminders."""

import hashlib
import json
import math
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gideon.automation.triggers.arm import arm
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.integrations.inbox import InboxStore, emit_attention_item

from .intervention import calendar_date
from .store import MeasurementError, MeasurementStore, instant, text


class LifeCalendarStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        self.home = self.path.parent.parent
        with self.connection() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS life_calendar_revisions(entity TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(entity,id,revision));
                CREATE TABLE IF NOT EXISTS life_reminder_claims(day TEXT NOT NULL,timezone TEXT NOT NULL,item_id TEXT NOT NULL,PRIMARY KEY(day,timezone));"""
            )

    def _get(self, db, entity, identity):
        row = db.execute(
            "SELECT data FROM life_calendar_revisions WHERE entity=? AND id=? ORDER BY revision DESC LIMIT 1",
            (entity, identity),
        ).fetchone()
        if not row:
            if entity == "config":
                return None
            raise MeasurementError("Life event not found", 404, "not_found")
        return json.loads(row[0])

    def _sync_trigger(self, config):
        hour, minute = config["reminder"]["time"].split(":")
        trigger = Trigger(
            id="wellbeing-daily-completion",
            name="Daily cognitive practice reminder",
            kind="clock",
            enabled=config["reminder"]["enabled"],
            spec={
                "kind": "cron",
                "expr": f"{int(minute)} {int(hour)} * * *",
                "timezone": config["timezone"],
            },
            workflow={"provider": "wellbeing-reminder", "config": {}},
            capabilities={"providers": ["wellbeing-reminder"]},
            delivery="none",
            failure_delivery="none",
        )
        store = TriggerStore(self.home)
        existing = store.get(trigger.id)
        if (
            existing
            and existing.trigger.spec == trigger.spec
            and existing.trigger.enabled == trigger.enabled
            and existing.trigger.workflow == trigger.workflow
        ):
            return trigger.id
        if existing:
            trigger = replace(
                existing.trigger,
                spec=trigger.spec,
                enabled=trigger.enabled,
                workflow=trigger.workflow,
                capabilities=trigger.capabilities,
            )
        trigger.next_fire_at = arm(trigger, now=time.time()) if trigger.enabled else ""
        store.upsert(trigger)
        return trigger.id

    def _validate_config(self, payload):
        birth = calendar_date(payload.get("birth_date"))
        years, sleep = payload.get("horizon_years"), payload.get("sleep_hours")
        if type(years) is not int or not 1 <= years <= 120 or birth.year + years > 9999:
            raise MeasurementError("horizon_years must be 1..120 within calendar range")
        if (
            type(sleep) not in (int, float)
            or not math.isfinite(sleep)
            or not 0 <= sleep <= 24
        ):
            raise MeasurementError("sleep_hours must be finite in 0..24")
        try:
            zone = ZoneInfo(payload.get("timezone"))
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise MeasurementError("Unknown calendar timezone") from exc
        if birth > datetime.now(timezone.utc).astimezone(zone).date():
            raise MeasurementError("Birth date must not be in the future")
        text(payload.get("source"), "source", 256)
        budgets = payload.get("budgets")
        if not isinstance(budgets, list) or len(budgets) > 30:
            raise MeasurementError("budgets must contain at most30 entries")
        names, total = set(), 0
        for budget in budgets:
            if not isinstance(budget, dict) or set(budget) != {
                "name",
                "hours_per_week",
            }:
                raise MeasurementError(
                    "Activity budget requires name and hours_per_week"
                )
            name = text(budget["name"], "budget name", 120)
            hours = budget["hours_per_week"]
            if (
                name in names
                or type(hours) not in (int, float)
                or not math.isfinite(hours)
                or not 0 <= hours <= 168
            ):
                raise MeasurementError(
                    "Activity budgets require unique names and finite weekly hours0..168"
                )
            names.add(name)
            total += hours
        if total > 168 - sleep * 7:
            raise MeasurementError(
                "Activity budgets exceed declared weekly waking hours"
            )
        reminder = payload.get("reminder")
        if (
            not isinstance(reminder, dict)
            or set(reminder) != {"enabled", "time"}
            or type(reminder["enabled"]) is not bool
            or not isinstance(reminder["time"], str)
            or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", reminder["time"])
        ):
            raise MeasurementError(
                "Reminder requires enabled boolean and HH:MM local time"
            )

    def _write(self, entity, payload, identity=None):
        config_fields = {
            "birth_date",
            "horizon_years",
            "sleep_hours",
            "timezone",
            "budgets",
            "source",
            "reminder",
        }
        event_fields = {"date", "title", "notes", "kind"}
        allowed = (
            config_fields | {"request_id", "revision"}
            if entity == "config"
            else event_fields
            | {"request_id"}
            | ({"revision", "deleted"} if identity else {"source"})
        )
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise MeasurementError("Unknown or immutable life calendar fields")
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps(
                ["life-calendar", entity, identity, payload],
                sort_keys=True,
                allow_nan=False,
            )
        except (ValueError, TypeError) as exc:
            raise MeasurementError(
                "Calendar payload requires finite JSON values"
            ) from exc
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used", 409, "conflict")
                return json.loads(prior[1])
            if entity == "config":
                current = self._get(db, "config", "config")
                expected = current["revision"] if current else 0
                if (
                    type(payload.get("revision")) is not int
                    or payload["revision"] != expected
                ):
                    raise MeasurementError(
                        "Calendar configuration changed; reload", 409, "conflict"
                    )
                self._validate_config(payload)
                row = {key: payload[key] for key in config_fields}
                row.update(
                    id="config",
                    revision=expected + 1,
                    created_at=(
                        current["created_at"]
                        if current
                        else datetime.now(timezone.utc).isoformat()
                    ),
                    assumption="user_declared_horizon",
                )
                row["trigger_id"] = self._sync_trigger(row)
            else:
                if identity:
                    row = self._get(db, entity, identity)
                    if (
                        type(payload.get("revision")) is not int
                        or payload["revision"] != row["revision"]
                        or row["deleted"]
                    ):
                        raise MeasurementError(
                            "Life event changed or deleted; reload", 409, "conflict"
                        )
                    row.update(
                        {
                            key: payload[key]
                            for key in event_fields | {"deleted"}
                            if key in payload
                        }
                    )
                    row["revision"] += 1
                else:
                    row = {key: payload.get(key) for key in event_fields | {"source"}}
                    row.update(
                        id=str(uuid4()),
                        revision=1,
                        deleted=False,
                        notes=payload.get("notes", ""),
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                calendar_date(row["date"])
                text(row["title"], "title", 200)
                text(row["source"], "source", 256)
                text(row["notes"], "notes", 4000, True)
                if (
                    row["kind"] not in ("recorded", "planned")
                    or type(row["deleted"]) is not bool
                ):
                    raise MeasurementError(
                        "Life event needs recorded/planned kind and boolean deleted"
                    )
            encoded = json.dumps(row)
            db.execute(
                "INSERT INTO life_calendar_revisions VALUES(?,?,?,?)",
                (entity, row["id"], row["revision"], encoded),
            )
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded)
            )
            return row

    def configure(self, payload):
        return self._write("config", payload)

    def get_config(self):
        with self.connection() as db:
            return self._get(db, "config", "config")

    def create_event(self, payload):
        return self._write("event", payload)

    def update_event(self, identity, payload):
        return self._write("event", payload, identity)

    def list_events(self):
        with self.connection() as db:
            rows = db.execute(
                """SELECT r.data FROM life_calendar_revisions r WHERE entity='event' AND revision=(SELECT MAX(s.revision) FROM life_calendar_revisions s WHERE s.entity=r.entity AND s.id=r.id) AND json_extract(data,'$.deleted')=0 ORDER BY json_extract(data,'$.date'),id LIMIT 500"""
            )
            return [json.loads(row[0]) for row in rows]

    def _history(self, entity, identity):
        with self.connection() as db:
            self._get(db, entity, identity)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM life_calendar_revisions WHERE entity=? AND id=? ORDER BY revision",
                    (entity, identity),
                )
            ]

    def config_history(self):
        return self._history("config", "config")

    def history_event(self, identity):
        return self._history("event", identity)

    def projection(self, as_of=None):
        config = self.get_config()
        if not config:
            return {"configured": False}
        at = (
            datetime.fromisoformat(instant(as_of))
            if as_of
            else datetime.now(timezone.utc)
        )
        today = at.astimezone(ZoneInfo(config["timezone"])).date()
        birth = calendar_date(config["birth_date"])
        try:
            horizon = birth.replace(year=birth.year + config["horizon_years"])
        except ValueError:
            horizon = birth.replace(year=birth.year + config["horizon_years"], day=28)
        total = (horizon - birth).days
        elapsed = min(total, max(0, (today - birth).days))
        remaining = total - elapsed
        return dict(
            configured=True,
            as_of=at.isoformat(),
            timezone=config["timezone"],
            birth_date=str(birth),
            horizon_date=str(horizon),
            total_days=total,
            elapsed_days=elapsed,
            remaining_days=remaining,
            weeks_total=math.ceil(total / 7),
            weeks_elapsed=elapsed // 7,
            weeks_remaining=math.ceil(remaining / 7),
            sleep_hours_elapsed=elapsed * config["sleep_hours"],
            sleep_hours_remaining=remaining * config["sleep_hours"],
            waking_hours_remaining=remaining * (24 - config["sleep_hours"]),
            budgets=[
                dict(budget, remaining_hours=remaining * budget["hours_per_week"] / 7)
                for budget in config["budgets"]
            ],
            assumption="user_declared_horizon",
            events=self.list_events(),
        )

    def check_reminder(self, as_of=None, inbox=None):
        at = (
            datetime.fromisoformat(instant(as_of))
            if as_of
            else datetime.now(timezone.utc)
        )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            config = self._get(db, "config", "config")
            if not config or not config["reminder"]["enabled"]:
                return {"status": "disabled"}
            local = at.astimezone(ZoneInfo(config["timezone"]))
            day = str(local.date())
            if local.strftime("%H:%M") < config["reminder"]["time"]:
                return dict(status="not_due", local_date=day)
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cognitive_sessions'"
            ).fetchone():
                sessions = db.execute(
                    "SELECT r.data FROM cognitive_sessions r WHERE revision=(SELECT MAX(s.revision) FROM cognitive_sessions s WHERE s.id=r.id) AND json_extract(data,'$.status')='completed'"
                )
                for record in sessions:
                    session = json.loads(record[0])
                    finished = datetime.fromisoformat(
                        session["trials"][-1]["answered_at"]
                    )
                    if (
                        finished <= at
                        and str(
                            finished.astimezone(ZoneInfo(config["timezone"])).date()
                        )
                        == day
                    ):
                        return dict(status="completed", local_date=day)
            prior = db.execute(
                "SELECT item_id FROM life_reminder_claims WHERE day=? AND timezone=?",
                (day, config["timezone"]),
            ).fetchone()
            if prior:
                return dict(status="already_sent", local_date=day, item_id=prior[0])
            target = (
                inbox if inbox is not None else InboxStore(self.home / "inbox.json")
            )
            if target._path.resolve() != (self.home / "inbox.json").resolve():
                raise MeasurementError("Reminder inbox belongs to a different home")
            if inbox is None:
                target.load()
            key = (
                "wellbeing-daily-"
                + hashlib.sha256(f'{config["timezone"]}:{day}'.encode()).hexdigest()
            )
            existing = next(
                (
                    item
                    for item in target.items.values()
                    if item.refs.get("dedup_key") == key
                ),
                None,
            )
            identity = (
                existing.id
                if existing
                else emit_attention_item(
                    None,
                    source="wellbeing",
                    kind="reminder",
                    item_kind="needs_input",
                    title="Daily cognitive practice",
                    body="No completed practice session is recorded for your local day. Open timed cognitive practice when ready.",
                    refs={
                        "href": "#/capabilities/wellbeing/cognition",
                        "local_date": day,
                    },
                    store=target,
                    dedup_key=key,
                    addressee="",
                )
            )
            persisted = InboxStore(self.home / "inbox.json")
            persisted.load()
            if not identity or identity not in persisted.items:
                raise MeasurementError(
                    "Reminder inbox item was not persisted", 503, "unavailable"
                )
            db.execute(
                "INSERT INTO life_reminder_claims VALUES(?,?,?)",
                (day, config["timezone"], identity),
            )
            return dict(
                status="already_sent" if existing else "sent",
                local_date=day,
                item_id=identity,
            )
