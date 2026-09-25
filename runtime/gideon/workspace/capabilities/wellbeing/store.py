"""Home-scoped measurement ledger. All corrections append a complete revision."""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class MeasurementError(ValueError):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status, self.code = status, code


def text(value, field, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise MeasurementError(f"{field} must be text of at most {limit} characters")
    return value


def instant(value):
    text(value, "observed_at", 64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError) as exc:
        raise MeasurementError("Timestamp must include a valid UTC offset") from exc


def normalized(kind, unit, values):
    keys = {"weight"} if kind == "body_weight" else {"systolic", "diastolic"}
    if kind not in ("body_weight", "blood_pressure"):
        raise MeasurementError("Unknown measurement kind")
    if not isinstance(values, dict) or set(values) != keys:
        raise MeasurementError("Values must contain exactly the components for this kind")
    numbers = {}
    for key, value in values.items():
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1000:
            raise MeasurementError(f"{key} must be finite and greater than 0, at most 1000")
        numbers[key] = float(value)
    if kind == "body_weight":
        if unit not in ("kg", "lb"):
            raise MeasurementError("Body weight unit must be kg or lb")
        numbers["weight"] *= 0.45359237 if unit == "lb" else 1
        return "kg", numbers
    if unit != "mmHg" or numbers["systolic"] > 400 or numbers["diastolic"] > 300:
        raise MeasurementError("Pressure requires mmHg, systolic <= 400 and diastolic <= 300")
    if numbers["diastolic"] >= numbers["systolic"]:
        raise MeasurementError("Diastolic must be below systolic")
    return unit, numbers


class MeasurementStore:
    def __init__(self, home: Path):
        self.path = Path(home) / "capabilities" / "wellbeing.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT NOT NULL, revision INTEGER NOT NULL,
                    observed_utc TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY (id, revision));
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS observed ON revisions(observed_utc);
                PRAGMA user_version = 1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _get(self, db, identity):
        row = db.execute("SELECT data FROM revisions WHERE id=? ORDER BY revision DESC LIMIT 1", (identity,)).fetchone()
        if row is None:
            raise MeasurementError("Measurement not found", 404, "not_found")
        return json.loads(row[0])

    def get(self, identity):
        with self.connection() as db:
            return self._get(db, identity)

    def create(self, payload):
        return self._write(None, payload)

    def correct(self, identity, payload):
        return self._write(identity, payload)

    def _write(self, identity, payload):
        if not isinstance(payload, dict):
            raise MeasurementError("Measurement must be an object")
        allowed = {"request_id", "revision", "observed_at", "unit", "values", "notes"} if identity else {"request_id", "kind", "observed_at", "unit", "values", "source", "notes"}
        if set(payload) - allowed:
            raise MeasurementError("Unknown or immutable measurement fields")
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps([identity, payload], sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise MeasurementError("Payload must contain finite JSON values") from exc
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT payload, result FROM requests WHERE id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used for a different mutation", 409, "conflict")
                return json.loads(prior[1])
            if identity:
                record = self._get(db, identity)
                if type(payload.get("revision")) is not int or payload["revision"] != record["revision"]:
                    raise MeasurementError("Measurement changed; reload before correcting", 409, "conflict")
                if "unit" in payload and "values" not in payload:
                    raise MeasurementError("Unit changes require values")
                record.update({key: value for key, value in payload.items() if key not in ("request_id", "revision")})
                record["revision"] += 1
            else:
                record = {key: payload.get(key) for key in ("kind", "observed_at", "unit", "values", "source")}
                record.update(id=str(uuid4()), notes=payload.get("notes", ""), created_at=datetime.now(timezone.utc).isoformat(), revision=1)
            observed_utc = instant(record["observed_at"])
            text(record["source"], "source", 256)
            text(record["notes"], "notes", 4000, empty=True)
            record["unit"], record["values"] = normalized(record["kind"], record["unit"], record["values"])
            encoded = json.dumps(record, allow_nan=False)
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (record["id"], record["revision"], observed_utc, record["kind"], encoded))
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded))
            return record

    def list(self, *, from_date=None, to_date=None, kind=None, limit=100, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 500 or not 0 <= offset <= 1000000:
            raise MeasurementError("Pagination requires limit 1..500 and offset 0..1000000")
        start, end = instant(from_date) if from_date else None, instant(to_date) if to_date else None
        if start and end and start > end:
            raise MeasurementError("from must not follow to")
        if kind is not None and kind not in ("body_weight", "blood_pressure"):
            raise MeasurementError("Unknown measurement kind")
        with self.connection() as db:
            rows = db.execute("""SELECT r.data FROM revisions r
                WHERE r.revision=(SELECT MAX(s.revision) FROM revisions s WHERE s.id=r.id)
                AND (? IS NULL OR r.observed_utc>=?) AND (? IS NULL OR r.observed_utc<=?)
                AND (? IS NULL OR r.kind=?) ORDER BY r.observed_utc DESC, r.id LIMIT ? OFFSET ?""",
                (start, start, end, end, kind, kind, limit, offset)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def history(self, identity):
        with self.connection() as db:
            self._get(db, identity)
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM revisions WHERE id=? ORDER BY revision", (identity,))]

    def export(self):
        with self.connection() as db:
            history = [json.loads(row[0]) for row in db.execute("SELECT data FROM revisions ORDER BY id,revision")]
        current = {record["id"]: record for record in history}
        return {"schema_version": 1, "measurements": list(current.values()), "history": history}
