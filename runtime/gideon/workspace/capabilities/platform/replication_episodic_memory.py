"""Canonical explicitly authored episodic-memory replication adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from gideon.cognition.archive_episode_import import (
    AuthoredEpisodeImport,
    validate_authored_episode,
)
from gideon.cognition.vector_memory import SemanticArchive
from gideon.operations.durability import conflicts, inventory

SCOPE = "memory.episodes"
ENTRY_ID = "memory.episodic_records"
ENTRIES = (ENTRY_ID,)


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
    value = SemanticArchive(db_path=Path(home) / "memory.db")
    value.init()
    return value


def _rows(rows):
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid episodic memory row set")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "data"}
            or row.get("id") in result
        ):
            raise ValueError("Invalid or duplicate episodic memory row")
        if not isinstance(row.get("data"), dict) or row["data"].get("id") != row.get(
            "id"
        ):
            raise ValueError("Episodic memory row identity does not match")
        validate_authored_episode(row["data"])
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
        raise ValueError("Invalid episodic memory replication coverage")
    _rows(entries[0]["rows"])


def read_rows(home, entry_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown episodic memory replication entry")
    store = _archive(home)
    try:
        result = []
        records = store.db.execute(
            "SELECT id,conversation_id,text,tags,importance,created_at,is_deleted,contributor "
            "FROM episodic_memories ORDER BY id"
        ).fetchall()
        for row in records:
            event = store.db.execute(
                "SELECT source,created_at FROM memory_events WHERE memory_type='episodic' AND memory_key=? "
                "AND event_type IN ('create','import') ORDER BY id LIMIT 1",
                (row["id"],),
            ).fetchone()
            if event is None or event["source"] not in {
                "user_explicit",
                "user",
                "vault_edit",
            }:
                continue
            latest = store.db.execute(
                "SELECT created_at FROM memory_events WHERE memory_type='episodic' AND memory_key=? "
                "AND event_type IN ('create','import','delete') ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            data = {
                "id": row["id"],
                "conversation_id": row["conversation_id"] or "",
                "text": row["text"],
                "tags": json.loads(row["tags"] or "[]"),
                "importance": row["importance"],
                "source": event["source"],
                "contributor": row["contributor"] or "",
                "created_at": row["created_at"],
                "updated_at": latest["created_at"] if latest else row["created_at"],
                "is_deleted": bool(row["is_deleted"]),
            }
            try:
                validate_authored_episode(data)
            except ValueError:
                continue
            result.append({"id": row["id"], "data": data})
    finally:
        store.close()
    return result


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


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id != ENTRY_ID or not isinstance(ancestors, dict):
        raise ValueError("Unknown episodic memory replication entry")
    remote, local = _rows(remote_rows), {
        row["id"]: row for row in read_rows(home, ENTRY_ID)
    }
    actions, held = {}, []
    added = removed = 0
    for identity in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row, ancestor = (
            local.get(identity),
            remote.get(identity),
            ancestors.get(identity, ""),
        )
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha or remote_row is None:
            continue
        if local_row is not None and not (ancestor and local_sha == ancestor):
            held.append(_conflict(identity, ancestor, local_row, remote_row, now))
            continue
        if local_row is not None:
            before, after = local_row["data"], remote_row["data"]
            immutable = set(before) - {"updated_at", "is_deleted"}
            if (
                any(before[key] != after[key] for key in immutable)
                or before["is_deleted"]
                or not after["is_deleted"]
            ):
                held.append(_conflict(identity, ancestor, local_row, remote_row, now))
                continue
            removed += 1
        else:
            removed += int(remote_row["data"]["is_deleted"])
            added += int(not remote_row["data"]["is_deleted"])
        actions[identity] = remote_row["data"]
    conflict_count = sum(1 for value in held if queue.record(value))
    store = _archive(home)
    try:
        owner = AuthoredEpisodeImport(store)
        for identity in sorted(actions):
            result = owner.apply(actions[identity], commit=False)
            if not result.accepted:
                raise ValueError(f"Episodic memory owner rejected {identity}")
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    finally:
        store.close()
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, ENTRY_ID)}
    return ApplyResult(added, 0, removed, conflict_count, current)


def restore_fields(home, record_id, fields, now):
    raise ValueError(
        "Authored episodic memories are immutable; resolve the identity collision without field merge"
    )
