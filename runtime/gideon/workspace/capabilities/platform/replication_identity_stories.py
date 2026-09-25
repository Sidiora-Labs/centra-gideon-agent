"""Canonical current life-story replication without request or revision ledgers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gideon.operations.durability import conflicts, inventory
from gideon.workspace.capabilities.identity.store import StoryStore


SCOPE = "identity.stories"
ENTRY_ID = "identity.life_stories"
ENTRIES = (ENTRY_ID,)
_ID = re.compile(r"[0-9a-f]{32}")
_FIELDS = {
    "id", "prompt", "theme", "text", "parent_id", "created_at", "updated_at",
    "revision",
}
_RESTORABLE = {"prompt", "theme", "text", "parent_id"}


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


def _story_store(home):
    return StoryStore(Path(home) / "capabilities/identity/stories.sqlite3")


def _timestamp(value, name):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"Invalid identity story {name}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid identity story {name}") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"Invalid identity story {name}")
    return parsed


def validate_record(value, entity_id=None):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Invalid or private identity story")
    identity = value.get("id")
    if not isinstance(identity, str) or _ID.fullmatch(identity) is None or (entity_id is not None and identity != entity_id):
        raise ValueError("Identity story identity changed")
    for field, limit in (("prompt", 4000), ("theme", 200), ("text", 100000)):
        content = value.get(field)
        if not isinstance(content, str) or not content.strip() or len(content) > limit or "\x00" in content:
            raise ValueError(f"Invalid identity story {field}")
    parent = value.get("parent_id")
    if parent is not None and (not isinstance(parent, str) or _ID.fullmatch(parent) is None or parent == identity):
        raise ValueError("Invalid identity story parent")
    if type(value.get("revision")) is not int or not 1 <= value["revision"] <= 2**31:
        raise ValueError("Invalid identity story revision")
    created = _timestamp(value.get("created_at"), "created_at")
    updated = _timestamp(value.get("updated_at"), "updated_at")
    if updated < created:
        raise ValueError("Identity story update predates creation")
    return value


def _validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid identity story row set")
    stories = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "data"}:
            raise ValueError("Invalid identity story row")
        identity = row.get("id")
        if identity in stories:
            raise ValueError("Duplicate identity story row")
        validate_record(row.get("data"), identity)
        stories[identity] = row["data"]
    for identity, story in stories.items():
        parent = story["parent_id"]
        if parent is not None and parent not in stories:
            raise ValueError("Identity story parent is missing from the complete batch")
        seen = {identity}
        while parent is not None:
            if parent in seen:
                raise ValueError("Identity story graph contains a cycle")
            seen.add(parent)
            parent = stories[parent]["parent_id"]
    return stories


def validate_entries(entries):
    if (not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict)
            or set(entries[0]) != {"entry_id", "rows"} or entries[0]["entry_id"] != ENTRY_ID):
        raise ValueError("Invalid identity story replication coverage")
    _validate_rows(entries[0]["rows"])


def read_rows(home, entry_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown identity story replication entry")
    rows = [{"id": story["id"], "data": story} for story in _story_store(home).list()]
    _validate_rows(rows)
    return rows


def _depths(stories):
    depths = {}

    def depth(identity):
        if identity not in depths:
            parent = stories[identity]["parent_id"]
            depths[identity] = 0 if parent is None else depth(parent) + 1
        return depths[identity]

    for identity in stories:
        depth(identity)
    return depths


def write_row(home, entry_id, row, entity_id):
    if entry_id != ENTRY_ID or not isinstance(entity_id, str) or _ID.fullmatch(entity_id) is None:
        raise ValueError("Unknown identity story replication entry")
    store = _story_store(home)
    current = {story["id"]: story for story in store.list()}
    if row is None:
        if any(story["parent_id"] == entity_id for story in current.values()):
            raise ValueError("Delete identity story children before their parent")
        with store._db() as database:
            database.execute("DELETE FROM stories WHERE id=?", (entity_id,))
        return
    if not isinstance(row, dict) or set(row) != {"id", "data"} or row["id"] != entity_id:
        raise ValueError("Identity story row identity changed")
    validate_record(row["data"], entity_id)
    proposed = {**current, entity_id: row["data"]}
    _validate_rows([{"id": identity, "data": data} for identity, data in proposed.items()])
    with store._db() as database:
        database.execute(
            "INSERT INTO stories(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (entity_id, json.dumps(row["data"], ensure_ascii=False, sort_keys=True)),
        )


def _conflict(entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=ENTRY_ID, entity_id=entity_id, domain=inventory.DOMAIN_MEMORY,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_MEMORY), ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local), remote_sha=conflicts.row_sha(remote),
        local_row=local, remote_row=remote, detected_at=now,
    )


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id != ENTRY_ID or not isinstance(ancestors, dict):
        raise ValueError("Unknown identity story replication entry")
    remote_stories = _validate_rows(remote_rows)
    local_rows = read_rows(home, ENTRY_ID)
    local = {row["id"]: row for row in local_rows}
    remote = {row["id"]: row for row in remote_rows}
    final = dict(local)
    upserts, deletes, pending = {}, set(), []
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
                entity_id, ancestor, local_row or {"id": entity_id, "deleted_at": now},
                remote_row or {"id": entity_id, "deleted_at": now}, now,
            ))
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
    final_stories = _validate_rows(list(final.values()))
    remote_depths = _depths(remote_stories) if remote_stories else {}
    local_stories = {row["id"]: row["data"] for row in local_rows}
    local_depths = _depths(local_stories) if local_stories else {}
    conflict_count = sum(1 for record in pending if queue.record(record))
    for entity_id in sorted(upserts, key=lambda item: (remote_depths[item], item)):
        write_row(home, ENTRY_ID, upserts[entity_id], entity_id)
    for entity_id in sorted(deletes, key=lambda item: (-local_depths[item], item)):
        write_row(home, ENTRY_ID, None, entity_id)
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, ENTRY_ID)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def restore_fields(home, record_id, fields, now):
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    if record is None or record.status != conflicts.STATUS_NEEDS_REVIEW or record.entry_id != ENTRY_ID:
        raise ValueError("Identity story conflict is not available")
    if (not fields or len(fields) > len(_RESTORABLE) or len(set(fields)) != len(fields)
            or any(not isinstance(field, str) or field not in _RESTORABLE for field in fields)):
        raise ValueError("Invalid identity story conflict fields")
    local, remote = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local, dict) or not isinstance(remote, dict):
        raise ValueError("Selected identity story fields are unavailable")
    merged = dict(local)
    for field in fields:
        merged[field] = remote[field]
    merged["revision"] = local["revision"] + 1
    merged["updated_at"] = now
    current = {row["id"]: row["data"] for row in read_rows(home, ENTRY_ID)}
    current[record.entity_id] = merged
    _validate_rows([{"id": identity, "data": data} for identity, data in current.items()])
    write_row(home, ENTRY_ID, {"id": record.entity_id, "data": merged}, record.entity_id)
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Identity story conflict queue update failed")
    return {"resolved": True, "id": record.id, "entry_id": ENTRY_ID,
            "entity_id": record.entity_id, "fields": fields}
