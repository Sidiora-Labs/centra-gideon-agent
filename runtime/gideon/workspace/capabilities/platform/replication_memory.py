"""Policy-screened canonical semantic-memory replication."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from gideon.cognition.memory_record import (
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryTier,
)
from gideon.cognition.onboarding_import.floors import safe_text, strip_secrets
from gideon.cognition.vector_memory import SemanticArchive
from gideon.operations.durability import conflicts, inventory

SCOPE = "memory.records"
ENTRY_ID = "memory.semantic_records"
ENTRIES = (ENTRY_ID,)
_FIELDS = {
    "id",
    "value",
    "confidence",
    "source",
    "category",
    "is_deleted",
    "created_at",
    "updated_at",
}
_RESTORABLE = {"value", "confidence", "category"}
_PREFIXES = ("pref.", "project.", "claim.", "user.")
_DENIED = (
    "lesson.",
    "slot.",
    "user.procedural.",
    "user.commitment.",
    "user.persona.",
    "user.approval.",
)
_KEY = re.compile(r"^[a-z][a-z0-9_.]{0,98}[a-z0-9]$")
_PRIVATE = {
    "credential",
    "credential_ref",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "password",
    "secret",
    "legal_name",
    "full_name",
    "birth_date",
    "date_of_birth",
    "ssn",
    "passport",
    "government_id",
    "email",
    "phone",
    "address",
    "biometric",
    "genome",
}
_HUMAN = {"user_explicit", "vault_edit"}
_PRIVATE_VALUE = re.compile(
    r"(?:\b\d{3}-\d{2}-\d{4}\b|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b)",
    re.IGNORECASE,
)


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


def _archive(home):
    store = SemanticArchive(db_path=Path(home) / "memory.db")
    store.init()
    return store


def _timestamp(value, label):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"Invalid semantic memory {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid semantic memory {label}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Invalid semantic memory {label}")
    return parsed


def _private(value):
    clean, dropped = strip_secrets(value)
    if dropped or clean != value:
        return True
    if isinstance(value, dict):
        return any(
            str(key).strip().lower().replace("-", "_") in _PRIVATE or _private(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_private(item) for item in value)
    if isinstance(value, str):
        cleaned, redactions = safe_text(value)
        return bool(redactions or cleaned != value or _PRIVATE_VALUE.search(value))
    return False


def validate_record(value, entity_id=None):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Invalid or private semantic memory record")
    identity = value.get("id")
    if (
        not isinstance(identity, str)
        or _KEY.fullmatch(identity) is None
        or not identity.startswith(_PREFIXES)
        or identity.startswith(_DENIED)
        or (entity_id is not None and identity != entity_id)
        or set(identity.split(".")) & _PRIVATE
    ):
        raise ValueError("Semantic memory identity is outside replication policy")
    if (
        type(value.get("confidence")) not in (int, float)
        or not 0 <= value["confidence"] <= 1
    ):
        raise ValueError("Invalid semantic memory confidence")
    source = value.get("source")
    category = value.get("category")
    if (
        not isinstance(source, str)
        or not source
        or len(source) > 100
        or (
            category is not None
            and (not isinstance(category, str) or len(category) > 100)
        )
        or type(value.get("is_deleted")) is not bool
    ):
        raise ValueError("Invalid semantic memory provenance")
    try:
        encoded = json.dumps(value.get("value"), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Semantic memory value is not JSON serializable") from error
    if (
        len(encoded.encode()) > 4096
        or _private(value["value"])
        or _private(source)
        or _private(category or "")
    ):
        raise ValueError("Semantic memory contains credential or private identity data")
    created = _timestamp(value.get("created_at"), "created_at")
    updated = _timestamp(value.get("updated_at"), "updated_at")
    if updated < created:
        raise ValueError("Semantic memory update predates creation")
    return value


def _record(value):
    validate_record(value, value["id"])
    return MemoryRecord(
        id=value["id"],
        kind=MemoryKind.SEMANTIC,
        value=value["value"],
        confidence=float(value["confidence"]),
        source=value["source"],
        tier=MemoryTier.SEMANTIC,
        scope=MemoryScope.GLOBAL,
        category=value["category"],
        is_deleted=value["is_deleted"],
        created_at=value["created_at"],
        updated_at=value["updated_at"],
    )


def _validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid semantic memory row set")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "data"}
            or row.get("id") in result
        ):
            raise ValueError("Invalid or duplicate semantic memory row")
        validate_record(row.get("data"), row.get("id"))
        result[row["id"]] = row
    return result


def validate_entries(entries):
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
        or set(entries[0]) != {"entry_id", "rows"}
        or entries[0]["entry_id"] != ENTRY_ID
    ):
        raise ValueError("Invalid semantic memory replication coverage")
    _validate_rows(entries[0]["rows"])


def _project(record):
    value = {
        "id": record.id,
        "value": record.value,
        "confidence": record.confidence,
        "source": record.source,
        "category": record.category,
        "is_deleted": record.is_deleted,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    return validate_record(value, record.id)


def read_rows(home, entry_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown semantic memory replication entry")
    store = _archive(home)
    try:
        records = store.query(kinds={MemoryKind.SEMANTIC.value}, include_deleted=True)
        rows = []
        for record in records:
            if record.scope is not MemoryScope.GLOBAL or record.scope_ref is not None:
                continue
            try:
                data = _project(record)
            except ValueError:
                continue
            rows.append({"id": record.id, "data": data})
    finally:
        store.close()
    rows.sort(key=lambda row: row["id"])
    _validate_rows(rows)
    return rows


def _conflict(entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=ENTRY_ID,
        entity_id=entity_id,
        domain=inventory.DOMAIN_MEMORY,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_MEMORY),
        ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local),
        remote_sha=conflicts.row_sha(remote),
        local_row=local,
        remote_row=remote,
        detected_at=now,
    )


def _owner_conflict(local, remote):
    if local["source"] in _HUMAN and remote["source"] not in _HUMAN:
        return True
    return (
        remote["source"] not in _HUMAN
        and local["source"] not in _HUMAN
        and remote["confidence"] < local["confidence"]
        and abs(remote["confidence"] - local["confidence"]) >= 0.1
    )


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id != ENTRY_ID or not isinstance(ancestors, dict):
        raise ValueError("Unknown semantic memory replication entry")
    remote = _validate_rows(remote_rows)
    local = {row["id"]: row for row in read_rows(home, ENTRY_ID)}
    actions, pending = {}, []
    added = updated = removed = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row = local.get(entity_id), remote.get(entity_id)
        ancestor = ancestors.get(entity_id, "")
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha or remote_row is None:
            continue
        diverged = local_row is not None and (
            (ancestor and local_sha != ancestor and remote_sha != ancestor)
            or (not ancestor and local_sha != remote_sha)
            or _owner_conflict(local_row["data"], remote_row["data"])
        )
        if diverged:
            pending.append(_conflict(entity_id, ancestor, local_row, remote_row, now))
            continue
        if local_row is None:
            added += 0 if remote_row["data"]["is_deleted"] else 1
            removed += 1 if remote_row["data"]["is_deleted"] else 0
        elif remote_row["data"]["is_deleted"] and not local_row["data"]["is_deleted"]:
            removed += 1
        else:
            updated += 1
        actions[entity_id] = remote_row
    conflict_count = sum(1 for record in pending if queue.record(record))
    store = _archive(home)
    try:
        for entity_id in sorted(actions):
            result = store.import_semantic(_record(actions[entity_id]["data"]))
            if not result.accepted:
                raise ValueError(
                    f"Semantic memory owner rejected {entity_id}: {result.code}: {result.message}"
                )
    finally:
        store.close()
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, ENTRY_ID)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def restore_fields(home, record_id, fields, now):
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    if (
        record is None
        or record.status != conflicts.STATUS_NEEDS_REVIEW
        or record.entry_id != ENTRY_ID
    ):
        raise ValueError("Semantic memory conflict is not available")
    if (
        not fields
        or len(fields) > len(_RESTORABLE)
        or len(set(fields)) != len(fields)
        or any(field not in _RESTORABLE for field in fields)
    ):
        raise ValueError("Invalid semantic memory conflict fields")
    local, remote = record.local_row.get("data"), record.remote_row.get("data")
    if (
        not isinstance(local, dict)
        or not isinstance(remote, dict)
        or local.get("is_deleted")
        or remote.get("is_deleted")
    ):
        raise ValueError("Selected semantic memory fields are unavailable")
    merged = dict(local)
    for field in fields:
        merged[field] = remote[field]
    created, restored = _timestamp(merged["created_at"], "created_at"), _timestamp(
        now, "updated_at"
    )
    merged["updated_at"] = (
        now if restored > created else (created + timedelta(microseconds=1)).isoformat()
    )
    validate_record(merged, record.entity_id)
    store = _archive(home)
    try:
        result = store.import_semantic(_record(merged))
        if not result.accepted:
            raise ValueError(
                f"Semantic memory owner rejected restoration: {result.code}: {result.message}"
            )
    finally:
        store.close()
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Semantic memory conflict queue update failed")
    return {
        "resolved": True,
        "id": record.id,
        "entry_id": ENTRY_ID,
        "entity_id": record.entity_id,
        "fields": fields,
    }
