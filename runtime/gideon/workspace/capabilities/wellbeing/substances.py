"""Recorded alcohol and labeled nicotine quantities with immutable revisions."""

import json
import math
from datetime import datetime, timedelta, timezone as utc
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import MeasurementError, MeasurementStore, instant, text


def amount(value, label, low=0, high=10000, positive=False):
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high or (positive and value == 0):
        raise MeasurementError(f"{label} must be finite in the permitted range")
    return float(value)


def quantities(kind, details, count=1):
    keys = {"volume_ml", "abv_percent"} if kind == "alcohol" else {"mg_per_unit"}
    if kind not in ("alcohol", "nicotine") or not isinstance(details, dict) or set(details) != keys:
        raise MeasurementError("Details must match alcohol or nicotine kind")
    if kind == "alcohol":
        volume = amount(details["volume_ml"], "volume_ml", high=5000, positive=True)
        abv = amount(details["abv_percent"], "abv_percent", high=100)
        return {"volume_ml": volume, "abv_percent": abv}, volume * abv / 100 * 0.789 * count, None
    mg = amount(details["mg_per_unit"], "mg_per_unit", high=1000)
    return {"mg_per_unit": mg}, None, mg * count


class ConsumptionStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            for entity in ("entries", "presets"):
                db.execute(f"CREATE TABLE IF NOT EXISTS substance_{entity} (id TEXT NOT NULL,revision INTEGER NOT NULL,kind TEXT NOT NULL,observed_utc TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision))")

    def _record(self, db, entity, identity):
        row = db.execute(f"SELECT data FROM substance_{entity} WHERE id=? ORDER BY revision DESC LIMIT 1", (identity,)).fetchone()
        if row is None:
            raise MeasurementError("Substance record not found", 404, "not_found")
        return json.loads(row[0])

    def _write(self, entity, payload, identity=None, delete=False):
        if not isinstance(payload, dict):
            raise MeasurementError("Record must be an object")
        mutable = {"name", "details"} | ({"observed_at", "count", "notes"} if entity == "entries" else set())
        allowed = {"request_id", "revision"} if delete else {"request_id"} | mutable | ({"revision"} if identity else {"kind"} | ({"source", "preset_id"} if entity == "entries" else set()))
        if set(payload) - allowed:
            raise MeasurementError("Unknown or immutable record fields")
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps(["substance", entity, identity, delete, payload], sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise MeasurementError("Record must contain finite JSON values") from exc
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT payload,result FROM requests WHERE id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used", 409, "conflict")
                return json.loads(prior[1])
            if identity:
                record = self._record(db, entity, identity)
                if record["deleted"] or type(payload.get("revision")) is not int or record["revision"] != payload["revision"]:
                    raise MeasurementError("Record changed or deleted; reload", 409, "conflict")
                record.update({key: payload[key] for key in mutable if key in payload})
                record["revision"] += 1
                record["deleted"] = delete
            else:
                record = {key: payload.get(key) for key in mutable | {"kind"}}
                record.update(id=str(uuid4()), revision=1, created_at=datetime.now(utc.utc).isoformat(), deleted=False)
                if entity == "entries":
                    record.update(source=payload.get("source"), notes=payload.get("notes", ""), preset_id=None, preset_revision=None)
                    if payload.get("preset_id") is not None:
                        if set(payload) & {"name", "kind", "details"}:
                            raise MeasurementError("Preset entries must not override the preset snapshot")
                        preset = self._record(db, "presets", payload["preset_id"])
                        if preset["deleted"]:
                            raise MeasurementError("Preset is deleted", 409, "conflict")
                        record.update({key: preset[key] for key in ("name", "kind", "details")})
                        record.update(preset_id=preset["id"], preset_revision=preset["revision"])
            text(record["name"], "name", 200)
            count = amount(record.get("count", 1), "count", high=1000, positive=True)
            details, ethanol, nicotine = quantities(record["kind"], record["details"], count)
            record["details"] = details
            observed = ""
            if entity == "entries":
                observed = instant(record["observed_at"])
                text(record["source"], "source", 256)
                text(record["notes"], "notes", 4000, True)
                record.update(count=count, ethanol_g=ethanol, nicotine_mg=nicotine)
            encoded = json.dumps(record, allow_nan=False)
            db.execute(f"INSERT INTO substance_{entity} VALUES(?,?,?,?,?)", (record["id"], record["revision"], record["kind"], observed, encoded))
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded))
            return record

    def create_entry(self, payload):
        return self._write("entries", payload)

    def correct_entry(self, identity, payload):
        return self._write("entries", payload, identity)

    def delete_entry(self, identity, payload):
        return self._write("entries", payload, identity, True)

    def create_preset(self, payload):
        return self._write("presets", payload)

    def update_preset(self, identity, payload):
        return self._write("presets", payload, identity)

    def delete_preset(self, identity, payload):
        return self._write("presets", payload, identity, True)

    def get_entry(self, identity):
        with self.connection() as db:
            return self._record(db, "entries", identity)

    def history_entry(self, identity):
        with self.connection() as db:
            self._record(db, "entries", identity)
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM substance_entries WHERE id=? ORDER BY revision", (identity,))]

    def _list(self, entity, kind=None, start=None, end=None, limit=100, offset=0):
        if kind is not None and kind not in ("alcohol", "nicotine"):
            raise MeasurementError("Unknown substance kind")
        with self.connection() as db:
            rows = db.execute(f"""SELECT r.data FROM substance_{entity} r WHERE
                r.revision=(SELECT MAX(s.revision) FROM substance_{entity} s WHERE s.id=r.id)
                AND json_extract(r.data,'$.deleted')=0 AND (? IS NULL OR r.kind=?)
                AND (? IS NULL OR observed_utc>=?) AND (? IS NULL OR observed_utc<=?)
                ORDER BY observed_utc DESC,id LIMIT ? OFFSET ?""", (kind, kind, start, start, end, end, limit, offset))
            return [json.loads(row[0]) for row in rows]

    def list_entries(self, *, kind=None, from_date=None, to_date=None, limit=100, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 500 or not 0 <= offset <= 1000000:
            raise MeasurementError("Invalid pagination")
        start, end = instant(from_date) if from_date else None, instant(to_date) if to_date else None
        if start and end and start > end:
            raise MeasurementError("from must not follow to")
        return self._list("entries", kind, start, end, limit, offset)

    def list_presets(self, kind=None):
        return self._list("presets", kind, limit=500)

    def summary(self, timezone="UTC", days=30, as_of=None):
        if type(days) is not int or not 1 <= days <= 366:
            raise MeasurementError("days must be 1..366")
        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise MeasurementError("Unknown timezone") from exc
        now = datetime.fromisoformat(instant(as_of)) if as_of else datetime.now(utc.utc)
        last = now.astimezone(zone).date()
        first = last - timedelta(days=days - 1)
        start = datetime.combine(first, datetime.min.time(), zone).astimezone(utc.utc).isoformat(timespec="microseconds")
        rows = self._list("entries", start=start, end=now.isoformat(timespec="microseconds"), limit=-1)
        buckets = {str(first + timedelta(days=index)): {"date": str(first + timedelta(days=index)), "entry_count": 0, "ethanol_g": None, "nicotine_mg": None} for index in range(days)}
        for row in rows:
            day = str(datetime.fromisoformat(instant(row["observed_at"])).astimezone(zone).date())
            bucket = buckets[day]
            bucket["entry_count"] += 1
            field = "ethanol_g" if row["kind"] == "alcohol" else "nicotine_mg"
            bucket[field] = (bucket[field] or 0) + row[field]
        totals, averages, logged = {}, {}, {}
        for kind, field in (("alcohol", "ethanol_g"), ("nicotine", "nicotine_mg")):
            values = [bucket[field] for bucket in buckets.values() if bucket[field] is not None]
            logged[kind] = len(values)
            totals[field] = sum(values) if values else None
            averages[field] = sum(values) / len(values) if values else None
        return {"timezone": timezone, "window_days": days, "as_of": now.isoformat(), "days": list(buckets.values()), "totals": totals, "logged_days": logged, "averages_per_logged_day": averages}
