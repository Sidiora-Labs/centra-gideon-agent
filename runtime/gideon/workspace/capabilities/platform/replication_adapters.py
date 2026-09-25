"""Canonical SQLite row adapters for direct peer replication."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.operations.durability import conflicts, inventory, merge, reconcile, writeback
from gideon.workspace.capabilities.creative.authors import AuthorStore
from gideon.workspace.capabilities.creative.moodboards import BoardStore
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.store import IngredientStore
from gideon.workspace.capabilities.creative.stories import StoryStore
from gideon.workspace.capabilities.creative.universes import UniverseStore
from gideon.workspace.capabilities.creative.works import WorkStore


@dataclass(frozen=True)
class ApplyResult:
    added: int
    updated: int
    removed: int
    conflicts: int
    new_ancestors: dict[str, str]

    @property
    def verdict(self) -> str:
        return "consumed"


CREATIVE_TABLES = {
    "creative.ingredients": "ingredients",
    "creative.moodboards": "boards",
    "creative.universes": "universes",
    "creative.authors": "authors",
    "creative.works": "works",
    "creative.stories": "stories",
    "creative.series": "series",
}
SQLITE_ENTRIES = frozenset({"knowledge.items", *CREATIVE_TABLES})


def _knowledge(home: Path) -> KnowledgeStore:
    return KnowledgeStore(str(knowledge_db_path(home)))


def _creative_path(home: Path) -> Path:
    IngredientStore(home); BoardStore(home); UniverseStore(home); AuthorStore(home)
    WorkStore(home); StoryStore(home); SeriesStore(home)
    return home / "capabilities/creative/catalog.sqlite3"


def read_rows(home: Path, entry_id: str) -> list[dict]:
    if entry_id == "knowledge.items":
        store = _knowledge(home)
        try:
            rows = store.db.execute("SELECT id FROM items ORDER BY id").fetchall()
            return [{"id": row["id"], "data": store.get_item(row["id"])} for row in rows]
        finally:
            store.db.close()
    table = CREATIVE_TABLES[entry_id]
    path = _creative_path(home)
    with sqlite3.connect(path) as db:
        return [{"id": row[0], "data": json.loads(row[1])} for row in db.execute(f"SELECT id,record FROM {table} ORDER BY id")]


def _write_knowledge(home: Path, row: dict | None, entity_id: str) -> None:
    store = _knowledge(home)
    try:
        if row is None:
            store.delete_item(entity_id)
            return
        data = row["data"]
        current = store.get_item(entity_id)
        if current:
            values = {key: value for key, value in data.items() if key in store._ITEM_COLUMNS or key == "tags"}
            values.pop("embedding", None)
            store.update_item(entity_id, touch=False, **values)
            store.db.commit()
            return
        columns = {item[1] for item in store.db.execute("PRAGMA table_info(items)")}
        values = {key: value for key, value in data.items() if key in columns and key != "embedding"}
        values["id"] = entity_id
        values.setdefault("title", "")
        values.setdefault("content", "")
        values.setdefault("item_type", data.get("type") or "note")
        values.setdefault("created_at", datetime.now().isoformat())
        values.setdefault("updated_at", values["created_at"])
        encoded = {key: json.dumps(value) if isinstance(value, (dict, list)) else int(value) if isinstance(value, bool) else value for key, value in values.items()}
        store.db.execute("BEGIN")
        names = ",".join(encoded)
        store.db.execute(f"INSERT INTO items ({names}) VALUES ({','.join('?' for _ in encoded)})", tuple(encoded.values()))
        store._write_item_tags(entity_id, data.get("tags", []))
        dbrow = store.db.execute("SELECT rowid,title,content FROM items WHERE id=?", (entity_id,)).fetchone()
        store.db.execute("INSERT INTO items_fts(rowid,title,content,tags) VALUES(?,?,?,?)", (dbrow["rowid"], dbrow["title"], dbrow["content"], " ".join(data.get("tags", []))))
        store.db.execute("COMMIT")
    finally:
        store.db.close()


def _write_creative(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    table = CREATIVE_TABLES[entry_id]
    with sqlite3.connect(_creative_path(home)) as db:
        if row is None:
            db.execute(f"DELETE FROM {table} WHERE id=?", (entity_id,))
        else:
            data = dict(row["data"])
            data["id"] = entity_id
            db.execute(f"INSERT INTO {table}(id,record) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record", (entity_id, json.dumps(data, sort_keys=True)))


def write_row(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    if entry_id == "knowledge.items":
        _write_knowledge(home, row, entity_id)
    else:
        _write_creative(home, entry_id, row, entity_id)


def _conflict(entry_id: str, entity_id: str, ancestor: str, local: dict, remote: dict, now: str) -> conflicts.ConflictRecord:
    domain = inventory.DOMAIN_KNOWLEDGE if entry_id == "knowledge.items" else inventory.DOMAIN_WORK
    return conflicts.ConflictRecord(entry_id=entry_id, entity_id=entity_id, domain=domain,
        surface=conflicts.surface_for_domain(domain), ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local), remote_sha=conflicts.row_sha(remote),
        local_row=local, remote_row=remote, detected_at=now)


def apply_rows(home: Path, entry_id: str, remote_rows: list[dict], ancestors: dict[str, str], queue: conflicts.ConflictQueue, now: str) -> ApplyResult:
    local_rows = read_rows(home, entry_id)
    local = {row["id"]: row for row in local_rows}
    remote = {row.get("id"): row for row in remote_rows if isinstance(row.get("id"), str) and isinstance(row.get("data"), dict)}
    if len(remote) != len(remote_rows):
        raise ValueError("invalid canonical SQLite rows")
    added = updated = removed = conflict_count = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        lrow, rrow, ancestor = local.get(entity_id), remote.get(entity_id), ancestors.get(entity_id, "")
        lsha = conflicts.row_sha(lrow) if lrow else ""
        rsha = conflicts.row_sha(rrow) if rrow else ""
        if lsha == rsha:
            continue
        if ancestor and lsha != ancestor and rsha != ancestor:
            marker_local = lrow or {"id": entity_id, "deleted_at": now}
            marker_remote = rrow or {"id": entity_id, "deleted_at": now}
            if queue.record(_conflict(entry_id, entity_id, ancestor, marker_local, marker_remote, now)):
                conflict_count += 1
            continue
        if rrow is None:
            if ancestor and lsha == ancestor:
                write_row(home, entry_id, None, entity_id); removed += 1
            continue
        if lrow is None:
            write_row(home, entry_id, rrow, entity_id); added += 1
        elif not ancestor or lsha == ancestor:
            write_row(home, entry_id, rrow, entity_id); updated += 1
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, entry_id)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def apply_inventory_rows(home: Path, entry: inventory.StateEntry, remote_rows: list[dict], ancestors: dict[str, str], queue: conflicts.ConflictQueue, now: str) -> ApplyResult:
    local_rows = reconcile.read_local_rows(entry, home / entry.path)
    detected = conflicts.detect_conflicts(entry, local_rows, remote_rows, ancestors, now=now)
    recorded = sum(1 for item in detected if queue.record(item))
    held = queue.held_ids(entry.id)
    effective = [row for row in remote_rows if conflicts.row_id(row) not in held]
    result = merge.merge_rows(entry.merge, local_rows, effective, tombstones=entry.tombstones, dedup_key="id")
    local = {conflicts.row_id(row): row for row in local_rows if conflicts.row_id(row)}
    remote = {conflicts.row_id(row): row for row in effective if conflicts.row_id(row)}
    merged = {conflicts.row_id(row): row for row in result.rows if conflicts.row_id(row)}
    for entity_id, remote_row in remote.items():
        local_row, ancestor = local.get(entity_id), ancestors.get(entity_id, "")
        if local_row and ancestor and conflicts.row_sha(local_row) == ancestor:
            merged[entity_id] = remote_row
    rows = [merged[key] for key in sorted(merged)]
    applied = writeback.apply_rows(entry.kind, home / entry.path, rows)
    after = {key: conflicts.row_sha(row) for key, row in merged.items()}
    before = {key: conflicts.row_sha(row) for key, row in local.items()}
    agreed = {key: sha for key, sha in after.items() if key not in held and key in remote and sha == conflicts.row_sha(remote[key])}
    added = len(set(after) - set(before))
    updated = sum(before.get(key) != sha for key, sha in after.items() if key in before)
    return ApplyResult(added, updated, applied.removed, recorded, agreed)


def restore_fields(home: Path, record_id: str, fields: list[str], now: str) -> dict:
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    if record is None or record.status != conflicts.STATUS_NEEDS_REVIEW:
        raise ValueError("Conflict is not available for restoration")
    if record.entry_id not in SQLITE_ENTRIES or not fields or len(fields) > 50 or any(not isinstance(field, str) or not field or field in {"id", "created_at", "updated_at", "revision"} for field in fields):
        raise ValueError("Invalid field restoration")
    local_data, remote_data = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local_data, dict) or not isinstance(remote_data, dict) or any(field not in remote_data for field in fields):
        raise ValueError("Selected remote fields are unavailable")
    merged = dict(local_data)
    for field in fields:
        merged[field] = remote_data[field]
    if "revision" in merged:
        merged["revision"] = int(local_data.get("revision", 0)) + 1
    merged["updated_at"] = now
    write_row(home, record.entry_id, {"id": record.entity_id, "data": merged}, record.entity_id)
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Conflict queue update failed")
    return {"resolved": True, "id": record.id, "entry_id": record.entry_id, "entity_id": record.entity_id, "fields": fields}
