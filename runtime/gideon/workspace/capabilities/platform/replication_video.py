"""Canonical current timeline-project adapter for opt-in peer replication."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gideon.operations.durability import conflicts, inventory

SCOPE = "media.video_projects"
ENTRY_ID = "media.timelines"
ENTRIES = (ENTRY_ID,)
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
_PROJECT_FIELDS = {
    "id",
    "revision",
    "title",
    "width",
    "height",
    "fps",
    "segments",
    "overlays",
    "audio",
    "duration",
    "updated_at",
}
_SEGMENT_FIELDS = {"artifact_id", "version", "kind", "start", "duration"}
_OVERLAY_FIELDS = {
    "artifact_id",
    "version",
    "start",
    "duration",
    "x",
    "y",
    "width",
    "height",
}
_AUDIO_FIELDS = {
    "artifact_id",
    "version",
    "start",
    "trim",
    "duration",
    "volume",
    "fade_in",
    "fade_out",
}
_PRIVATE = {
    "request_id",
    "credential",
    "credential_ref",
    "token",
    "secret",
    "bytes",
    "job_id",
    "events",
    "result",
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


def _path(home: Path) -> Path:
    path = Path(home) / "capabilities/media/timelines.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as database:
        database.execute(
            "CREATE TABLE IF NOT EXISTS timelines (id TEXT, revision INTEGER, body TEXT, PRIMARY KEY(id,revision))"
        )
        database.execute(
            "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, body TEXT)"
        )
    return path


def _integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Invalid timeline {name}")
    return value


def _number(value: object, name: str, low: float, high: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"Invalid timeline {name}")
    return float(value)


def _pin(value: dict, expected: set[str], name: str) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"Timeline {name} has an unsupported shape")
    identity = value["artifact_id"]
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise ValueError(f"Timeline {name} requires a canonical artifact ID")
    return identity, _integer(
        value["version"], f"{name} artifact version", 1, 1_000_000
    )


def _contains_private(value: object) -> bool:
    if isinstance(value, dict):
        return bool(_PRIVATE & set(value)) or any(
            _contains_private(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_private(item) for item in value)
    return False


def validate_row(row: object) -> set[str]:
    if (
        not isinstance(row, dict)
        or set(row) != {"id", "data"}
        or not isinstance(row["data"], dict)
    ):
        raise ValueError("Invalid canonical timeline row")
    identity, data = row["id"], row["data"]
    if (
        not isinstance(identity, str)
        or not _ID.fullmatch(identity)
        or set(data) != _PROJECT_FIELDS
        or data.get("id") != identity
        or _contains_private(data)
    ):
        raise ValueError("Timeline project contains non-current or private fields")
    _integer(data["revision"], "revision", 1, 1_000_000)
    if not isinstance(data["title"], str) or not 1 <= len(data["title"].strip()) <= 120:
        raise ValueError("Invalid timeline title")
    width = _integer(data["width"], "width", 2, 1920)
    height = _integer(data["height"], "height", 2, 1920)
    if width % 2 or height % 2:
        raise ValueError("Timeline dimensions must be even")
    _integer(data["fps"], "fps", 1, 60)
    segments, overlays, audio = data["segments"], data["overlays"], data["audio"]
    if (
        not isinstance(segments, list)
        or not 1 <= len(segments) <= 20
        or not isinstance(overlays, list)
        or len(overlays) > 20
        or not isinstance(audio, list)
        or len(audio) > 20
    ):
        raise ValueError("Timeline tracks are outside supported bounds")
    pins: set[str] = set()
    total = 0.0
    for segment in segments:
        artifact_id, _ = _pin(segment, _SEGMENT_FIELDS, "segment")
        pins.add(artifact_id)
        if segment["kind"] not in {"image", "video"}:
            raise ValueError("Timeline segment kind is unsupported")
        start = _number(segment["start"], "segment start", 0, 3600)
        duration = _number(segment["duration"], "segment duration", 0.05, 300)
        if segment["kind"] == "image" and start != 0:
            raise ValueError("Timeline image segments must start at zero")
        total += duration
    if total > 300 or not math.isclose(
        _number(data["duration"], "duration", 0.05, 300), total, rel_tol=0, abs_tol=1e-9
    ):
        raise ValueError("Timeline duration does not match its canonical segments")
    for overlay in overlays:
        artifact_id, _ = _pin(overlay, _OVERLAY_FIELDS, "overlay")
        pins.add(artifact_id)
        start = _number(overlay["start"], "overlay start", 0, total)
        duration = _number(overlay["duration"], "overlay duration", 0.05, total)
        if start + duration > total:
            raise ValueError("Timeline overlay exceeds project duration")
        x = _integer(overlay["x"], "overlay x", 0, width - 1)
        y = _integer(overlay["y"], "overlay y", 0, height - 1)
        _integer(overlay["width"], "overlay width", 1, width - x)
        _integer(overlay["height"], "overlay height", 1, height - y)
    for track in audio:
        artifact_id, _ = _pin(track, _AUDIO_FIELDS, "audio")
        pins.add(artifact_id)
        start = _number(track["start"], "audio start", 0, total)
        _number(track["trim"], "audio trim", 0, 3600)
        duration = _number(track["duration"], "audio duration", 0.05, total)
        if start + duration > total:
            raise ValueError("Timeline audio exceeds project duration")
        _number(track["volume"], "audio volume", 0, 2)
        _number(track["fade_in"], "audio fade in", 0, duration)
        _number(track["fade_out"], "audio fade out", 0, duration)
    updated = data["updated_at"]
    if not isinstance(updated, str) or len(updated) > 64:
        raise ValueError("Invalid timeline updated_at")
    try:
        if datetime.fromisoformat(updated).utcoffset() is None:
            raise ValueError
    except ValueError as error:
        raise ValueError("Invalid timeline updated_at") from error
    return pins


def validate_entries(entries: object) -> None:
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
    ):
        raise ValueError("Video-project replication requires complete entry coverage")
    entry = entries[0]
    if (
        set(entry) != {"entry_id", "rows"}
        or entry["entry_id"] != ENTRY_ID
        or not isinstance(entry["rows"], list)
    ):
        raise ValueError("Video-project replication coverage is invalid")
    identities: set[str] = set()
    dependencies: set[str] = set()
    for row in entry["rows"]:
        pins = validate_row(row)
        if row["id"] in identities:
            raise ValueError("Timeline project identities must be unique")
        identities.add(row["id"])
        dependencies.update(pins)
    if identities & dependencies:
        raise ValueError(
            "Timeline projects cannot treat transmitted project identities as artifact dependencies"
        )


def read_rows(home: Path, entry_id: str) -> list[dict]:
    if entry_id != ENTRY_ID:
        raise ValueError("Unsupported video-project entry")
    with sqlite3.connect(_path(home)) as database:
        rows = database.execute(
            "SELECT t.id,t.body FROM timelines t WHERE t.revision=(SELECT max(s.revision) FROM timelines s WHERE s.id=t.id) ORDER BY t.id"
        ).fetchall()
    result = [{"id": identity, "data": json.loads(body)} for identity, body in rows]
    validate_entries([{"entry_id": ENTRY_ID, "rows": result}])
    return result


def write_row(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    if (
        entry_id != ENTRY_ID
        or not isinstance(entity_id, str)
        or not _ID.fullmatch(entity_id)
    ):
        raise ValueError("Unsupported video-project identity")
    path = _path(home)
    with sqlite3.connect(path) as database:
        if row is None:
            database.execute("DELETE FROM timelines WHERE id=?", (entity_id,))
            return
        validate_row(row)
        if row["id"] != entity_id:
            raise ValueError("Timeline row identity does not match its key")
        data = row["data"]
        database.execute(
            "INSERT INTO timelines(id,revision,body) VALUES(?,?,?) ON CONFLICT(id,revision) DO UPDATE SET body=excluded.body",
            (entity_id, data["revision"], json.dumps(data, sort_keys=True)),
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
    forbidden = {"id", "revision", "updated_at"}
    if (
        record is None
        or record.status != conflicts.STATUS_NEEDS_REVIEW
        or record.entry_id != ENTRY_ID
    ):
        raise ValueError("Video-project conflict is unavailable")
    if (
        not fields
        or len(fields) > 20
        or any(not isinstance(field, str) or field in forbidden for field in fields)
    ):
        raise ValueError("Invalid video-project field restoration")
    local_data, remote_data = record.local_row.get("data"), record.remote_row.get(
        "data"
    )
    if (
        not isinstance(local_data, dict)
        or not isinstance(remote_data, dict)
        or any(field not in remote_data for field in fields)
    ):
        raise ValueError("Selected remote video-project fields are unavailable")
    merged = dict(local_data)
    for field in fields:
        merged[field] = remote_data[field]
    merged["revision"] = int(local_data["revision"]) + 1
    merged["updated_at"] = now
    row = {"id": record.entity_id, "data": merged}
    validate_entries([{"entry_id": ENTRY_ID, "rows": [row]}])
    write_row(home, ENTRY_ID, row, record.entity_id)
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Video-project conflict queue update failed")
    return {
        "resolved": True,
        "id": record.id,
        "entry_id": ENTRY_ID,
        "entity_id": record.entity_id,
        "fields": fields,
    }
