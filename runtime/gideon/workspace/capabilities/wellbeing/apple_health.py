"""Local Apple export imports with original artifacts and atomic record commits."""

import base64
import binascii
import hashlib
import io
import json
import math
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import PurePosixPath
from xml.etree import ElementTree

from .labs import LabStore, digest, validate_row
from .store import MeasurementError, instant, text

MAX_BYTES = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_RECORDS = 20000


def timestamp(value):
    text(value, "timestamp", 80)
    normalized = value.strip().replace(" +", "+").replace(" -", "-")
    instant(normalized)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).isoformat()


def quantity(value):
    if isinstance(value, bool):
        raise MeasurementError("Boolean quantity is not numeric")
    try:
        numeric = float(value)
    except (ValueError, TypeError) as exc:
        raise MeasurementError("Quantity must be numeric") from exc
    if not math.isfinite(numeric) or abs(numeric) > 1e12:
        raise MeasurementError("Quantity must be finite within +/-1e12")
    return numeric


class ExportParser:
    def __init__(self):
        self.metrics, self.labs, self.skipped = [], [], Counter()

    def metric(self, name, observed, unit, value, device="", end=None, stage=None):
        row = dict(
            metric=text(name, "metric", 200),
            observed_at=timestamp(observed),
            unit=text(unit, "unit", 64),
            value=quantity(value),
            device_source=text(device or "", "device_source", 256, True),
            end_at=timestamp(end) if end else None,
            stage=stage,
        )
        if row["end_at"] and instant(row["end_at"]) < instant(row["observed_at"]):
            raise MeasurementError("Metric end precedes observation")
        self.metrics.append(row)
        self.bound()

    def bound(self):
        if (
            len(self.metrics) + len(self.labs) + sum(self.skipped.values())
            > MAX_RECORDS
        ):
            raise MeasurementError("Export exceeds 20000 records")

    def xml(self, raw):
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise MeasurementError("XML export must use UTF-8") from exc
        if (
            "\x00" in decoded
            or "<!ENTITY" in decoded.upper()
            or re.search(r"<!DOCTYPE\s+\w+\s+(SYSTEM|PUBLIC)\b", decoded, re.I)
        ):
            raise MeasurementError(
                "External XML schemas and entity declarations are unsupported"
            )
        try:
            for _, element in ElementTree.iterparse(io.BytesIO(raw), events=("end",)):
                if element.tag == "Record":
                    data = element.attrib
                    kind = data.get("type", "")
                    if kind == "HKCategoryTypeIdentifierSleepAnalysis":
                        start, end = timestamp(data.get("startDate")), timestamp(
                            data.get("endDate")
                        )
                        hours = (
                            datetime.fromisoformat(end) - datetime.fromisoformat(start)
                        ).total_seconds() / 3600
                        if hours < 0:
                            raise MeasurementError(
                                "Sleep interval ends before it starts"
                            )
                        self.metric(
                            kind,
                            start,
                            "h",
                            hours,
                            data.get("sourceName", ""),
                            end,
                            data.get("value"),
                        )
                    elif kind.startswith("HKQuantityTypeIdentifier"):
                        self.metric(
                            kind,
                            data.get("startDate"),
                            data.get("unit"),
                            data.get("value"),
                            data.get("sourceName", ""),
                            data.get("endDate"),
                        )
                    else:
                        self.skipped["unsupported_xml_record"] += 1
                    self.bound()
                elif element.tag in ("Workout", "ActivitySummary"):
                    self.skipped["unsupported_" + element.tag.lower()] += 1
                    self.bound()
                element.clear()
        except ElementTree.ParseError as exc:
            raise MeasurementError("Malformed Apple Health XML") from exc

    def fhir(self, resource):
        if not isinstance(resource, dict):
            raise MeasurementError("FHIR resource must be an object")
        if resource.get("resourceType") == "Bundle":
            entries = resource.get("entry", [])
            if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
                raise MeasurementError("Invalid FHIR bundle entries")
            for entry in entries:
                nested = entry.get("resource") if isinstance(entry, dict) else None
                if isinstance(nested, dict) and nested.get("resourceType") == "Bundle":
                    raise MeasurementError("Nested FHIR bundles are unsupported")
                self.fhir(nested)
            return
        categories = resource.get("category", [])
        if not isinstance(categories, list) or any(
            not isinstance(category, dict)
            or not isinstance(category.get("coding", []), list)
            for category in categories
        ):
            raise MeasurementError("FHIR categories must contain coding arrays")
        is_lab = (
            any(
                isinstance(category, dict)
                and any(
                    isinstance(code, dict) and code.get("code") == "laboratory"
                    for code in category.get("coding", [])
                )
                for category in categories
            )
            if isinstance(categories, list)
            else False
        )
        if (
            resource.get("resourceType") != "Observation"
            or not is_lab
            or resource.get("status") in ("cancelled", "entered-in-error")
        ):
            self.skipped["nonactive_or_nonlaboratory_fhir"] += 1
        elif not isinstance(resource.get("valueQuantity"), dict):
            self.skipped["nonnumeric_fhir_observation"] += 1
        elif resource["valueQuantity"].get("comparator"):
            self.skipped["comparator_fhir_observation"] += 1
        else:
            value = resource["valueQuantity"]
            coding = resource.get("code", {})
            if not isinstance(coding, dict) or not isinstance(
                coding.get("coding", []), list
            ):
                raise MeasurementError("FHIR code must contain a coding array")
            code = next(
                (
                    item
                    for item in coding.get("coding", [])
                    if isinstance(item, dict)
                    and isinstance(item.get("system"), str)
                    and "loinc" in item["system"]
                ),
                {},
            )
            ranges = resource.get("referenceRange", [])
            if not isinstance(ranges, list) or (
                ranges and not isinstance(ranges[0], dict)
            ):
                raise MeasurementError(
                    "FHIR referenceRange must be an array of objects"
                )
            bounds = ranges[0] if ranges else {}
            if any(
                bounds.get(key) is not None and not isinstance(bounds[key], dict)
                for key in ("low", "high")
            ):
                raise MeasurementError("FHIR reference bounds must be quantities")
            for key in ("low", "high"):
                bound = bounds.get(key) or {}
                if bound.get("comparator") or any(
                    bound.get(field)
                    and bound[field]
                    != (value.get(field) or value.get("unit") or value.get("code"))
                    for field in ("unit", "code")
                ):
                    raise MeasurementError(
                        "FHIR reference bounds require matching units and exact values"
                    )
            row = dict(
                analyte=coding.get("text") or code.get("display") or code.get("code"),
                observed_at=timestamp(
                    resource.get("effectiveDateTime") or resource.get("issued")
                ),
                value=value.get("value"),
                unit=value.get("unit") or value.get("code"),
                reference_low=(bounds.get("low") or {}).get("value"),
                reference_high=(bounds.get("high") or {}).get("value"),
                external_id=str(resource.get("id") or ""),
                notes="",
            )
            self.labs.append(validate_row(row))
        self.bound()

    def json(self, raw, fhir=False):
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise MeasurementError("Malformed JSON export") from exc
        if fhir or (isinstance(payload, dict) and "resourceType" in payload):
            self.fhir(payload)
            return
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("metrics"), list):
            raise MeasurementError("Health Auto Export JSON requires data.metrics")
        for metric in data["metrics"]:
            if not isinstance(metric, dict) or not isinstance(metric.get("data"), list):
                raise MeasurementError("Metric data must be an array")
            for point in metric["data"]:
                if not isinstance(point, dict):
                    raise MeasurementError("Metric point must be an object")
                components = [
                    key for key in ("qty", "Min", "Max", "Avg") if key in point
                ]
                if not components:
                    self.skipped["unsupported_json_point"] += 1
                for key in components:
                    name = (
                        metric.get("name")
                        if key == "qty"
                        else f"{metric.get('name')}:{key}"
                    )
                    self.metric(
                        name,
                        point.get("date"),
                        point.get("unit") or metric.get("units"),
                        point[key],
                        point.get("source") or point.get("src") or "",
                        point.get("end"),
                    )
                self.bound()


