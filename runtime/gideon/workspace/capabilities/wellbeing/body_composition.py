"""Authored body-composition observations with explicit units and immutable history."""
import json
import math
from datetime import datetime, timezone
from uuid import uuid4

from .store import MeasurementError, MeasurementStore, instant, text


MASS_FACTORS = {"kg": 1.0, "g": 0.001, "lb": 0.45359237}
TEMPERATURE_UNITS = {"C", "F", "K"}


def number(value, field, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise MeasurementError(f"{field} must be a finite number between {low} and {high}")
    return float(value)


def normalize(payload):
    if not isinstance(payload, dict) or set(payload) != {"muscle_percent", "fat_percent", "bone_mass", "temperature"}:
        raise MeasurementError("Values require muscle_percent, fat_percent, bone_mass and temperature; weight is recorded separately")
    muscle = number(payload["muscle_percent"], "muscle_percent", 0, 100)
    fat = number(payload["fat_percent"], "fat_percent", 0, 100)
    if muscle + fat > 100:
        raise MeasurementError("Reported muscle and fat percentages cannot exceed 100 in total")
    bone, temperature = payload["bone_mass"], payload["temperature"]
    if not isinstance(bone, dict) or set(bone) != {"value", "unit"} or bone.get("unit") not in MASS_FACTORS:
        raise MeasurementError("bone_mass requires value and unit kg, g or lb")
    if not isinstance(temperature, dict) or set(temperature) != {"value", "unit"} or temperature.get("unit") not in TEMPERATURE_UNITS:
        raise MeasurementError("temperature requires value and unit C, F or K")
    bone_value = number(bone["value"], "bone_mass.value", 0, 1000 / MASS_FACTORS[bone["unit"]])
    temperature_value = number(temperature["value"], "temperature.value", -459.67, 1000)
    celsius = temperature_value if temperature["unit"] == "C" else (temperature_value - 32) * 5 / 9 if temperature["unit"] == "F" else temperature_value - 273.15
    if not -273.15 <= celsius <= 200:
        raise MeasurementError("temperature is outside the supported physical range")
    original = {
        "muscle_percent": muscle,
        "fat_percent": fat,
        "bone_mass": {"value": bone_value, "unit": bone["unit"]},
        "temperature": {"value": temperature_value, "unit": temperature["unit"]},
    }
    normalized = {
        "muscle_percent": muscle,
        "fat_percent": fat,
        "bone_mass_kg": round(bone_value * MASS_FACTORS[bone["unit"]], 9),
        "temperature_c": round(celsius, 9),
    }
    return original, normalized


class BodyCompositionStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS body_composition_revisions(
                    id TEXT NOT NULL, revision INTEGER NOT NULL, observed_utc TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS body_composition_requests(
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS body_composition_observed ON body_composition_revisions(observed_utc);
            """)

    def _get(self, database, identity):
        row = database.execute("SELECT data FROM body_composition_revisions WHERE id=? ORDER BY revision DESC LIMIT 1", (identity,)).fetchone()
        if row is None:
            raise MeasurementError("Body-composition observation not found", 404, "not_found")
        return json.loads(row[0])

    def get(self, identity):
        with self.connection() as database:
            return self._get(database, identity)

    def create(self, payload):
        return self._write(None, payload)

    def correct(self, identity, payload):
        return self._write(identity, payload)

    def _write(self, identity, payload):
        create_fields = {"request_id", "observed_at", "source", "values", "notes"}
        correction_fields = {"request_id", "revision", "observed_at", "values", "notes"}
        if not isinstance(payload, dict) or set(payload) - (correction_fields if identity else create_fields):
            raise MeasurementError("Unexpected or immutable body-composition fields")
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps([identity, payload], sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise MeasurementError("Body-composition payload must contain finite JSON values") from error
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            prior = database.execute("SELECT payload,result FROM body_composition_requests WHERE id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used for another body-composition mutation", 409, "conflict")
                return json.loads(prior[1])
            if identity:
                record = self._get(database, identity)
                if type(payload.get("revision")) is not int or payload["revision"] != record["revision"]:
                    raise MeasurementError("Body-composition observation changed; reload", 409, "conflict")
                record.update({key: value for key, value in payload.items() if key not in {"request_id", "revision"}})
                record["revision"] += 1
            else:
                record = {
                    "id": str(uuid4()), "revision": 1, "observed_at": payload.get("observed_at"),
                    "source": payload.get("source"), "values": payload.get("values"),
                    "notes": payload.get("notes", ""), "created_at": datetime.now(timezone.utc).isoformat(),
                }
            observed = instant(record["observed_at"])
            text(record["source"], "source", 256)
            text(record["notes"], "notes", 4000, empty=True)
            record["original_values"], record["normalized_values"] = normalize(record.pop("values", record.get("original_values")))
            encoded = json.dumps(record, sort_keys=True, allow_nan=False)
            database.execute("INSERT INTO body_composition_revisions VALUES(?,?,?,?)", (record["id"], record["revision"], observed, encoded))
            database.execute("INSERT INTO body_composition_requests VALUES(?,?,?)", (request_id, fingerprint, encoded))
            return record

    def list(self, *, from_date=None, to_date=None, limit=100, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 500 or not 0 <= offset <= 1000000:
            raise MeasurementError("Invalid body-composition pagination")
        start, end = instant(from_date) if from_date else None, instant(to_date) if to_date else None
        if start and end and start > end:
            raise MeasurementError("from must not follow to")
        with self.connection() as database:
            rows = database.execute("""SELECT r.data FROM body_composition_revisions r WHERE
                r.revision=(SELECT MAX(s.revision) FROM body_composition_revisions s WHERE s.id=r.id)
                AND (? IS NULL OR observed_utc>=?) AND (? IS NULL OR observed_utc<=?)
                ORDER BY observed_utc DESC,id LIMIT ? OFFSET ?""", (start, start, end, end, limit, offset)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def history(self, identity):
        with self.connection() as database:
            self._get(database, identity)
            return [json.loads(row[0]) for row in database.execute("SELECT data FROM body_composition_revisions WHERE id=? ORDER BY revision", (identity,))]

    def export(self):
        with self.connection() as database:
            history = [json.loads(row[0]) for row in database.execute("SELECT data FROM body_composition_revisions ORDER BY id,revision")]
        current = {record["id"]: record for record in history}
        return {"schema_version": 1, "body_composition": list(current.values()), "history": history}
