"""User-defined intervention schedules and explicit adherence observations."""

import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import MeasurementError, MeasurementStore, instant, text


def calendar_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise MeasurementError("Date must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except ValueError as exc:
        raise MeasurementError("Date must use YYYY-MM-DD") from exc


def scheduled(plan, day):
    return (
        day >= calendar_date(plan["start_date"])
        and (plan["end_date"] is None or day <= calendar_date(plan["end_date"]))
        and day.weekday() in plan["weekdays"]
    )


class InterventionStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            for entity in ("plans", "records"):
                db.execute(
                    f"CREATE TABLE IF NOT EXISTS intervention_{entity}(id TEXT NOT NULL,revision INTEGER NOT NULL,parent_id TEXT NOT NULL,day TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision))"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS intervention_dates(plan_id TEXT NOT NULL,day TEXT NOT NULL,record_id TEXT NOT NULL,PRIMARY KEY(plan_id,day))"
            )

    def _get(self, db, entity, identity):
        row = db.execute(
            f"SELECT data FROM intervention_{entity} WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if not row:
            raise MeasurementError("Intervention record not found", 404, "not_found")
        return json.loads(row[0])

    def _write(self, entity, payload, identity=None, parent=None):
        if not isinstance(payload, dict):
            raise MeasurementError("Intervention payload must be an object")
        fields = (
            {"name", "instructions", "archived"}
            if entity == "plans"
            else {"status", "observed_at", "notes"}
        )
        create = (
            {
                "name",
                "instructions",
                "kind",
                "source",
                "timezone",
                "start_date",
                "end_date",
                "weekdays",
            }
            if entity == "plans"
            else fields | {"date"}
        )
        if set(payload) - (
            {"request_id", "revision"} | fields if identity else {"request_id"} | create
        ):
            raise MeasurementError("Unknown or immutable intervention fields")
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps(
                ["intervention", entity, identity, parent, payload],
                sort_keys=True,
                allow_nan=False,
            )
        except (ValueError, TypeError) as exc:
            raise MeasurementError(
                "Intervention payload requires finite JSON values"
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
            if identity:
                row = self._get(db, entity, identity)
                if (
                    type(payload.get("revision")) is not int
                    or payload["revision"] != row["revision"]
                ):
                    raise MeasurementError(
                        "Intervention changed; reload", 409, "conflict"
                    )
                row.update({key: payload[key] for key in fields if key in payload})
                row["revision"] += 1
            else:
                row = {key: payload.get(key) for key in create}
                row.update(
                    id=str(uuid4()),
                    revision=1,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                if entity == "plans":
                    row["archived"] = False
                else:
                    plan = self._get(db, "plans", parent)
                    if plan["archived"]:
                        raise MeasurementError(
                            "Intervention is archived", 409, "conflict"
                        )
                    calendar_date(row["date"])
                    if db.execute(
                        "SELECT 1 FROM intervention_dates WHERE plan_id=? AND day=?",
                        (parent, row["date"]),
                    ).fetchone():
                        raise MeasurementError(
                            "This intervention day already has a record; correct it",
                            409,
                            "conflict",
                        )
                    row.update(
                        plan_id=parent,
                        plan_revision=plan["revision"],
                        source=plan["source"],
                        notes=payload.get("notes", ""),
                    )
            if entity == "plans":
                for key, limit in [
                    ("name", 200),
                    ("instructions", 4000),
                    ("source", 256),
                ]:
                    text(row[key], key, limit)
                if (
                    row["kind"] not in ("medication", "supplement", "activity", "other")
                    or type(row["archived"]) is not bool
                ):
                    raise MeasurementError("Invalid intervention kind or archive state")
                try:
                    ZoneInfo(row["timezone"])
                except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
                    raise MeasurementError("Unknown intervention timezone") from exc
                first = calendar_date(row["start_date"])
                if (
                    row["end_date"] is not None
                    and calendar_date(row["end_date"]) < first
                ):
                    raise MeasurementError("Intervention end must not precede start")
                days = row["weekdays"]
                if (
                    not isinstance(days, list)
                    or not 1 <= len(days) <= 7
                    or any(type(day) is not int or not 0 <= day <= 6 for day in days)
                    or len(set(days)) != len(days)
                ):
                    raise MeasurementError(
                        "Weekdays require unique integers Monday=0 through Sunday=6"
                    )
                parent_id, day = "", ""
            else:
                plan = self._get(db, "plans", row["plan_id"])
                day = calendar_date(row["date"])
                if not scheduled(plan, day):
                    raise MeasurementError(
                        "Record date is outside the intervention schedule"
                    )
                observed = datetime.fromisoformat(instant(row["observed_at"]))
                if observed.astimezone(ZoneInfo(plan["timezone"])).date() < day:
                    raise MeasurementError(
                        "Observation must not precede its scheduled local date"
                    )
                if row["status"] not in ("completed", "skipped"):
                    raise MeasurementError("Status must be completed or skipped")
                text(row["notes"], "notes", 4000, True)
                parent_id, day = row["plan_id"], row["date"]
                if not identity:
                    db.execute(
                        "INSERT INTO intervention_dates VALUES(?,?,?)",
                        (parent_id, day, row["id"]),
                    )
            encoded = json.dumps(row)
            db.execute(
                f"INSERT INTO intervention_{entity} VALUES(?,?,?,?,?)",
                (row["id"], row["revision"], parent_id, day, encoded),
            )
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded)
            )
            return row

    def create_plan(self, payload):
        return self._write("plans", payload)

    def update_plan(self, identity, payload):
        return self._write("plans", payload, identity)

    def record(self, plan_id, payload):
        return self._write("records", payload, parent=plan_id)

    def correct_record(self, identity, payload):
        return self._write("records", payload, identity)

    def get_plan(self, identity):
        with self.connection() as db:
            return self._get(db, "plans", identity)

    def get_record(self, identity):
        with self.connection() as db:
            return self._get(db, "records", identity)

    def list_plans(self, include_archived=False):
        if type(include_archived) is not bool:
            raise MeasurementError("include_archived must be boolean")
        with self.connection() as db:
            rows = db.execute(
                "SELECT r.data FROM intervention_plans r WHERE revision=(SELECT MAX(s.revision) FROM intervention_plans s WHERE s.id=r.id) AND (? OR json_extract(r.data,'$.archived')=0) ORDER BY id LIMIT 500",
                (include_archived,),
            )
            return [json.loads(row[0]) for row in rows]

    def list_records(self, plan_id):
        self.get_plan(plan_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT r.data FROM intervention_records r WHERE parent_id=? AND revision=(SELECT MAX(s.revision) FROM intervention_records s WHERE s.id=r.id) ORDER BY day DESC LIMIT 10000",
                (plan_id,),
            )
            return [json.loads(row[0]) for row in rows]

    def _history(self, entity, identity):
        with self.connection() as db:
            self._get(db, entity, identity)
            return [
                json.loads(row[0])
                for row in db.execute(
                    f"SELECT data FROM intervention_{entity} WHERE id=? ORDER BY revision",
                    (identity,),
                )
            ]

    def history_plan(self, identity):
        return self._history("plans", identity)

    def history_record(self, identity):
        return self._history("records", identity)

    def summary(self, plan_id, days=30, as_of=None):
        if type(days) is not int or not 1 <= days <= 366:
            raise MeasurementError("days must be 1..366")
        plan = self.get_plan(plan_id)
        now = (
            datetime.fromisoformat(instant(as_of))
            if as_of
            else datetime.now(timezone.utc)
        )
        last = now.astimezone(ZoneInfo(plan["timezone"])).date()
        rows = {
            row["date"]: row
            for row in self.list_records(plan_id)
            if datetime.fromisoformat(instant(row["observed_at"])) <= now
        }
        result = []
        for offset in range(days - 1, -1, -1):
            day = last - timedelta(days=offset)
            record = rows.get(str(day))
            result.append(
                dict(
                    date=str(day),
                    scheduled=scheduled(plan, day),
                    status=record["status"] if record else None,
                    record_id=record["id"] if record else None,
                )
            )
        completed = sum(row["status"] == "completed" for row in result)
        skipped = sum(row["status"] == "skipped" for row in result)
        expected = sum(row["scheduled"] for row in result)
        return dict(
            plan_id=plan_id,
            timezone=plan["timezone"],
            days=result,
            scheduled_days=expected,
            completed_days=completed,
            skipped_days=skipped,
            unrecorded_days=expected - completed - skipped,
            completion_rate=(
                completed / (completed + skipped) if completed + skipped else None
            ),
            recording_rate=(completed + skipped) / expected if expected else None,
        )