def parse_export(payload):
    if not isinstance(payload, dict) or set(payload) - {
        "filename",
        "format",
        "content_base64",
        "source",
        "preview_id",
        "request_id",
    }:
        raise MeasurementError("Unknown Apple import fields")
    text(payload.get("filename"), "filename", 200)
    text(payload.get("source"), "source", 256)
    encoded = text(
        payload.get("content_base64"), "content_base64", (MAX_BYTES + 2) // 3 * 4
    )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MeasurementError("Invalid base64 content") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise MeasurementError("Original export must be 1 byte to 8 MiB")
    parser = ExportParser()
    format_ = payload.get("format")
    if format_ == "xml":
        parser.xml(raw)
    elif format_ in ("json", "fhir"):
        parser.json(raw, fhir=format_ == "fhir")
    elif format_ == "zip":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > 2000
                    or sum(entry.file_size for entry in entries) > MAX_EXPANDED
                ):
                    raise MeasurementError(
                        "ZIP exceeds 2000 entries or 32 MiB expanded"
                    )
                found = False
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in entry.filename
                        or entry.flag_bits & 1
                    ):
                        raise MeasurementError("Unsafe or encrypted ZIP member")
                    if entry.filename in (
                        "export.xml",
                        "apple_health_export/export.xml",
                    ):
                        parser.xml(archive.read(entry))
                        found = True
                    elif "clinical_records" in path.parts and path.suffix == ".json":
                        parser.json(archive.read(entry), fhir=True)
                        found = True
                if not found:
                    raise MeasurementError("ZIP has no export.xml or clinical records")
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise MeasurementError("Unreadable ZIP export") from exc
    else:
        raise MeasurementError("Format must be xml, zip, json or fhir")
    if not parser.metrics and not parser.labs:
        raise MeasurementError("No supported measurements in export")
    return raw, parser


