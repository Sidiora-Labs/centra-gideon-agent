"""Authored lifestyle-profile observations without diagnostic interpretation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .store import MeasurementError, MeasurementStore, instant, text

SCHEMA = "gideon.wellbeing.lifestyle-profile"
SMOKING = {"never", "former", "current", "unknown"}
ALCOHOL_UNITS = {"standard_drinks_per_day", "g_per_day", "ml_ethanol_per_day"}
FIELDS = {
    "observed_at",
    "source",
    "reported_sex",
    "sex_source",
    "smoking_status",
    "diet_quality",
    "stress",
    "reported_bmi",
    "condition_labels",
    "reported_daily_alcohol",
}
CORRECTABLE = FIELDS - {"observed_at", "source"}


def _number(value, field, low, high):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise MeasurementError(f"{field} must be a finite number from {low} to {high}")
    return float(value)


def _scaled(value, field):
    if (
        not isinstance(value, dict)
        or set(value) != {"value", "scale"}
        or not isinstance(value["scale"], dict)
        or set(value["scale"]) != {"minimum", "maximum", "label"}
    ):
        raise MeasurementError(
            f"{field} requires value and a declared minimum, maximum and label"
        )
    minimum = _number(
        value["scale"]["minimum"], field + ".scale.minimum", -1000000, 1000000
    )
    maximum = _number(
        value["scale"]["maximum"], field + ".scale.maximum", -1000000, 1000000
    )
    if minimum >= maximum:
        raise MeasurementError(f"{field} scale minimum must be below maximum")
    score = _number(value["value"], field + ".value", minimum, maximum)
    return {
        "value": score,
        "scale": {
            "minimum": minimum,
            "maximum": maximum,
            "label": text(value["scale"]["label"], field + ".scale.label", 120),
        },
    }


def _alcohol(value):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"value", "unit"}
        or value["unit"] not in ALCOHOL_UNITS
    ):
        raise MeasurementError(
            "reported_daily_alcohol requires a supported explicit unit"
        )
    return {
        "value": _number(value["value"], "reported_daily_alcohol.value", 0, 1000),
        "unit": value["unit"],
    }


def validate(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise MeasurementError(
            "Lifestyle observation fields are incomplete or unsupported"
        )
    observed_at = text(value["observed_at"], "observed_at", 64)
    instant(observed_at)
    source = text(value["source"], "source", 256)
    reported_sex = text(value["reported_sex"], "reported_sex", 100)
    sex_source = text(value["sex_source"], "sex_source", 256)
    if value["smoking_status"] not in SMOKING:
        raise MeasurementError(
            "smoking_status must be never, former, current or unknown"
        )
    labels = value["condition_labels"]
    if not isinstance(labels, list) or len(labels) > 50:
        raise MeasurementError("condition_labels must contain at most 50 labels")
    labels = [text(item, "condition_label", 120) for item in labels]
    if len(labels) != len(set(label.casefold() for label in labels)):
        raise MeasurementError("condition_labels must be unique")
    return {
        "observed_at": observed_at,
        "source": source,
        "reported_sex": reported_sex,
        "sex_source": sex_source,
        "smoking_status": value["smoking_status"],
        "diet_quality": _scaled(value["diet_quality"], "diet_quality"),
        "stress": _scaled(value["stress"], "stress"),
        "reported_bmi": _number(value["reported_bmi"], "reported_bmi", 5, 100),
        "condition_labels": labels,
        "reported_daily_alcohol": _alcohol(value["reported_daily_alcohol"]),
    }


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class LifestyleProfileStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS lifestyle_profile_revisions(
                id TEXT NOT NULL,revision INTEGER NOT NULL,observed_utc TEXT NOT NULL,data TEXT NOT NULL,
                PRIMARY KEY(id,revision));""")

    def _get(self, db, identity):
        row = db.execute(
            "SELECT data FROM lifestyle_profile_revisions WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if row is None:
            raise MeasurementError("Lifestyle observation not found", 404, "not_found")
        return json.loads(row[0])

    def _append(self, db, record):
        db.execute(
            "INSERT INTO lifestyle_profile_revisions VALUES(?,?,?,?)",
            (
                record["id"],
                record["revision"],
                instant(record["observed_at"]),
                json.dumps(record, sort_keys=True),
            ),
        )

    def create(self, payload):
        if not isinstance(payload, dict) or set(payload) != FIELDS | {"request_id"}:
            raise MeasurementError(
                "Lifestyle creation requires one exact authored observation"
            )
        request_id = text(payload["request_id"], "request_id", 128)
        values = validate({key: payload[key] for key in FIELDS})
        fingerprint = json.dumps(["lifestyle-create", _fingerprint(values)])
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used", 409, "conflict")
                return json.loads(prior[1])
            stamp = datetime.now(timezone.utc).isoformat()
            record = {
                **values,
                "id": str(uuid4()),
                "revision": 1,
                "created_at": stamp,
                "updated_at": stamp,
            }
            self._append(db, record)
            encoded = json.dumps(record, sort_keys=True)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded)
            )
            return record

    def correct(self, identity, payload):
        if (
            not isinstance(payload, dict)
            or set(payload) - (CORRECTABLE | {"request_id", "revision"})
            or not {"request_id", "revision"} <= set(payload)
        ):
            raise MeasurementError("Lifestyle correction contains unsupported fields")
        request_id = text(payload["request_id"], "request_id", 128)
        try:
            fingerprint = json.dumps(
                ["lifestyle-correct", identity, payload],
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise MeasurementError(
                "Lifestyle correction must contain finite JSON values"
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
            record = self._get(db, identity)
            if (
                type(payload["revision"]) is not int
                or payload["revision"] != record["revision"]
            ):
                raise MeasurementError(
                    "Lifestyle observation changed; reload", 409, "conflict"
                )
            values = {key: record[key] for key in FIELDS}
            values.update(
                {key: value for key, value in payload.items() if key in CORRECTABLE}
            )
            record.update(
                validate(values),
                revision=record["revision"] + 1,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self._append(db, record)
            encoded = json.dumps(record, sort_keys=True)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded)
            )
            return record

    def get(self, identity):
        with self.connection() as db:
            return self._get(db, identity)

    def list(self):
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    """SELECT r.data FROM lifestyle_profile_revisions r
                WHERE r.revision=(SELECT MAX(s.revision) FROM lifestyle_profile_revisions s WHERE s.id=r.id)
                ORDER BY observed_utc DESC,id"""
                )
            ]

    def history(self, identity):
        with self.connection() as db:
            self._get(db, identity)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM lifestyle_profile_revisions WHERE id=? ORDER BY revision",
                    (identity,),
                )
            ]

    def export(self):
        records = self.list()
        with self.connection() as db:
            history = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM lifestyle_profile_revisions ORDER BY id,revision"
                )
            ]
        return {
            "schema": SCHEMA,
            "schema_version": 1,
            "records": records,
            "history": history,
        }

    def import_current(self, record):
        fields = FIELDS | {"id", "revision", "created_at", "updated_at"}
        if not isinstance(record, dict) or set(record) != fields:
            raise MeasurementError("Invalid canonical lifestyle import")
        try:
            if str(UUID(record["id"])) != record["id"]:
                raise ValueError
            created = datetime.fromisoformat(record["created_at"])
            updated = datetime.fromisoformat(record["updated_at"])
        except (TypeError, ValueError) as exc:
            raise MeasurementError(
                "Invalid canonical lifestyle identity or timestamp"
            ) from exc
        if (
            created.utcoffset() is None
            or updated.utcoffset() is None
            or updated < created
            or type(record["revision"]) is not int
            or record["revision"] < 1
        ):
            raise MeasurementError("Invalid canonical lifestyle revision or timestamp")
        authored = validate({key: record[key] for key in FIELDS})
        if {
            **authored,
            "id": record["id"],
            "revision": record["revision"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        } != record:
            raise MeasurementError("Canonical lifestyle import is not exact")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision,data FROM lifestyle_profile_revisions WHERE id=? ORDER BY revision DESC LIMIT 1",
                (record["id"],),
            ).fetchone()
            if row and json.loads(row[1]) == record:
                return "unchanged"
            if row and row[0] >= record["revision"]:
                raise MeasurementError(
                    "Canonical lifestyle import conflicts with local history",
                    409,
                    "conflict",
                )
            self._append(db, record)
        return "imported"
