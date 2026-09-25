"""Deterministic laboratory imports backed by the measurement ledger and artifacts."""

import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone

from gideon.workspace.artifacts.native import NativeArtifactProvider

from .store import MeasurementError, MeasurementStore, instant, text


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def number(value, field, optional=False):
    if optional and value is None:
        return None
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1e12
    ):
        raise MeasurementError(f"{field} must be a finite number between 0 and 1e12")
    return float(value)


def validate_row(row):
    fields = {
        "analyte",
        "observed_at",
        "value",
        "unit",
        "reference_low",
        "reference_high",
        "notes",
        "external_id",
    }
    if not isinstance(row, dict) or set(row) - fields:
        raise MeasurementError("Unexpected laboratory fields")
    result = {
        key: text(row.get(key), key, limit)
        for key, limit in (("analyte", 120), ("observed_at", 64), ("unit", 64))
    }
    instant(result["observed_at"])
    result.update(
        value=number(row.get("value"), "value"),
        reference_low=number(row.get("reference_low"), "reference_low", True),
        reference_high=number(row.get("reference_high"), "reference_high", True),
    )
    if (
        result["reference_low"] is not None
        and result["reference_high"] is not None
        and result["reference_low"] > result["reference_high"]
    ):
        raise MeasurementError("reference_low must not exceed reference_high")
    result["notes"] = text(row.get("notes", ""), "notes", 4000, True)
    result["external_id"] = text(row.get("external_id", ""), "external_id", 256, True)
    return result


def parse(payload):
    if not isinstance(payload, dict) or set(payload) - {
        "filename",
        "format",
        "content",
        "source",
        "preview_id",
        "request_id",
    }:
        raise MeasurementError(
            "Import must contain only filename, format, content and source"
        )
    filename = text(payload.get("filename"), "filename", 200)
    source = text(payload.get("source"), "source", 256)
    content = text(payload.get("content"), "content", 500000)
    if len(content.encode()) > 500000:
        raise MeasurementError("Import content must be at most 500000 UTF-8 bytes")
    format_ = payload.get("format")
    if format_ not in ("csv", "json"):
        raise MeasurementError("Import format must be csv or json")
    try:
        if format_ == "json":
            rows = json.loads(content)
        else:
            reader = csv.DictReader(io.StringIO(content))
            header = reader.fieldnames or []
            if len(header) != len(set(header)) or not {
                "analyte",
                "observed_at",
                "value",
                "unit",
            } <= set(header):
                raise MeasurementError(
                    "CSV needs unique analyte, observed_at, value and unit headers"
                )
            rows = list(reader)
            for index, row in enumerate(rows, 1):
                for key in ("value", "reference_low", "reference_high"):
                    if key in row:
                        try:
                            row[key] = (
                                None if row[key] in ("", None) else float(row[key])
                            )
                        except (ValueError, TypeError) as exc:
                            raise MeasurementError(
                                f"Row {index}: {key} must be numeric"
                            ) from exc
    except (ValueError, csv.Error) as exc:
        raise MeasurementError(str(exc)) from exc
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise MeasurementError("Import must contain 1..1000 laboratory rows")
    valid = []
    for index, row in enumerate(rows, 1):
        try:
            valid.append(validate_row(row))
        except MeasurementError as exc:
            raise MeasurementError(f"Row {index}: {exc}") from exc
    import_id = digest([filename, format_, content, source])
    return import_id, valid


class LabStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        self.artifacts = NativeArtifactProvider(
            root=self.path.parent.parent / "artifacts"
        )
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS lab_revisions (
                    id TEXT NOT NULL, revision INTEGER NOT NULL, observed_utc TEXT NOT NULL,
                    analyte TEXT NOT NULL, unit TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS lab_imports (id TEXT PRIMARY KEY, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lab_sources (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
            """)

    def _identity(self, source, row):
        identity = (
            digest([source, row["external_id"]])
            if row["external_id"]
            else digest([source, row])
        )
        return identity, digest(row)

    def preview(self, payload):
        identity, rows = parse(payload)
        duplicates, seen = 0, {}
        with self.connection() as db:
            for row in rows:
                key, fingerprint = self._identity(payload["source"], row)
                existing = db.execute(
                    "SELECT fingerprint FROM lab_sources WHERE id=?", (key,)
                ).fetchone()
                prior = existing[0] if existing else seen.get(key)
                if prior and prior != fingerprint:
                    raise MeasurementError(
                        "External record ID conflicts with a different source row",
                        409,
                        "conflict",
                    )
                duplicates += int(prior is not None)
                seen[key] = fingerprint
        return {"preview_id": identity, "rows": rows, "duplicates": duplicates}

    def commit(self, payload):
        preview = self.preview(payload)
        if payload.get("preview_id") != preview["preview_id"]:
            raise MeasurementError(
                "Preview changed; preview this input again", 409, "conflict"
            )
        request_id = text(payload.get("request_id"), "request_id", 128)
        import_id = preview["preview_id"]
        fingerprint = json.dumps(["lab-import", import_id])
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used", 409, "conflict")
                return json.loads(prior[1])
            existing = db.execute(
                "SELECT receipt FROM lab_imports WHERE id=?", (import_id,)
            ).fetchone()
            if existing:
                db.execute(
                    "INSERT INTO requests VALUES(?,?,?)",
                    (request_id, fingerprint, existing[0]),
                )
                return json.loads(existing[0])
            slug = "lab-source-" + import_id
            artifact = self.artifacts.get(slug, version=1)
            if artifact is None:
                artifact = self.artifacts.create(
                    name=payload["filename"],
                    content=payload["content"],
                    kind=payload["format"],
                    source="import",
                    slug=slug,
                    readonly=True,
                )
            if artifact.content.replace("\r\n", "\n").replace("\r", "\n") != payload[
                "content"
            ].replace("\r\n", "\n").replace("\r", "\n"):
                raise MeasurementError(
                    "Original artifact content differs", 409, "conflict"
                )
            reference = {
                "slug": artifact.slug,
                "version": 1,
                "sha256": hashlib.sha256(payload["content"].encode()).hexdigest(),
            }
            reference["filename"] = (
                f"original@{reference['sha256']}.{payload['format']}"
            )
            if not self.artifacts.store_version_file(
                artifact.slug, reference["filename"], payload["content"].encode()
            ):
                raise MeasurementError("Original attachment could not be stored")
            self._original(reference)
            records, added, duplicates = [], 0, 0
            for index, row in enumerate(preview["rows"], 1):
                identity, row_fingerprint = self._identity(payload["source"], row)
                prior = db.execute(
                    "SELECT fingerprint FROM lab_sources WHERE id=?", (identity,)
                ).fetchone()
                if prior:
                    if prior[0] != row_fingerprint:
                        raise MeasurementError(
                            "Concurrent source record conflict", 409, "conflict"
                        )
                    duplicates += 1
                    records.append(self._get(db, identity))
                    continue
                record = dict(
                    row,
                    id=identity,
                    kind="laboratory",
                    source=payload["source"],
                    artifact=reference,
                    row_index=index,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    revision=1,
                )
                self._append(db, record)
                db.execute(
                    "INSERT INTO lab_sources VALUES(?,?)", (identity, row_fingerprint)
                )
                records.append(record)
                added += 1
            receipt = {
                "import_id": import_id,
                "artifact": reference,
                "records": records,
                "added": added,
                "duplicates": duplicates,
            }
            encoded = json.dumps(receipt)
            db.execute("INSERT INTO lab_imports VALUES(?,?)", (import_id, encoded))
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded)
            )
            return receipt

    def _original(self, reference):
        path = (
            self.path.parent.parent
            / "artifacts"
            / reference["slug"]
            / "versions"
            / reference["filename"]
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise MeasurementError(
                "Original attachment is unavailable", 404, "not_found"
            ) from exc
        if hashlib.sha256(data).hexdigest() != reference["sha256"]:
            raise MeasurementError("Original attachment hash mismatch", 409, "conflict")
        return data

    def original(self, identity):
        return self._original(self.get(identity)["artifact"])

    def _append(self, db, record):
        db.execute(
            "INSERT INTO lab_revisions VALUES(?,?,?,?,?,?)",
            (
                record["id"],
                record["revision"],
                instant(record["observed_at"]),
                record["analyte"],
                record["unit"],
                json.dumps(record),
            ),
        )

    def _get(self, db, identity):
        row = db.execute(
            "SELECT data FROM lab_revisions WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if row is None:
            raise MeasurementError("Laboratory record not found", 404, "not_found")
        return json.loads(row[0])

    def correct(self, identity, payload):
        if not isinstance(payload, dict) or set(payload) - {
            "request_id",
            "revision",
            "value",
            "reference_low",
            "reference_high",
            "notes",
        }:
            raise MeasurementError(
                "Only value, reference bounds and notes can be corrected"
            )
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps(
                ["lab-correct", identity, payload], sort_keys=True, allow_nan=False
            )
        except (ValueError, TypeError) as exc:
            raise MeasurementError(
                "Correction must contain finite JSON values"
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
                type(payload.get("revision")) is not int
                or payload["revision"] != record["revision"]
            ):
                raise MeasurementError(
                    "Laboratory record changed; reload", 409, "conflict"
                )
            record.update(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in ("request_id", "revision")
                }
            )
            fields = {
                key: record[key]
                for key in (
                    "analyte",
                    "observed_at",
                    "value",
                    "unit",
                    "reference_low",
                    "reference_high",
                    "notes",
                    "external_id",
                )
            }
            record.update(validate_row(fields))
            record["revision"] += 1
            self._append(db, record)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(record)),
            )
            return record

    def list(self, *, analyte=None, from_date=None, to_date=None, limit=100, offset=0):
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 500
            or not 0 <= offset <= 1000000
        ):
            raise MeasurementError("Invalid pagination")
        start, end = instant(from_date) if from_date else None, (
            instant(to_date) if to_date else None
        )
        if start and end and start > end:
            raise MeasurementError("from must not follow to")
        with self.connection() as db:
            rows = db.execute(
                """SELECT r.data FROM lab_revisions r WHERE
                r.revision=(SELECT MAX(s.revision) FROM lab_revisions s WHERE s.id=r.id)
                AND (? IS NULL OR analyte=?) AND (? IS NULL OR observed_utc>=?)
                AND (? IS NULL OR observed_utc<=?) ORDER BY observed_utc DESC,id LIMIT ? OFFSET ?""",
                (analyte, analyte, start, start, end, end, limit, offset),
            )
            return [json.loads(row[0]) for row in rows]

    def history(self, identity):
        with self.connection() as db:
            self._get(db, identity)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM lab_revisions WHERE id=? ORDER BY revision",
                    (identity,),
                )
            ]

    def trends(self, analyte, unit):
        text(analyte, "analyte", 120)
        text(unit, "unit", 64)
        with self.connection() as db:
            rows = db.execute(
                """SELECT r.data FROM lab_revisions r WHERE analyte=? AND unit=?
                AND r.revision=(SELECT MAX(s.revision) FROM lab_revisions s WHERE s.id=r.id)
                ORDER BY observed_utc DESC,id LIMIT 500""",
                (analyte, unit),
            ).fetchall()
            return [json.loads(row[0]) for row in reversed(rows)]
