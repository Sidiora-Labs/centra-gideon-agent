"""Canonical current-project adapter for opt-in creative direction replication."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gideon.operations.durability import conflicts, inventory
from gideon.workspace.capabilities.creative.direction import OPERATIONS, DirectionStore
from gideon.workspace.capabilities.creative.store import identifier

SCOPE = "creative.direction"
ENTRY_ID = "creative.direction_projects"
ENTRIES = (ENTRY_ID,)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROJECT_KEYS = {
    "id",
    "revision",
    "name",
    "treatment",
    "sources",
    "steps",
    "status",
    "created_at",
    "updated_at",
}
_SOURCE_KEYS = {"kind", "id", "revision", "title", "chapters"}
_CHAPTER_KEYS = {
    "chapter_id",
    "title",
    "work_id",
    "work_revision",
    "draft_id",
    "artifact_id",
    "artifact_version",
    "content_hash",
}
_STEP_KEYS = {"id", "title", "operation", "depends_on", "status", "result", "attempts"}
_PRIVATE_KEYS = {
    "request_id",
    "credential",
    "credential_ref",
    "secret",
    "token",
    "trigger_id",
    "import_id",
    "bytes",
}


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


def _timestamp(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"Invalid {name}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"Invalid {name}")
    return value


def _text(value: object, name: str, limit: int, required: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (required and not value.strip())
    ):
        raise ValueError(f"Invalid {name}")
    return value


def _positive(value: object, name: str, *, allow_zero: bool = False) -> int:
    floor = 0 if allow_zero else 1
    if type(value) is not int or value < floor or value > 2**31:
        raise ValueError(f"Invalid {name}")
    return value


def _contains_private(value: object) -> bool:
    if isinstance(value, dict):
        return bool(_PRIVATE_KEYS & set(value)) or any(
            _contains_private(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_private(item) for item in value)
    return False


def _chapter(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != _CHAPTER_KEYS:
        raise ValueError("Direction source chapter pins must use the canonical shape")
    chapter_id = value["chapter_id"]
    if chapter_id is not None:
        identifier(chapter_id)
    for field in ("work_id", "draft_id", "artifact_id"):
        identifier(value[field])
    _text(value["title"], "chapter title", 300, True)
    _positive(value["work_revision"], "work revision")
    _positive(value["artifact_version"], "artifact version")
    if not isinstance(value["content_hash"], str) or not _SHA256.fullmatch(
        value["content_hash"]
    ):
        raise ValueError("Direction source content hash must be SHA-256")
    return value


def _source(value: object) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != _SOURCE_KEYS
        or value["kind"] not in {"work", "series"}
    ):
        raise ValueError("Direction sources must use the canonical pinned shape")
    identifier(value["id"])
    _positive(value["revision"], "source revision")
    _text(value["title"], "source title", 300, True)
    chapters = value["chapters"]
    if not isinstance(chapters, list) or not chapters or len(chapters) > 500:
        raise ValueError("Direction sources require bounded chapter pins")
    for chapter in chapters:
        _chapter(chapter)
    if value["kind"] == "work" and (
        len(chapters) != 1 or chapters[0]["chapter_id"] is not None
    ):
        raise ValueError("A work source requires one direct manuscript pin")
    if value["kind"] == "series" and any(
        chapter["chapter_id"] is None for chapter in chapters
    ):
        raise ValueError("A series source requires chapter identities")
    return value


def _verified_result(value: object, sources: list[dict]) -> None:
    if not isinstance(value, dict) or set(value) != {"verified_sources", "verified_at"}:
        raise ValueError("Source verification result has an unsupported shape")
    expected = [
        {"kind": source["kind"], "id": source["id"], "revision": source["revision"]}
        for source in sources
    ]
    if value["verified_sources"] != expected:
        raise ValueError("Source verification result does not match pinned sources")
    _timestamp(value["verified_at"], "verified_at")


def _artifact_result(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "artifact_id",
        "artifact_version",
        "content_hash",
        "path",
    }:
        raise ValueError("Treatment output has an unsupported shape")
    identifier(value["artifact_id"])
    version = _positive(value["artifact_version"], "output artifact version")
    if not isinstance(value["content_hash"], str) or not _SHA256.fullmatch(
        value["content_hash"]
    ):
        raise ValueError("Treatment output content hash must be SHA-256")
    if value["path"] != f'/api/artifacts/{value["artifact_id"]}?version={version}':
        raise ValueError("Treatment output path does not match its exact artifact pin")


def _steps(values: object, sources: list[dict]) -> list[dict]:
    if not isinstance(values, list) or not 1 <= len(values) <= 50:
        raise ValueError("Direction project requires a bounded production plan")
    seen: set[str] = set()
    for step in values:
        if not isinstance(step, dict) or set(step) != _STEP_KEYS:
            raise ValueError("Direction plan step has an unsupported shape")
        step_id = identifier(step["id"])
        _text(step["title"], "step title", 200, True)
        if step["operation"] not in OPERATIONS:
            raise ValueError("Direction plan operation is unsupported")
        dependencies = step["depends_on"]
        if (
            not isinstance(dependencies, list)
            or len(dependencies) != len(set(dependencies))
            or any(identifier(item) not in seen for item in dependencies)
        ):
            raise ValueError("Direction plan dependencies must refer to earlier steps")
        if step["status"] not in {"pending", "done", "skipped"}:
            raise ValueError("Direction plan status is unsupported")
        attempts = _positive(step["attempts"], "step attempts", allow_zero=True)
        if step["status"] == "pending":
            if step["result"] is not None or attempts != 0:
                raise ValueError(
                    "Pending direction steps cannot claim execution output"
                )
        elif step["status"] == "skipped":
            if step["result"] is not None:
                raise ValueError(
                    "Skipped direction steps cannot claim execution output"
                )
        elif step["operation"] == "source.verify":
            _verified_result(step["result"], sources)
        else:
            _artifact_result(step["result"])
        seen.add(step_id)
    return values


def validate_row(row: object) -> dict:
    if (
        not isinstance(row, dict)
        or set(row) != {"id", "data"}
        or not isinstance(row["data"], dict)
    ):
        raise ValueError("Invalid canonical direction project row")
    identity = identifier(row["id"])
    data = row["data"]
    if (
        set(data) != _PROJECT_KEYS
        or data.get("id") != identity
        or _contains_private(data)
    ):
        raise ValueError("Direction project contains non-current or private fields")
    _positive(data["revision"], "project revision")
    _text(data["name"], "project name", 200, True)
    _text(data["treatment"], "project treatment", 20000, True)
    sources = data["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError("Direction project requires canonical sources")
    for source in sources:
        _source(source)
    steps = _steps(data["steps"], sources)
    if data["status"] not in {"draft", "running", "paused", "completed"}:
        raise ValueError("Direction project status is unsupported")
    if data["status"] == "completed" and any(
        step["status"] not in {"done", "skipped"} for step in steps
    ):
        raise ValueError("Completed direction project has unfinished steps")
    _timestamp(data["created_at"], "created_at")
    _timestamp(data["updated_at"], "updated_at")
    if datetime.fromisoformat(data["updated_at"]) < datetime.fromisoformat(
        data["created_at"]
    ):
        raise ValueError("Direction project update predates creation")
    return row


def validate_entries(entries: object) -> None:
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
    ):
        raise ValueError(
            "Creative direction replication requires complete entry coverage"
        )
    entry = entries[0]
    if (
        set(entry) != {"entry_id", "rows"}
        or entry["entry_id"] != ENTRY_ID
        or not isinstance(entry["rows"], list)
    ):
        raise ValueError("Creative direction replication coverage is invalid")
    identities: set[str] = set()
    for row in entry["rows"]:
        validate_row(row)
        if row["id"] in identities:
            raise ValueError("Creative direction project identities must be unique")
        identities.add(row["id"])


def read_rows(home: Path, entry_id: str) -> list[dict]:
    if entry_id != ENTRY_ID:
        raise ValueError("Unsupported creative direction entry")
    store = DirectionStore(home)
    with store.db() as database:
        rows = database.execute(
            "SELECT id,record FROM direction_projects ORDER BY id"
        ).fetchall()
    result = [{"id": identity, "data": json.loads(record)} for identity, record in rows]
    validate_entries([{"entry_id": ENTRY_ID, "rows": result}])
    return result


def write_row(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    if entry_id != ENTRY_ID:
        raise ValueError("Unsupported creative direction entry")
    identity = identifier(entity_id)
    store = DirectionStore(home)
    with store.db() as database:
        if row is None:
            database.execute("DELETE FROM direction_projects WHERE id=?", (identity,))
            return
        validate_row(row)
        if row["id"] != identity:
            raise ValueError("Direction project row identity does not match its key")
        database.execute(
            "INSERT INTO direction_projects(id,record) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
            (identity, json.dumps(row["data"], sort_keys=True)),
        )


def _conflict(
    entity_id: str, ancestor: str, local: dict, remote: dict, now: str
) -> conflicts.ConflictRecord:
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


def apply_rows(
    home: Path,
    entry_id: str,
    remote_rows: list[dict],
    ancestors: dict[str, str],
    queue: conflicts.ConflictQueue,
    now: str,
) -> ApplyResult:
    validate_entries([{"entry_id": entry_id, "rows": remote_rows}])
    local = {row["id"]: row for row in read_rows(home, entry_id)}
    remote = {row["id"]: row for row in remote_rows}
    added = updated = removed = conflict_count = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row, ancestor = (
            local.get(entity_id),
            remote.get(entity_id),
            ancestors.get(entity_id, ""),
        )
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha:
            continue
        if ancestor and local_sha != ancestor and remote_sha != ancestor:
            local_marker = local_row or {"id": entity_id, "deleted_at": now}
            remote_marker = remote_row or {"id": entity_id, "deleted_at": now}
            if queue.record(
                _conflict(entity_id, ancestor, local_marker, remote_marker, now)
            ):
                conflict_count += 1
            continue
        if remote_row is None:
            if ancestor and local_sha == ancestor:
                write_row(home, entry_id, None, entity_id)
                removed += 1
        elif local_row is None:
            write_row(home, entry_id, remote_row, entity_id)
            added += 1
        elif not ancestor or local_sha == ancestor:
            write_row(home, entry_id, remote_row, entity_id)
            updated += 1
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, entry_id)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def restore_fields(home: Path, conflict_id: str, fields: list[str], now: str) -> dict:
    queue = conflicts.ConflictQueue(home)
    record = queue.get(conflict_id)
    forbidden = {"id", "created_at", "updated_at", "revision"}
    if (
        record is None
        or record.status != conflicts.STATUS_NEEDS_REVIEW
        or record.entry_id != ENTRY_ID
    ):
        raise ValueError("Creative direction conflict is unavailable")
    if (
        not fields
        or len(fields) > 50
        or any(not isinstance(field, str) or field in forbidden for field in fields)
    ):
        raise ValueError("Invalid creative direction field restoration")
    local_data, remote_data = record.local_row.get("data"), record.remote_row.get(
        "data"
    )
    if (
        not isinstance(local_data, dict)
        or not isinstance(remote_data, dict)
        or any(field not in remote_data for field in fields)
    ):
        raise ValueError("Selected remote direction fields are unavailable")
    merged = dict(local_data)
    for field in fields:
        merged[field] = remote_data[field]
    merged["revision"] = int(local_data["revision"]) + 1
    merged["updated_at"] = now
    write_row(
        home, ENTRY_ID, {"id": record.entity_id, "data": merged}, record.entity_id
    )
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Creative direction conflict queue update failed")
    return {
        "resolved": True,
        "id": record.id,
        "entry_id": ENTRY_ID,
        "entity_id": record.entity_id,
        "fields": fields,
    }
