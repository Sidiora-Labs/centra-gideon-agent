"""Versioned authored eye prescriptions without medical interpretation."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from .store import MeasurementError, MeasurementStore, text


SCHEMA = "gideon.eye-prescriptions"
EYE_FIELDS = {"sphere", "sphere_unit", "cylinder", "cylinder_unit", "axis", "axis_unit"}


def _date(value):
    text(value, "observed_date", 10)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MeasurementError("observed_date must be an ISO calendar date") from exc
    if parsed.isoformat() != value:
        raise MeasurementError("observed_date must be an ISO calendar date")
    return value


def _number(value, field, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise MeasurementError(f"{field} must be a finite number from {low} to {high}")
    return float(value)


def _eye(value, side):
    if not isinstance(value, dict) or set(value) != EYE_FIELDS:
        raise MeasurementError(f"{side} eye requires sphere, cylinder and axis with explicit units")
    if value["sphere_unit"] != "D" or value["cylinder_unit"] != "D" or value["axis_unit"] != "degrees":
        raise MeasurementError("Sphere and cylinder require D; axis requires degrees")
    return {
        "sphere": _number(value["sphere"], f"{side} sphere", -40, 40),
        "sphere_unit": "D",
        "cylinder": _number(value["cylinder"], f"{side} cylinder", -20, 20),
        "cylinder_unit": "D",
        "axis": _number(value["axis"], f"{side} axis", 0, 180),
        "axis_unit": "degrees",
    }


def _validated(payload, correction=False):
    if not isinstance(payload, dict):
        raise MeasurementError("Eye prescription must be an object")
    allowed = {"request_id", "revision", "observed_date", "source", "notes", "left", "right"}
    if set(payload) - allowed:
        raise MeasurementError("Unknown eye prescription fields")
    required = {"request_id", "revision"} if correction else {"request_id", "observed_date", "source", "left", "right"}
    if required - set(payload):
        raise MeasurementError("Eye prescription is missing required fields")
    result = {}
    if "observed_date" in payload:
        result["observed_date"] = _date(payload["observed_date"])
    if "source" in payload:
        result["source"] = text(payload["source"], "source", 256)
    if "notes" in payload:
        result["notes"] = text(payload["notes"], "notes", 4000, empty=True)
    for side in ("left", "right"):
        if side in payload:
            result[side] = _eye(payload[side], side)
    text(payload.get("request_id"), "request_id", 128)
    if correction and (type(payload.get("revision")) is not int or payload["revision"] < 1):
        raise MeasurementError("revision must be a positive integer")
    return result


class EyePrescriptionStore(MeasurementStore):
    def __init__(self, home: Path):
        super().__init__(home)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS eye_prescription_revisions(
                    id TEXT NOT NULL, revision INTEGER NOT NULL, observed_date TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS eye_prescription_requests(
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS eye_prescription_observed
                    ON eye_prescription_revisions(observed_date);
            """)

    def _get(self, db, identity):
        row = db.execute(
            "SELECT data FROM eye_prescription_revisions WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if row is None:
            raise MeasurementError("Eye prescription not found", 404, "not_found")
        return json.loads(row[0])

    def get(self, identity):
        with self.connection() as db:
            return self._get(db, identity)

    def _replay(self, db, request_id, fingerprint):
        row = db.execute("SELECT payload,result FROM eye_prescription_requests WHERE id=?", (request_id,)).fetchone()
        if row and row[0] != fingerprint:
            raise MeasurementError("Request ID already used for a different mutation", 409, "conflict")
        return json.loads(row[1]) if row else None

    def _remember(self, db, request_id, fingerprint, record):
        encoded = json.dumps(record, sort_keys=True, allow_nan=False)
        db.execute("INSERT INTO eye_prescription_requests VALUES(?,?,?)", (request_id, fingerprint, encoded))
        return record

    def create(self, payload):
        values = _validated(payload)
        fingerprint = json.dumps(["create", payload], sort_keys=True, allow_nan=False)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, payload["request_id"], fingerprint)
            if replay is not None:
                return replay
            now = datetime.now(timezone.utc).isoformat()
            record = {"id": uuid4().hex, **values, "notes": values.get("notes", ""),
                      "revision": 1, "created_at": now, "updated_at": now}
            encoded = json.dumps(record, sort_keys=True, allow_nan=False)
            db.execute("INSERT INTO eye_prescription_revisions VALUES(?,?,?,?)",
                       (record["id"], 1, record["observed_date"], encoded))
            return self._remember(db, payload["request_id"], fingerprint, record)

    def correct(self, identity, payload):
        values = _validated(payload, correction=True)
        if "source" in values:
            raise MeasurementError("Eye prescription source is immutable")
        fingerprint = json.dumps(["correct", identity, payload], sort_keys=True, allow_nan=False)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, payload["request_id"], fingerprint)
            if replay is not None:
                return replay
            record = self._get(db, identity)
            if payload["revision"] != record["revision"]:
                raise MeasurementError("Eye prescription changed; reload", 409, "conflict")
            record.update(values)
            record["revision"] += 1
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            encoded = json.dumps(record, sort_keys=True, allow_nan=False)
            db.execute("INSERT INTO eye_prescription_revisions VALUES(?,?,?,?)",
                       (identity, record["revision"], record["observed_date"], encoded))
            return self._remember(db, payload["request_id"], fingerprint, record)

    def list(self, *, from_date=None, to_date=None, limit=100, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 500 or not 0 <= offset <= 1000000:
            raise MeasurementError("Invalid eye prescription pagination")
        start, end = _date(from_date) if from_date else None, _date(to_date) if to_date else None
        if start and end and start > end:
            raise MeasurementError("from must not follow to")
        with self.connection() as db:
            rows = db.execute("""SELECT r.data FROM eye_prescription_revisions r
                WHERE r.revision=(SELECT MAX(s.revision) FROM eye_prescription_revisions s WHERE s.id=r.id)
                AND (? IS NULL OR r.observed_date>=?) AND (? IS NULL OR r.observed_date<=?)
                ORDER BY r.observed_date DESC,r.id LIMIT ? OFFSET ?""",
                (start, start, end, end, limit, offset)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def history(self, identity):
        with self.connection() as db:
            self._get(db, identity)
            return [json.loads(row[0]) for row in db.execute(
                "SELECT data FROM eye_prescription_revisions WHERE id=? ORDER BY revision", (identity,))]

    def export(self):
        with self.connection() as db:
            history = [json.loads(row[0]) for row in db.execute(
                "SELECT data FROM eye_prescription_revisions ORDER BY id,revision")]
        current = {}
        for record in history:
            current[record["id"]] = record
        return {"schema": SCHEMA, "version": 1, "prescriptions": list(current.values()), "history": history}

    def import_current(self, record):
        fields = {"id", "revision", "observed_date", "source", "notes", "left", "right", "created_at", "updated_at"}
        if not isinstance(record, dict) or set(record) != fields or not re.fullmatch(r"[0-9a-f]{32}", record.get("id", "")):
            raise MeasurementError("Invalid canonical eye prescription import")
        try:
            created, updated = datetime.fromisoformat(record["created_at"]), datetime.fromisoformat(record["updated_at"])
        except (TypeError, ValueError) as exc:
            raise MeasurementError("Invalid canonical eye prescription timestamp") from exc
        if created.utcoffset() is None or updated.utcoffset() is None or updated < created or type(record["revision"]) is not int or record["revision"] < 1:
            raise MeasurementError("Invalid canonical eye prescription revision or timestamp")
        authored = _validated({"request_id":"peer-import", **{key:record[key] for key in ("observed_date","source","notes","left","right")}})
        if {**authored, "id":record["id"], "revision":record["revision"], "created_at":record["created_at"], "updated_at":record["updated_at"]} != record:
            raise MeasurementError("Canonical eye prescription import is not exact")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision,data FROM eye_prescription_revisions WHERE id=? ORDER BY revision DESC LIMIT 1", (record["id"],)).fetchone()
            if row and json.loads(row[1]) == record:
                return "unchanged"
            if row and row[0] >= record["revision"]:
                raise MeasurementError("Canonical eye prescription import conflicts with local history", 409, "conflict")
            db.execute("INSERT INTO eye_prescription_revisions VALUES(?,?,?,?)", (record["id"], record["revision"], record["observed_date"], json.dumps(record, sort_keys=True, allow_nan=False)))
        return "imported"
