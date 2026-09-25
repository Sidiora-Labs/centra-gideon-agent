"""Current authored clinical records without request receipts or revision history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

from gideon.operations.durability import conflicts, inventory
from gideon.workspace.capabilities.wellbeing.body_composition import (
    BodyCompositionStore,
    normalize,
)
from gideon.workspace.capabilities.wellbeing.epigenetic import EpigeneticStore
from gideon.workspace.capabilities.wellbeing.eyes import EyePrescriptionStore
from gideon.workspace.capabilities.wellbeing.lifestyle_profile import (
    LifestyleProfileStore,
)
from gideon.workspace.capabilities.wellbeing.store import MeasurementError

SCOPE = "wellbeing.clinical_records"
EPIGENETIC = "wellbeing.epigenetic_results"
EYES = "wellbeing.eye_prescriptions"
LIFESTYLE = "wellbeing.lifestyle_profiles"
BODY = "wellbeing.body_composition"
ENTRIES = (EPIGENETIC, EYES, LIFESTYLE, BODY)
_STORES = {
    EPIGENETIC: EpigeneticStore,
    EYES: EyePrescriptionStore,
    LIFESTYLE: LifestyleProfileStore,
    BODY: BodyCompositionStore,
}
_RESTORABLE = {
    EPIGENETIC: {
        "observed_at",
        "source_report_id",
        "biological_age",
        "chronological_age",
        "pace_of_aging",
        "organ_scores",
        "notes",
    },
    EYES: {"observed_date", "notes", "left", "right"},
    LIFESTYLE: {
        "reported_sex",
        "sex_source",
        "smoking_status",
        "diet_quality",
        "stress",
        "reported_bmi",
        "condition_labels",
        "reported_daily_alcohol",
    },
    BODY: {"observed_at", "notes", "original_values"},
}
_PRIVATE = {
    "request_id",
    "permission",
    "grant",
    "credential",
    "credential_ref",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "password",
    "secret",
}


@dataclass(frozen=True)
class ApplyResult:
    added: int
    updated: int
    removed: int
    conflicts: int
    new_ancestors: dict[str, str]

    @property
    def verdict(self):
        return "consumed"


def _store(home, entry_id):
    try:
        return _STORES[entry_id](home)
    except KeyError as error:
        raise ValueError("Unknown clinical replication entry") from error


def _private(value):
    if isinstance(value, dict):
        return any(
            str(key).lower().replace("-", "_") in _PRIVATE or _private(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_private(item) for item in value)
    return False


def _validate_row(entry_id, row):
    if (
        not isinstance(row, dict)
        or set(row) != {"id", "data"}
        or not isinstance(row.get("id"), str)
        or not isinstance(row.get("data"), dict)
        or row["data"].get("id") != row["id"]
        or _private(row["data"])
    ):
        raise ValueError("Invalid or private clinical replication row")
    try:
        with TemporaryDirectory() as temporary:
            _store(temporary, entry_id).import_current(row["data"])
    except MeasurementError as error:
        raise ValueError(str(error)) from error
    return row


def _validate_rows(entry_id, rows):
    if entry_id not in ENTRIES or not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid clinical replication rows")
    result = {}
    for row in rows:
        _validate_row(entry_id, row)
        if row["id"] in result:
            raise ValueError("Duplicate clinical replication row")
        result[row["id"]] = row
    return result


def validate_entries(entries):
    if (
        not isinstance(entries, list)
        or len(entries) != len(ENTRIES)
        or [
            entry.get("entry_id") if isinstance(entry, dict) else None
            for entry in entries
        ]
        != list(ENTRIES)
        or any(set(entry) != {"entry_id", "rows"} for entry in entries)
    ):
        raise ValueError("Invalid clinical replication coverage")
    for entry in entries:
        _validate_rows(entry["entry_id"], entry["rows"])


def read_rows(home, entry_id):
    store = _store(home, entry_id)
    values = store.list()
    rows = [{"id": value["id"], "data": value} for value in values]
    rows.sort(key=lambda row: row["id"])
    _validate_rows(entry_id, rows)
    return rows


def _conflict(entry_id, entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=entry_id,
        entity_id=entity_id,
        domain=inventory.DOMAIN_WORK,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_WORK),
        ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local),
        remote_sha=conflicts.row_sha(remote),
        local_row=local,
        remote_row=remote,
        detected_at=now,
    )


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id not in ENTRIES or not isinstance(ancestors, dict):
        raise ValueError("Unknown clinical replication entry")
    remote = _validate_rows(entry_id, remote_rows)
    local = {row["id"]: row for row in read_rows(home, entry_id)}
    actions, pending = {}, []
    added = updated = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row = local.get(entity_id), remote.get(entity_id)
        ancestor = ancestors.get(entity_id, "")
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha or remote_row is None:
            continue
        if local_row is not None and (
            (ancestor and local_sha != ancestor and remote_sha != ancestor)
            or not ancestor
        ):
            pending.append(
                _conflict(entry_id, entity_id, ancestor, local_row, remote_row, now)
            )
        elif local_row is None or local_sha == ancestor:
            actions[entity_id] = remote_row
            added += int(local_row is None)
            updated += int(local_row is not None)
    conflict_count = sum(1 for record in pending if queue.record(record))
    store = _store(home, entry_id)
    try:
        for entity_id in sorted(actions):
            store.import_current(actions[entity_id]["data"])
    except MeasurementError as error:
        raise ValueError(str(error)) from error
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, entry_id)}
    return ApplyResult(added, updated, 0, conflict_count, current)


def restore_fields(home, record_id, fields, now):
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    allowed = _RESTORABLE.get(record.entry_id, set()) if record else set()
    if (
        record is None
        or record.status != conflicts.STATUS_NEEDS_REVIEW
        or not fields
        or len(fields) != len(set(fields))
        or any(field not in allowed for field in fields)
    ):
        raise ValueError("Clinical conflict is not available for restoration")
    local, remote = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local, dict) or not isinstance(remote, dict):
        raise ValueError("Selected clinical fields are unavailable")
    merged = dict(local)
    for field in fields:
        merged[field] = remote[field]
    merged["revision"] = local["revision"] + 1
    if record.entry_id == BODY:
        merged["original_values"], merged["normalized_values"] = normalize(
            merged["original_values"]
        )
    else:
        created = datetime.fromisoformat(merged["created_at"])
        restored = datetime.fromisoformat(now)
        merged["updated_at"] = (
            now
            if restored > created
            else (created + timedelta(microseconds=1)).isoformat()
        )
    _validate_row(record.entry_id, {"id": record.entity_id, "data": merged})
    try:
        _store(home, record.entry_id).import_current(merged)
    except MeasurementError as error:
        raise ValueError(str(error)) from error
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Clinical conflict queue update failed")
    return {
        "resolved": True,
        "id": record.id,
        "entry_id": record.entry_id,
        "entity_id": record.entity_id,
        "fields": fields,
    }
