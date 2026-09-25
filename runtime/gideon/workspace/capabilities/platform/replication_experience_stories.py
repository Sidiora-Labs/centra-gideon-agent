"""Canonical authored story-graph replication without player state or request authority."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from gideon.operations.durability import conflicts, inventory
from gideon.workspace.capabilities.experience.graph import identifier, validate_graph
from gideon.workspace.capabilities.experience.store import ExperienceStore


SCOPE = "experience.stories"
ENTRY_ID = "experience.story_graphs"
ENTRIES = (ENTRY_ID,)
_FIELDS = {"id", "title", "start_node", "nodes", "transitions", "revision"}
_RESTORABLE = {"title", "start_node", "nodes"}


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
    return ExperienceStore(Path(home))


def validate_record(value, entity_id=None):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Invalid or private story graph")
    identity = identifier(value.get("id"))
    if entity_id is not None and identity != entity_id:
        raise ValueError("Story graph identity changed")
    if type(value.get("revision")) is not int or not 1 <= value["revision"] <= 2**31:
        raise ValueError("Invalid story graph revision")
    graph = validate_graph({key: value[key] for key in ("title", "start_node", "nodes", "transitions", "revision")})
    canonical = {"id": identity, **graph, "revision": value["revision"]}
    if value != canonical:
        raise ValueError("Story graph is not canonical")
    return value


def _validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid story graph row set")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "data"}:
            raise ValueError("Invalid story graph row")
        identity = identifier(row.get("id"))
        if identity in result:
            raise ValueError("Duplicate story graph row")
        validate_record(row.get("data"), identity)
        result[identity] = row["data"]
    return result


def validate_entries(entries):
    if (not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict)
            or set(entries[0]) != {"entry_id", "rows"} or entries[0]["entry_id"] != ENTRY_ID):
        raise ValueError("Invalid story graph replication coverage")
    _validate_rows(entries[0]["rows"])


def read_rows(home, entry_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown story graph replication entry")
    rows = [{"id": story["id"], "data": story} for story in _store(home).stories()]
    _validate_rows(rows)
    return rows


def write_row(home, entry_id, row, entity_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown story graph replication entry")
    identifier(entity_id)
    store = _store(home)
    with store.connection() as database:
        if row is None:
            database.execute("UPDATE stories SET deleted=1 WHERE id=?", (entity_id,))
            return
        if not isinstance(row, dict) or set(row) != {"id", "data"} or row["id"] != entity_id:
            raise ValueError("Story graph row identity changed")
        data = validate_record(row["data"], entity_id)
        database.execute(
            "INSERT INTO stories(id,revision,deleted) VALUES(?,?,0) "
            "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,deleted=0",
            (entity_id, data["revision"]),
        )
        database.execute(
            "INSERT INTO versions(id,revision,body) VALUES(?,?,?) "
            "ON CONFLICT(id,revision) DO UPDATE SET body=excluded.body",
            (entity_id, data["revision"], json.dumps(data, ensure_ascii=False, sort_keys=True)),
        )


def _conflict(entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=ENTRY_ID,
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
    if entry_id != ENTRY_ID or not isinstance(ancestors, dict):
        raise ValueError("Unknown story graph replication entry")
    _validate_rows(remote_rows)
    local = {row["id"]: row for row in read_rows(home, ENTRY_ID)}
    remote = {row["id"]: row for row in remote_rows}
    operations = []
    pending = []
    added = updated = removed = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row = local.get(entity_id), remote.get(entity_id)
        ancestor = ancestors.get(entity_id, "")
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha:
            continue
        if ancestor and local_sha != ancestor and remote_sha != ancestor:
            pending.append(_conflict(
                entity_id,
                ancestor,
                local_row or {"id": entity_id, "deleted_at": now},
                remote_row or {"id": entity_id, "deleted_at": now},
                now,
            ))
        elif remote_row is None:
            if ancestor and local_sha == ancestor:
                operations.append((entity_id, None)); removed += 1
        elif local_row is None:
            operations.append((entity_id, remote_row)); added += 1
        elif not ancestor or local_sha == ancestor:
            operations.append((entity_id, remote_row)); updated += 1
    conflict_count = sum(1 for record in pending if queue.record(record))
    for entity_id, row in operations:
        write_row(home, ENTRY_ID, row, entity_id)
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, ENTRY_ID)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def restore_fields(home, record_id, fields, now):
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    if record is None or record.status != conflicts.STATUS_NEEDS_REVIEW or record.entry_id != ENTRY_ID:
        raise ValueError("Story graph conflict is not available")
    if (not fields or len(fields) > len(_RESTORABLE) or len(set(fields)) != len(fields)
            or any(not isinstance(field, str) or field not in _RESTORABLE for field in fields)):
        raise ValueError("Invalid story graph conflict fields")
    local, remote = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local, dict) or not isinstance(remote, dict):
        raise ValueError("Selected story graph fields are unavailable")
    draft = {key: local[key] for key in ("title", "start_node", "nodes")}
    for field in fields:
        draft[field] = remote[field]
    graph = validate_graph(draft)
    merged = {"id": record.entity_id, **graph, "revision": local["revision"] + 1}
    validate_record(merged, record.entity_id)
    write_row(home, ENTRY_ID, {"id": record.entity_id, "data": merged}, record.entity_id)
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Story graph conflict queue update failed")
    return {"resolved": True, "id": record.id, "entry_id": ENTRY_ID,
            "entity_id": record.entity_id, "fields": fields}
