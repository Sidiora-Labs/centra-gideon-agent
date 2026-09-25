"""Source-reported epigenetic age records with immutable correction history."""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from .store import MeasurementError, text

MEASURES = ("biological_age", "chronological_age", "pace_of_aging")


def observed_date(value):
    text(value, "observed_at", 10)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MeasurementError("observed_at must be a YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise MeasurementError("observed_at must be a YYYY-MM-DD date")
    return value


def reported(value, field):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) - {"value", "unit", "scale"}
        or "value" not in value
    ):
        raise MeasurementError(
            f"{field} must contain value and one authored unit or scale"
        )
    number = value["value"]
    if (
        type(number) not in (int, float)
        or not math.isfinite(number)
        or abs(number) > 1e9
    ):
        raise MeasurementError(f"{field}.value must be a bounded finite number")
    has_unit, has_scale = "unit" in value, "scale" in value
    if has_unit == has_scale:
        raise MeasurementError(f"{field} requires exactly one authored unit or scale")
    key = "unit" if has_unit else "scale"
    return {"value": float(number), key: text(value[key], f"{field}.{key}", 120)}


def validate_record(payload, *, correction=False):
    mutable = {
        "observed_at",
        "source_report_id",
        "biological_age",
        "chronological_age",
        "pace_of_aging",
        "organ_scores",
        "notes",
    }
    required = (
        {"request_id", "revision"}
        if correction
        else {"request_id", "source_report_id", "observed_at", "source"}
    )
    allowed = mutable | required | ({"source"} if not correction else set())
    if (
        not isinstance(payload, dict)
        or set(payload) - allowed
        or not required <= set(payload)
    ):
        raise MeasurementError("Unexpected or missing epigenetic record fields")
    result = {}
    if "observed_at" in payload:
        result["observed_at"] = observed_date(payload["observed_at"])
    if "source_report_id" in payload:
        result["source_report_id"] = text(
            payload["source_report_id"], "source_report_id", 256
        )
    if "source" in payload:
        result["source"] = text(payload["source"], "source", 256)
    if "notes" in payload:
        result["notes"] = text(payload["notes"], "notes", 4000, True)
    for field in MEASURES:
        if field in payload:
            result[field] = reported(payload[field], field)
    if "organ_scores" in payload:
        scores = payload["organ_scores"]
        if not isinstance(scores, dict) or len(scores) > 100:
            raise MeasurementError(
                "organ_scores must be an object with at most 100 named scores"
            )
        result["organ_scores"] = {
            text(name, "organ score name", 120): reported(value, f"organ_scores.{name}")
            for name, value in scores.items()
        }
    return result


class EpigeneticStore:
    def __init__(self, home):
        self.path = Path(home) / "capabilities" / "wellbeing.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS epigenetic_results(id TEXT NOT NULL,revision INTEGER NOT NULL,observed_utc TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision));
                CREATE INDEX IF NOT EXISTS epigenetic_observed ON epigenetic_results(observed_utc);"""
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _current(self, db, identity):
        row = db.execute(
            "SELECT data FROM epigenetic_results WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if row is None:
            raise MeasurementError("Epigenetic record not found", 404, "not_found")
        return json.loads(row[0])

    def _write(self, kind, identity, payload, execute):
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps(
                [kind, identity, payload], sort_keys=True, allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise MeasurementError(
                "Epigenetic payload must contain finite JSON values"
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
            result = execute(db)
            encoded = json.dumps(result, allow_nan=False)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded)
            )
            return result

    def _append(self, db, record):
        db.execute(
            "INSERT INTO epigenetic_results VALUES(?,?,?,?)",
            (
                record["id"],
                record["revision"],
                record["observed_at"] + "T00:00:00+00:00",
                json.dumps(record, allow_nan=False),
            ),
        )
        return record

    def create(self, payload):
        fields = validate_record(payload)

        def execute(db):
            stamp = datetime.now(timezone.utc).isoformat()
            record = dict(
                fields,
                id=str(uuid4()),
                revision=1,
                evidence_basis="source_reported",
                created_at=stamp,
                updated_at=stamp,
            )
            record.setdefault("notes", "")
            record.setdefault("organ_scores", {})
            return self._append(db, record)

        return self._write("epigenetic-create", None, payload, execute)

    def correct(self, identity, payload):
        fields = validate_record(payload, correction=True)
        if type(payload["revision"]) is not int:
            raise MeasurementError("revision must be an integer")

        def execute(db):
            record = self._current(db, identity)
            if payload["revision"] != record["revision"]:
                raise MeasurementError(
                    "Epigenetic record changed; reload", 409, "conflict"
                )
            record.update(
                fields,
                revision=record["revision"] + 1,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            return self._append(db, record)

        return self._write("epigenetic-correct", identity, payload, execute)

    def get(self, identity):
        with self.connection() as db:
            return self._current(db, identity)

    def history(self, identity):
        with self.connection() as db:
            self._current(db, identity)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM epigenetic_results WHERE id=? ORDER BY revision",
                    (identity,),
                )
            ]

    def list(self, limit=100, offset=0):
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 500
            or not 0 <= offset <= 1000000
        ):
            raise MeasurementError("Invalid epigenetic pagination")
        with self.connection() as db:
            rows = db.execute(
                """SELECT r.data FROM epigenetic_results r WHERE r.revision=(SELECT MAX(s.revision) FROM epigenetic_results s WHERE s.id=r.id)
                ORDER BY observed_utc DESC,id LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            return [json.loads(row[0]) for row in rows]

    def export(self):
        with self.connection() as db:
            history = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM epigenetic_results ORDER BY id,revision"
                )
            ]
        current = {}
        for record in history:
            current[record["id"]] = record
        return {
            "schema_version": 1,
            "record_family": "source_reported_epigenetic_results",
            "records": list(current.values()),
            "history": history,
        }

    def import_current(self, record):
        generated = {"id", "revision", "evidence_basis", "created_at", "updated_at"}
        if (
            not isinstance(record, dict)
            or not generated <= set(record)
            or record["evidence_basis"] != "source_reported"
        ):
            raise MeasurementError("Invalid canonical epigenetic import")
        try:
            from uuid import UUID

            if str(UUID(record["id"])) != record["id"]:
                raise ValueError
            created, updated = datetime.fromisoformat(
                record["created_at"]
            ), datetime.fromisoformat(record["updated_at"])
        except (TypeError, ValueError) as exc:
            raise MeasurementError(
                "Invalid canonical epigenetic import identity or timestamp"
            ) from exc
        if (
            created.utcoffset() is None
            or updated.utcoffset() is None
            or updated < created
            or type(record["revision"]) is not int
            or record["revision"] < 1
        ):
            raise MeasurementError(
                "Invalid canonical epigenetic import revision or timestamp"
            )
        payload = {key: value for key, value in record.items() if key not in generated}
        values = validate_record({"request_id": "peer-import", **payload})
        expected = {
            **values,
            "id": record["id"],
            "revision": record["revision"],
            "evidence_basis": "source_reported",
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }
        expected.setdefault("notes", "")
        expected.setdefault("organ_scores", {})
        if expected != record:
            raise MeasurementError("Canonical epigenetic import is not exact")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision,data FROM epigenetic_results WHERE id=? ORDER BY revision DESC LIMIT 1",
                (record["id"],),
            ).fetchone()
            if row and json.loads(row[1]) == record:
                return "unchanged"
            if row and row[0] >= record["revision"]:
                raise MeasurementError(
                    "Canonical epigenetic import conflicts with local history",
                    409,
                    "conflict",
                )
            self._append(db, record)
        return "imported"
