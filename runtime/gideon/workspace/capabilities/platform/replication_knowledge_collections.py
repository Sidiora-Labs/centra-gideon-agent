"""Canonical KnowledgeStore shelves and explicit membership replication."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.operations.durability import conflicts, inventory

SCOPE = "knowledge.collections"
COLLECTION_ENTRY = "knowledge.collections"
MEMBERSHIP_ENTRY = "knowledge.collection_items"
ENTRIES = (COLLECTION_ENTRY, MEMBERSHIP_ENTRY)
_COLLECTION_FIELDS = {
    "id",
    "name",
    "kind",
    "query",
    "icon",
    "position",
    "created_at",
    "updated_at",
}
_MEMBERSHIP_FIELDS = {"collection_id", "item_id", "added_at"}


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


def _store(home):
    return KnowledgeStore(str(knowledge_db_path(Path(home))))


def _uuid(value, label):
    if not isinstance(value, str):
        raise ValueError(f"Invalid knowledge collection {label}")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"Invalid knowledge collection {label}") from error
    if str(parsed) != value:
        raise ValueError(f"Invalid knowledge collection {label}")
    return value


def _text(value, label, limit, required=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or "\x00" in value
        or (required and not value.strip())
    ):
        raise ValueError(f"Invalid knowledge collection {label}")
    return value


def _timestamp(value, label):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"Invalid knowledge collection {label}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid knowledge collection {label}") from error
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def membership_id(collection_id, item_id):
    return hashlib.sha256((collection_id + "\0" + item_id).encode()).hexdigest()


def validate_collection(value, entity_id=None):
    if not isinstance(value, dict) or set(value) != _COLLECTION_FIELDS:
        raise ValueError("Invalid or private knowledge collection")
    identity = _uuid(value.get("id"), "identity")
    if entity_id is not None and identity != entity_id:
        raise ValueError("Knowledge collection identity changed")
    _text(value.get("name"), "name", 200, required=True)
    if value.get("kind") not in {"manual", "smart"}:
        raise ValueError("Invalid knowledge collection kind")
    query = _text(value.get("query"), "query", 2000)
    _text(value.get("icon"), "icon", 200)
    if value["kind"] == "smart" and not query.strip():
        raise ValueError("Smart knowledge collection requires a query")
    if type(value.get("position")) is not int or not 0 <= value["position"] <= 2**31:
        raise ValueError("Invalid knowledge collection position")
    created = _timestamp(value.get("created_at"), "created_at")
    updated = _timestamp(value.get("updated_at"), "updated_at")
    if updated < created:
        raise ValueError("Knowledge collection update predates creation")
    return value


def validate_membership(value, entity_id=None):
    if not isinstance(value, dict) or set(value) != _MEMBERSHIP_FIELDS:
        raise ValueError("Invalid or private knowledge collection membership")
    collection_id = _uuid(value.get("collection_id"), "membership collection")
    item_id = _text(value.get("item_id"), "membership item", 256, required=True)
    _timestamp(value.get("added_at"), "membership added_at")
    expected = membership_id(collection_id, item_id)
    if entity_id is not None and entity_id != expected:
        raise ValueError("Knowledge collection membership identity changed")
    return value


def _rows_by_entry(entries):
    if (
        not isinstance(entries, list)
        or len(entries) != 2
        or [
            entry.get("entry_id") if isinstance(entry, dict) else None
            for entry in entries
        ]
        != list(ENTRIES)
        or any(
            set(entry) != {"entry_id", "rows"} or not isinstance(entry["rows"], list)
            for entry in entries
        )
    ):
        raise ValueError("Invalid knowledge collection replication coverage")
    return {entry["entry_id"]: entry["rows"] for entry in entries}


def _validate_rows(entry_id, rows):
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid knowledge collection row set")
    result = {}
    validator = (
        validate_collection if entry_id == COLLECTION_ENTRY else validate_membership
    )
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "data"}
            or not isinstance(row.get("id"), str)
        ):
            raise ValueError("Invalid knowledge collection row")
        if row["id"] in result:
            raise ValueError("Duplicate knowledge collection row")
        validator(row.get("data"), row["id"])
        result[row["id"]] = row
    return result


def validate_entries(entries, home=None):
    grouped = _rows_by_entry(entries)
    collections = _validate_rows(COLLECTION_ENTRY, grouped[COLLECTION_ENTRY])
    memberships = _validate_rows(MEMBERSHIP_ENTRY, grouped[MEMBERSHIP_ENTRY])
    names = [row["data"]["name"].strip().casefold() for row in collections.values()]
    if len(names) != len(set(names)):
        raise ValueError("Knowledge collection names must be unique")
    collection_ids = set(collections)
    if any(
        row["data"]["collection_id"] not in collection_ids
        for row in memberships.values()
    ):
        raise ValueError(
            "Knowledge collection membership references a missing collection"
        )
    if home is not None:
        store = _store(home)
        try:
            item_ids = {row[0] for row in store.db.execute("SELECT id FROM items")}
        finally:
            store.db.close()
        if any(row["data"]["item_id"] not in item_ids for row in memberships.values()):
            raise ValueError(
                "Knowledge collection membership references a missing canonical item"
            )
    return grouped


def read_rows(home, entry_id):
    store = _store(home)
    try:
        if entry_id == COLLECTION_ENTRY:
            values = [
                dict(row)
                for row in store.db.execute(
                    "SELECT id,name,kind,query,icon,position,created_at,updated_at FROM collections ORDER BY id"
                )
            ]
            rows = [{"id": value["id"], "data": value} for value in values]
        elif entry_id == MEMBERSHIP_ENTRY:
            values = [
                dict(row)
                for row in store.db.execute(
                    "SELECT collection_id,item_id,added_at FROM collection_items ORDER BY collection_id,item_id"
                )
            ]
            rows = [
                {
                    "id": membership_id(value["collection_id"], value["item_id"]),
                    "data": value,
                }
                for value in values
            ]
        else:
            raise ValueError("Unknown knowledge collection replication entry")
    finally:
        store.db.close()
    _validate_rows(entry_id, rows)
    return rows


def _conflict(entry_id, entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=entry_id,
        entity_id=entity_id,
        domain=inventory.DOMAIN_KNOWLEDGE,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_KNOWLEDGE),
        ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local),
        remote_sha=conflicts.row_sha(remote),
        local_row=local,
        remote_row=remote,
        detected_at=now,
    )


def _plan(entry_id, local_rows, remote_rows, ancestors, now):
    local, remote = _validate_rows(entry_id, local_rows), _validate_rows(
        entry_id, remote_rows
    )
    final, upserts, deletes, pending = dict(local), {}, set(), []
    added = updated = removed = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row = local.get(entity_id), remote.get(entity_id)
        ancestor = ancestors.get(entity_id, "")
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha:
            continue
        if ancestor and local_sha != ancestor and remote_sha != ancestor:
            pending.append(
                _conflict(
                    entry_id,
                    entity_id,
                    ancestor,
                    local_row or {"id": entity_id, "deleted_at": now},
                    remote_row or {"id": entity_id, "deleted_at": now},
                    now,
                )
            )
        elif remote_row is None:
            if ancestor and local_sha == ancestor:
                final.pop(entity_id, None)
                deletes.add(entity_id)
                removed += 1
        elif local_row is None:
            final[entity_id] = remote_row
            upserts[entity_id] = remote_row
            added += 1
        elif not ancestor or local_sha == ancestor:
            final[entity_id] = remote_row
            upserts[entity_id] = remote_row
            updated += 1
    return final, upserts, deletes, pending, (added, updated, removed)


def apply_entries(home, entries, ancestors, queue, now):
    remote = validate_entries(entries)
    if not isinstance(ancestors, dict) or set(ancestors) != set(ENTRIES):
        raise ValueError("Invalid knowledge collection ancestors")
    local_entries = [
        {"entry_id": entry_id, "rows": read_rows(home, entry_id)}
        for entry_id in ENTRIES
    ]
    local = {entry["entry_id"]: entry["rows"] for entry in local_entries}
    plans = {
        entry_id: _plan(
            entry_id, local[entry_id], remote[entry_id], ancestors[entry_id], now
        )
        for entry_id in ENTRIES
    }
    final_entries = [
        {"entry_id": entry_id, "rows": list(plans[entry_id][0].values())}
        for entry_id in ENTRIES
    ]
    validate_entries(final_entries, home)
    recorded = {
        entry_id: sum(1 for record in plans[entry_id][3] if queue.record(record))
        for entry_id in ENTRIES
    }
    store = _store(home)
    try:
        store.db.execute("BEGIN IMMEDIATE")
        for row in plans[COLLECTION_ENTRY][1].values():
            data = row["data"]
            store.db.execute(
                "INSERT INTO collections(id,name,kind,query,icon,position,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,query=excluded.query,icon=excluded.icon,position=excluded.position,created_at=excluded.created_at,updated_at=excluded.updated_at",
                tuple(
                    data[field]
                    for field in (
                        "id",
                        "name",
                        "kind",
                        "query",
                        "icon",
                        "position",
                        "created_at",
                        "updated_at",
                    )
                ),
            )
        for row in plans[MEMBERSHIP_ENTRY][1].values():
            data = row["data"]
            store.db.execute(
                "INSERT INTO collection_items(collection_id,item_id,added_at) VALUES(?,?,?) "
                "ON CONFLICT(collection_id,item_id) DO UPDATE SET added_at=excluded.added_at",
                (data["collection_id"], data["item_id"], data["added_at"]),
            )
        for entity_id in plans[MEMBERSHIP_ENTRY][2]:
            data = _validate_rows(MEMBERSHIP_ENTRY, local[MEMBERSHIP_ENTRY])[entity_id][
                "data"
            ]
            store.db.execute(
                "DELETE FROM collection_items WHERE collection_id=? AND item_id=?",
                (data["collection_id"], data["item_id"]),
            )
        for entity_id in plans[COLLECTION_ENTRY][2]:
            store.db.execute("DELETE FROM collections WHERE id=?", (entity_id,))
        store.db.commit()
    except BaseException:
        store.db.rollback()
        raise
    finally:
        store.db.close()
    results = {}
    for entry_id in ENTRIES:
        added, updated, removed = plans[entry_id][4]
        current = {
            row["id"]: conflicts.row_sha(row) for row in read_rows(home, entry_id)
        }
        results[entry_id] = ApplyResult(
            added, updated, removed, recorded[entry_id], current
        )
    return results


def restore_fields(home, record_id, fields, now):
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    allowed = (
        {"name", "kind", "query", "icon", "position"}
        if record and record.entry_id == COLLECTION_ENTRY
        else {"added_at"} if record and record.entry_id == MEMBERSHIP_ENTRY else set()
    )
    if (
        record is None
        or record.status != conflicts.STATUS_NEEDS_REVIEW
        or not fields
        or len(set(fields)) != len(fields)
        or any(field not in allowed for field in fields)
    ):
        raise ValueError(
            "Knowledge collection conflict is not available for restoration"
        )
    local, remote = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local, dict) or not isinstance(remote, dict):
        raise ValueError("Selected knowledge collection fields are unavailable")
    merged = dict(local)
    for field in fields:
        merged[field] = remote[field]
    if record.entry_id == COLLECTION_ENTRY:
        created = _timestamp(merged["created_at"], "created_at")
        restored = _timestamp(now, "updated_at")
        merged["updated_at"] = (
            now
            if restored > created
            else (created + timedelta(microseconds=1)).isoformat()
        )
    current = {entry_id: read_rows(home, entry_id) for entry_id in ENTRIES}
    ancestors = {
        entry_id: {row["id"]: conflicts.row_sha(row) for row in current[entry_id]}
        for entry_id in ENTRIES
    }
    current[record.entry_id] = [
        row for row in current[record.entry_id] if row["id"] != record.entity_id
    ]
    current[record.entry_id].append({"id": record.entity_id, "data": merged})
    validate_entries(
        [{"entry_id": entry_id, "rows": current[entry_id]} for entry_id in ENTRIES],
        home,
    )
    remote_entries = [
        {"entry_id": entry_id, "rows": current[entry_id]} for entry_id in ENTRIES
    ]
    apply_entries(home, remote_entries, ancestors, conflicts.ConflictQueue(home), now)
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Knowledge collection conflict queue update failed")
    return {
        "resolved": True,
        "id": record.id,
        "entry_id": record.entry_id,
        "entity_id": record.entity_id,
        "fields": fields,
    }
