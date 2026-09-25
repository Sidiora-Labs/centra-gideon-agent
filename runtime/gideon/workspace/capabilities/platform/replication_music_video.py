"""Canonical current music-video project replication adapter."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from gideon.operations.durability import conflicts, inventory
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.video import VideoStore


SCOPE = "music.video"
ENTRY_ID = "music.video_projects"
ENTRIES = (ENTRY_ID,)
_PROJECT_FIELDS = {
    "id", "revision", "title", "track_id", "render_id", "tempo_bpm",
    "offset_seconds", "scenes", "duration_seconds", "audio_ref",
}
_SCENE_FIELDS = {"id", "image_ref", "beats", "start_seconds", "duration_seconds"}
_REF_FIELDS = {"slug", "version"}
_PRIVATE_FIELDS = {
    "request_id", "credential", "credentials", "credential_ref", "secret",
    "secrets", "token", "access_token", "refresh_token", "provider",
    "provider_config", "job", "jobs", "task", "tasks", "bytes", "content",
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


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"Invalid music video {name}")
    return value


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Invalid music video {name}")
    return value


def _number(value, name, low=0.0, high=float("inf")):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid music video {name}")
    return value


def _contains_private(value):
    if isinstance(value, dict):
        return bool(_PRIVATE_FIELDS & set(value)) or any(_contains_private(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private(item) for item in value)
    return False


def _reference(value, label):
    if not isinstance(value, dict) or set(value) != _REF_FIELDS:
        raise ValueError(f"Invalid music video {label} pin")
    _text(value["slug"], f"{label} slug", 200)
    _integer(value["version"], f"{label} version", 1, 1_000_000)


def validate_record(value, entity_id=None):
    if not isinstance(value, dict) or set(value) != _PROJECT_FIELDS or _contains_private(value):
        raise ValueError("Invalid or private music video project")
    _text(value["id"], "project id", 200)
    if entity_id is not None and value["id"] != entity_id:
        raise ValueError("Music video project identity changed")
    _integer(value["revision"], "revision", 1, 1_000_000_000)
    _text(value["title"], "title", 200)
    _text(value["track_id"], "track id", 100)
    _text(value["render_id"], "render id", 100)
    tempo = _integer(value["tempo_bpm"], "tempo", 20, 300)
    _number(value["offset_seconds"], "offset")
    _reference(value["audio_ref"], "audio")
    scenes = value["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 16:
        raise ValueError("Music video requires one to sixteen scenes")
    identities = set()
    elapsed = 0.0
    for scene in scenes:
        if not isinstance(scene, dict) or set(scene) != _SCENE_FIELDS:
            raise ValueError("Invalid music video scene")
        scene_id = _text(scene["id"], "scene id", 100)
        if scene_id in identities:
            raise ValueError("Music video scene identities must be unique")
        identities.add(scene_id)
        _reference(scene["image_ref"], "image")
        beats = _integer(scene["beats"], "scene beats", 1, 64)
        start = _number(scene["start_seconds"], "scene start", 0, 60)
        duration = _number(scene["duration_seconds"], "scene duration", 0, 60)
        expected = beats * 60 / tempo
        if not math.isclose(start, elapsed, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("Music video scene start does not match beat grid")
        if not math.isclose(duration, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("Music video scene duration does not match beat grid")
        elapsed += expected
    total = _number(value["duration_seconds"], "duration", 0, 60)
    if not math.isclose(total, elapsed, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("Music video duration does not match its scenes")
    return value


def validate_entries(entries):
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("Invalid music video replication coverage")
    entry = entries[0]
    if set(entry) != {"entry_id", "rows"} or entry["entry_id"] != ENTRY_ID or not isinstance(entry["rows"], list):
        raise ValueError("Invalid music video replication coverage")
    identities = set()
    for row in entry["rows"]:
        if not isinstance(row, dict) or set(row) != {"id", "data"}:
            raise ValueError("Invalid music video row")
        identity = _text(row["id"], "row identity", 200)
        if identity in identities:
            raise ValueError("Duplicate music video row")
        validate_record(row["data"], identity)
        identities.add(identity)


def _store(home):
    root = Path(home)
    artifacts = NativeArtifactProvider(root=root / "artifacts")
    music = root / "capabilities" / "music"
    return VideoStore(music, MusicCatalog(music, artifacts))


def read_rows(home, entry_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown music video replication entry")
    store = _store(home)
    with store._db() as database:
        rows = database.execute("SELECT id,payload FROM projects ORDER BY id").fetchall()
    result = [{"id": identity, "data": json.loads(payload)} for identity, payload in rows]
    validate_entries([{"entry_id": ENTRY_ID, "rows": result}])
    return result


def write_row(home, entry_id, row, entity_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown music video replication entry")
    identity = _text(entity_id, "row identity", 200)
    store = _store(home)
    with store._db() as database:
        if row is None:
            database.execute("DELETE FROM projects WHERE id=?", (identity,))
            return
        if not isinstance(row, dict) or set(row) != {"id", "data"} or row["id"] != identity:
            raise ValueError("Music video row identity changed")
        validate_record(row["data"], identity)
        database.execute(
            "INSERT INTO projects(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (identity, json.dumps(row["data"], sort_keys=True)),
        )


def _conflict(entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=ENTRY_ID, entity_id=entity_id, domain=inventory.DOMAIN_WORK,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_WORK), ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local), remote_sha=conflicts.row_sha(remote),
        local_row=local, remote_row=remote, detected_at=now,
    )


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown music video replication entry")
    validate_entries([{"entry_id": ENTRY_ID, "rows": remote_rows}])
    local = {row["id"]: row for row in read_rows(home, ENTRY_ID)}
    remote = {row["id"]: row for row in remote_rows}
    added = updated = removed = conflict_count = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row, ancestor = local.get(entity_id), remote.get(entity_id), ancestors.get(entity_id, "")
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha:
            continue
        if ancestor and local_sha != ancestor and remote_sha != ancestor:
            local_marker = local_row or {"id": entity_id, "deleted_at": now}
            remote_marker = remote_row or {"id": entity_id, "deleted_at": now}
            if queue.record(_conflict(entity_id, ancestor, local_marker, remote_marker, now)):
                conflict_count += 1
        elif remote_row is None:
            if ancestor and local_sha == ancestor:
                write_row(home, ENTRY_ID, None, entity_id)
                removed += 1
        elif local_row is None:
            write_row(home, ENTRY_ID, remote_row, entity_id)
            added += 1
        elif not ancestor or local_sha == ancestor:
            write_row(home, ENTRY_ID, remote_row, entity_id)
            updated += 1
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, ENTRY_ID)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def restore_fields(home, record_id, fields, now):
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    forbidden = {"id", "revision"}
    if record is None or record.status != conflicts.STATUS_NEEDS_REVIEW or record.entry_id != ENTRY_ID:
        raise ValueError("Music video conflict is not available")
    if not fields or len(fields) > 20 or len(set(fields)) != len(fields) or any(not isinstance(field, str) or field in forbidden for field in fields):
        raise ValueError("Invalid music video conflict fields")
    local_data, remote_data = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local_data, dict) or not isinstance(remote_data, dict) or any(field not in remote_data for field in fields):
        raise ValueError("Selected music video fields are unavailable")
    merged = dict(local_data)
    for field in fields:
        merged[field] = remote_data[field]
    merged["revision"] = local_data["revision"] + 1
    validate_record(merged, record.entity_id)
    write_row(home, ENTRY_ID, {"id": record.entity_id, "data": merged}, record.entity_id)
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Music video conflict queue update failed")
    return {"resolved": True, "id": record.id, "entry_id": ENTRY_ID,
            "entity_id": record.entity_id, "fields": fields}