class AppleHealthStore(LabStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS apple_metrics (id TEXT PRIMARY KEY, metric TEXT NOT NULL, unit TEXT NOT NULL, observed_utc TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS apple_imports (id TEXT PRIMARY KEY, receipt TEXT NOT NULL);
            """)

    def preview(self, payload):
        raw, parser = parse_export(payload)
        identity = digest(
            [
                payload["filename"],
                payload["format"],
                hashlib.sha256(raw).hexdigest(),
                payload["source"],
            ]
        )
        return {
            "preview_id": identity,
            "metric_count": len(parser.metrics),
            "lab_count": len(parser.labs),
            "metrics": parser.metrics[:100],
            "labs": parser.labs[:100],
            "skipped": dict(parser.skipped),
        }

    def commit(self, payload):
        preview = self.preview(payload)
        if payload.get("preview_id") != preview["preview_id"]:
            raise MeasurementError("Preview changed; preview again", 409, "conflict")
        request_id = text(payload.get("request_id"), "request_id", 128)
        raw, parser = parse_export(payload)
        identity = preview["preview_id"]
        fingerprint = json.dumps(["apple-import", identity])
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
                "SELECT receipt FROM apple_imports WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                db.execute(
                    "INSERT INTO requests VALUES(?,?,?)",
                    (request_id, fingerprint, existing[0]),
                )
                return json.loads(existing[0])
            sha = hashlib.sha256(raw).hexdigest()
            slug = "apple-source-" + identity
            artifact = self.artifacts.get(slug) or self.artifacts.create(
                name=payload["filename"],
                kind="json",
                content=json.dumps({"filename": payload["filename"], "sha256": sha}),
                source="import",
                slug=slug,
                readonly=True,
            )
            reference = {
                "slug": artifact.slug,
                "version": 1,
                "sha256": sha,
                "filename": f"original@{sha}.{payload['format']}",
            }
            if not self.artifacts.store_version_file(
                artifact.slug, reference["filename"], raw
            ):
                raise MeasurementError("Original export could not be stored")
            self._original(reference)
            added, labs_added, duplicates = 0, 0, 0
            for row in parser.metrics:
                key = digest([payload["source"], row])
                record = dict(row, id=key, source=payload["source"], artifact=reference)
                cursor = db.execute(
                    "INSERT OR IGNORE INTO apple_metrics VALUES(?,?,?,?,?)",
                    (
                        key,
                        row["metric"],
                        row["unit"],
                        instant(row["observed_at"]),
                        json.dumps(record),
                    ),
                )
                added += cursor.rowcount
                duplicates += 1 - cursor.rowcount
            for index, row in enumerate(parser.labs, 1):
                key, row_hash = self._identity(payload["source"], row)
                prior = db.execute(
                    "SELECT fingerprint FROM lab_sources WHERE id=?", (key,)
                ).fetchone()
                if prior:
                    if prior[0] != row_hash:
                        raise MeasurementError(
                            "Laboratory source identity conflict", 409, "conflict"
                        )
                    duplicates += 1
                    continue
                record = dict(
                    row,
                    id=key,
                    kind="laboratory",
                    source=payload["source"],
                    artifact=reference,
                    row_index=index,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    revision=1,
                )
                self._append(db, record)
                db.execute("INSERT INTO lab_sources VALUES(?,?)", (key, row_hash))
                labs_added += 1
            receipt = dict(
                import_id=identity,
                artifact=reference,
                metrics_added=added,
                labs_added=labs_added,
                duplicates=duplicates,
                skipped=dict(parser.skipped),
            )
            encoded = json.dumps(receipt)
            db.execute("INSERT INTO apple_imports VALUES(?,?)", (identity, encoded))
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, encoded)
            )
            return receipt

    def list_metrics(
        self,
        *,
        metric=None,
        unit=None,
        from_date=None,
        to_date=None,
        limit=100,
        offset=0,
    ):
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
                """SELECT data FROM apple_metrics WHERE (? IS NULL OR metric=?) AND (? IS NULL OR unit=?)
                AND (? IS NULL OR observed_utc>=?) AND (? IS NULL OR observed_utc<=?) ORDER BY observed_utc DESC,id LIMIT ? OFFSET ?""",
                (metric, metric, unit, unit, start, start, end, end, limit, offset),
            )
            return [json.loads(row[0]) for row in rows]

    def original_metric(self, identity):
        with self.connection() as db:
            row = db.execute(
                "SELECT data FROM apple_metrics WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise MeasurementError("Imported metric not found", 404, "not_found")
        return self._original(json.loads(row[0])["artifact"])
