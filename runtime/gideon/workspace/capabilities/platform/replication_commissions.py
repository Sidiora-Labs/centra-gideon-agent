"""Selective replication of canonical commission definitions without execution authority."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gideon.core.sqlite_compat import sqlite3
from gideon.operations.durability import conflicts, inventory
from gideon.workspace.capabilities.creative.commissions import ABILITIES, OPERATIONS


SCOPE = "creative.commissions"
ENTRY_ID = "creative.commission_records"
ENTRIES = (ENTRY_ID,)
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
_FIELDS = {"id", "revision", "name", "target_ability", "brief", "sources", "steps", "created_at", "updated_at"}
_RESTORABLE = {"name", "brief", "sources", "steps"}
_PRIVATE = {"request_id", "credential", "credential_ref", "secret", "token", "dispatch", "cadence", "enabled",
            "max_attempts", "mode", "schedule_revision", "schedule_error", "schedule_state", "next_fire_at",
            "claim_id", "claim_pid", "claim_identity", "runs", "feedback", "trigger", "artifact", "bytes"}
_SAFE_CADENCE = {"kind": "interval", "seconds": 31536000, "timezone": "UTC",
                 "spec": {"kind": "interval", "interval_secs": 31536000, "timezone": "UTC"}}


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


def _path(home):
    path = Path(home) / "capabilities/creative/commissions.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE IF NOT EXISTS creative_commissions(id TEXT PRIMARY KEY, record TEXT NOT NULL)")
    return path


def _exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"Invalid commission {label}")


def _identifier(value, label="identity"):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"Invalid commission {label}")
    return value


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
        raise ValueError(f"Invalid commission {label}")
    return value


def _positive(value):
    return type(value) is int and 0 < value <= 2**31


def _timestamp(value, label):
    _text(value, label, 64, True)
    try:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError
    except ValueError as error:
        raise ValueError(f"Invalid commission {label}") from error


def _contains_private(value):
    if isinstance(value, dict):
        return bool(_PRIVATE & set(value)) or any(_contains_private(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private(item) for item in value)
    return False


def _brief(value):
    _exact(value, ("intent", "genre", "category", "style", "constraints", "seed_refs"), "brief")
    _text(value["intent"], "brief intent", 4000, True)
    _text(value["genre"], "brief genre", 100)
    _text(value["category"], "brief category", 100)
    _text(value["style"], "brief style", 8000)
    if not isinstance(value["constraints"], dict) or len(value["constraints"]) > 20:
        raise ValueError("Invalid commission brief constraints")
    for key, item in value["constraints"].items():
        _text(key, "brief constraint key", 80, True); _text(item, "brief constraint value", 500)
    if not isinstance(value["seed_refs"], list) or len(value["seed_refs"]) > 50:
        raise ValueError("Invalid commission brief seed references")
    for item in value["seed_refs"]:
        _identifier(item, "brief seed reference")


def _sources(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ValueError("Commission requires one to twenty canonical source pins")
    seen = set()
    for source in values:
        _exact(source, ("kind", "id", "revision"), "source pin")
        if source["kind"] not in ("work", "series") or not _positive(source["revision"]):
            raise ValueError("Invalid commission source pin")
        key = (source["kind"], _identifier(source["id"], "source identity"), source["revision"])
        if key in seen:
            raise ValueError("Commission source pins must be unique")
        seen.add(key)


def _steps(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise ValueError("Commission requires one to ten plan steps")
    seen = []
    for step in values:
        _exact(step, ("id", "title", "operation", "depends_on"), "plan step")
        identity = _identifier(step["id"], "step identity")
        _text(step["title"], "step title", 200, True)
        if identity in seen or step["operation"] not in OPERATIONS or not isinstance(step["depends_on"], list):
            raise ValueError("Invalid commission plan step")
        dependencies = [_identifier(item, "step dependency") for item in step["depends_on"]]
        if len(dependencies) != len(set(dependencies)) or any(item not in seen for item in dependencies):
            raise ValueError("Commission steps may depend only on earlier steps")
        seen.append(identity)


def validate_record(value, entity_id=None):
    _exact(value, _FIELDS, "definition")
    if _contains_private(value) or (entity_id is not None and value["id"] != entity_id):
        raise ValueError("Commission definition contains private or mismatched fields")
    _identifier(value["id"])
    if not _positive(value["revision"]) or value["target_ability"] not in ABILITIES:
        raise ValueError("Invalid commission definition")
    _text(value["name"], "name", 200, True)
    _brief(value["brief"]); _sources(value["sources"]); _steps(value["steps"])
    _timestamp(value["created_at"], "created_at"); _timestamp(value["updated_at"], "updated_at")
    if datetime.fromisoformat(value["updated_at"]) < datetime.fromisoformat(value["created_at"]):
        raise ValueError("Commission update predates creation")


def validate_entries(entries):
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("Commission replication requires complete entry coverage")
    entry = entries[0]
    if set(entry) != {"entry_id", "rows"} or entry["entry_id"] != ENTRY_ID or not isinstance(entry["rows"], list):
        raise ValueError("Invalid commission replication coverage")
    identities = set()
    for row in entry["rows"]:
        _exact(row, ("id", "data"), "row")
        _identifier(row["id"], "row identity")
        if row["id"] in identities:
            raise ValueError("Duplicate commission row")
        validate_record(row["data"], row["id"]); identities.add(row["id"])


def _project(record):
    value = {key: record.get(key) for key in _FIELDS}
    validate_record(value, value.get("id"))
    return value


def read_rows(home, entry_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown commission replication entry")
    with sqlite3.connect(_path(home)) as database:
        rows = database.execute("SELECT id,record FROM creative_commissions ORDER BY id").fetchall()
    result = [{"id": identity, "data": _project(json.loads(encoded))} for identity, encoded in rows]
    validate_entries([{"entry_id": ENTRY_ID, "rows": result}])
    return result


def _safe_record(data):
    return {**data, "cadence": _SAFE_CADENCE, "enabled": False, "max_attempts": 1, "mode": "planning",
            "dispatch": None, "schedule_revision": 1, "schedule_error": "", "schedule_state": "disabled",
            "next_fire_at": ""}


def _safe_authority(record):
    return (record.get("cadence") == _SAFE_CADENCE and record.get("enabled") is False and
            record.get("max_attempts") == 1 and record.get("mode") == "planning" and
            record.get("dispatch") is None and record.get("schedule_revision") == 1 and
            record.get("schedule_error") == "" and record.get("schedule_state") == "disabled" and
            record.get("next_fire_at") == "")


def _full(home, entity_id):
    with sqlite3.connect(_path(home)) as database:
        row = database.execute("SELECT record FROM creative_commissions WHERE id=?", (entity_id,)).fetchone()
    return json.loads(row[0]) if row else None


def write_row(home, entry_id, row, entity_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown commission replication entry")
    _identifier(entity_id)
    path = _path(home)
    with sqlite3.connect(path) as database:
        if row is None:
            database.execute("DELETE FROM creative_commissions WHERE id=?", (entity_id,)); return
        _exact(row, ("id", "data"), "row")
        if row["id"] != entity_id:
            raise ValueError("Commission row identity changed")
        validate_record(row["data"], entity_id)
        prior = database.execute("SELECT record FROM creative_commissions WHERE id=?", (entity_id,)).fetchone()
        record = _safe_record(row["data"]) if prior is None else {**json.loads(prior[0]), **row["data"]}
        database.execute("INSERT INTO creative_commissions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                         (entity_id, json.dumps(record, sort_keys=True)))


def missing_dependencies(home, row):
    validate_record(row["data"], row["id"])
    path = Path(home) / "capabilities/creative/catalog.sqlite3"
    if not path.exists():
        return list(row["data"]["sources"])
    missing = []
    with sqlite3.connect(path) as database:
        for source in row["data"]["sources"]:
            table = "work_revisions" if source["kind"] == "work" else "series_revisions"
            try:
                found = database.execute(f"SELECT 1 FROM {table} WHERE id=? AND revision=?", (source["id"], source["revision"])).fetchone()
            except sqlite3.OperationalError:
                found = None
            if not found:
                missing.append(source)
    return missing


def _conflict(entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(entry_id=ENTRY_ID, entity_id=entity_id, domain=inventory.DOMAIN_WORK,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_WORK), ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local), remote_sha=conflicts.row_sha(remote), local_row=local,
        remote_row=remote, detected_at=now)


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown commission replication entry")
    validate_entries([{"entry_id": ENTRY_ID, "rows": remote_rows}])
    local = {row["id"]: row for row in read_rows(home, entry_id)}
    remote = {row["id"]: row for row in remote_rows}
    added = updated = removed = conflict_count = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        local_row, remote_row, ancestor = local.get(entity_id), remote.get(entity_id), ancestors.get(entity_id, "")
        local_sha = conflicts.row_sha(local_row) if local_row else ""
        remote_sha = conflicts.row_sha(remote_row) if remote_row else ""
        if local_sha == remote_sha:
            continue
        authority_changed = local_row is not None and not _safe_authority(_full(home, entity_id))
        divergent = local_row is not None and remote_row is not None and (not ancestor or (local_sha != ancestor and remote_sha != ancestor))
        unsafe_delete = remote_row is None and ancestor and local_sha == ancestor and authority_changed
        if divergent or unsafe_delete or (ancestor and local_sha != ancestor and remote_sha != ancestor):
            local_marker = local_row or {"id": entity_id, "deleted_at": now}
            remote_marker = remote_row or {"id": entity_id, "deleted_at": now}
            if queue.record(_conflict(entity_id, ancestor, local_marker, remote_marker, now)):
                conflict_count += 1
            continue
        if remote_row is None:
            if ancestor and local_sha == ancestor:
                write_row(home, entry_id, None, entity_id); removed += 1
        elif local_row is None:
            write_row(home, entry_id, remote_row, entity_id); added += 1
        elif ancestor and local_sha == ancestor:
            write_row(home, entry_id, remote_row, entity_id); updated += 1
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, entry_id)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def restore_fields(home, conflict_id, fields, now):
    queue = conflicts.ConflictQueue(home); record = queue.get(conflict_id)
    if record is None or record.status != conflicts.STATUS_NEEDS_REVIEW or record.entry_id != ENTRY_ID:
        raise ValueError("Commission conflict is unavailable")
    if not fields or len(fields) != len(set(fields)) or any(field not in _RESTORABLE for field in fields):
        raise ValueError("Invalid commission field restoration")
    local, remote = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local, dict) or not isinstance(remote, dict) or any(field not in remote for field in fields):
        raise ValueError("Selected remote commission fields are unavailable")
    merged = dict(local)
    for field in fields:
        merged[field] = remote[field]
    merged["revision"] = local["revision"] + 1; merged["updated_at"] = now
    validate_record(merged, record.entity_id)
    write_row(home, ENTRY_ID, {"id": record.entity_id, "data": merged}, record.entity_id)
    record.status = conflicts.STATUS_RESOLVED; record.resolution = "merge_fields:" + ",".join(fields); record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Commission conflict queue update failed")
    return {"resolved": True, "id": record.id, "entry_id": ENTRY_ID, "entity_id": record.entity_id, "fields": fields}
