"""Replication of immutable, explicitly imported CLI usage events."""

from __future__ import annotations

import fcntl
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gideon.core.config.loader import config_dir
from gideon.operations.durability import conflicts, inventory
from gideon.operations.durability.shards import machine_id
from gideon.operations.usage_ledger import TurnUsage, UsageJournal

SCOPE = "platform.usage"
ENTRY_ID = "usage.imported_events"
ENTRIES = (ENTRY_ID,)
_FORMATS = {"claude_code_jsonl", "codex_rollout_jsonl"}
_HASH = re.compile(r"[0-9a-f]{64}")
_DATA_FIELDS = {
    "event_id",
    "occurred_at",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "import_format",
    "source_file_sha256",
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


def _active(home):
    root = Path(home).resolve(strict=True)
    if root != config_dir().resolve(strict=True):
        raise ValueError("Usage replication home is not the active workspace")
    path = root / "usage" / "turns.jsonl"
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Usage replication store escapes workspace")
    return root, machine_id(root), path


def _timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("Invalid imported usage timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid imported usage timestamp") from error
    if parsed.utcoffset() is None:
        raise ValueError("Imported usage timestamp requires a timezone")


def _count(value):
    return type(value) is int and 0 <= value <= 2**63 - 1


def validate_record(value, entity_id=None):
    if not isinstance(value, dict) or set(value) != _DATA_FIELDS:
        raise ValueError("Invalid imported usage event")
    event_id = value.get("event_id")
    if (
        not isinstance(event_id, str)
        or _HASH.fullmatch(event_id) is None
        or (entity_id is not None and event_id != entity_id)
    ):
        raise ValueError("Invalid imported usage event identity")
    _timestamp(value.get("occurred_at"))
    if any(
        not _count(value.get(field))
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        )
    ):
        raise ValueError("Invalid imported usage token count")
    if value.get("import_format") not in _FORMATS:
        raise ValueError("Invalid imported usage format")
    source_hash = value.get("source_file_sha256")
    if not isinstance(source_hash, str) or _HASH.fullmatch(source_hash) is None:
        raise ValueError("Invalid imported usage provenance")
    return value


def _validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise ValueError("Invalid imported usage rows")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "data"}:
            raise ValueError("Invalid imported usage row")
        identity = row.get("id")
        if identity in result:
            raise ValueError("Duplicate imported usage event")
        validate_record(row.get("data"), identity)
        result[identity] = row
    return result


def validate_entries(entries):
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
        or set(entries[0]) != {"entry_id", "rows"}
        or entries[0].get("entry_id") != ENTRY_ID
    ):
        raise ValueError("Imported usage replication requires complete entry coverage")
    _validate_rows(entries[0].get("rows"))


def _project(row):
    if (
        row.get("source") != "cli_import"
        or row.get("attribution") != "historical_cli_import"
        or row.get("priced") is not False
        or row.get("cost_usd") != 0
        or row.get("import_format") not in _FORMATS
    ):
        return None
    value = {
        "event_id": row.get("import_record_id"),
        "occurred_at": row.get("ts"),
        "input_tokens": row.get("input_tokens"),
        "output_tokens": row.get("output_tokens"),
        "cache_read_tokens": row.get("cache_read_tokens"),
        "cache_creation_tokens": row.get("cache_creation_tokens"),
        "import_format": row.get("import_format"),
        "source_file_sha256": row.get("import_file_sha256"),
    }
    try:
        validate_record(value, value["event_id"])
    except ValueError:
        return None
    return {"id": value["event_id"], "data": value}


def read_rows(home, entry_id):
    if entry_id != ENTRY_ID:
        raise ValueError("Unknown imported usage replication entry")
    _, instance_id, path = _active(home)
    projected = []
    for row in UsageJournal(path).rows():
        if row.get("instance_id") != instance_id:
            continue
        value = _project(row)
        if value is not None:
            projected.append(value)
    projected.sort(key=lambda row: row["id"])
    _validate_rows(projected)
    return projected


@contextmanager
def _lock(root):
    path = root / "usage" / "replication.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _usage(data, instance_id):
    event_id = data["event_id"]
    provider = "claude" if data["import_format"] == "claude_code_jsonl" else "codex"
    return TurnUsage(
        ts=data["occurred_at"],
        session_key="replicated:" + event_id[:24],
        source="cli_import",
        agent="",
        provider=provider,
        model="unknown",
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
        cache_read_tokens=data["cache_read_tokens"],
        cache_creation_tokens=data["cache_creation_tokens"],
        cost_usd=0.0,
        priced=False,
        instance_id=instance_id,
        provider_instance=f"{provider}-cli-import",
        credential_ref=None,
        subscription_source=None,
        attribution="historical_cli_import",
        import_format=data["import_format"],
        import_source_name="peer-replication",
        import_file_sha256=data["source_file_sha256"],
        import_record_id=event_id,
    )


def _conflict(entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=ENTRY_ID,
        entity_id=entity_id,
        domain=inventory.DOMAIN_PLATFORM,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_PLATFORM),
        ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local),
        remote_sha=conflicts.row_sha(remote),
        local_row=local,
        remote_row=remote,
        detected_at=now,
    )


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id != ENTRY_ID or not isinstance(ancestors, dict):
        raise ValueError("Unknown imported usage replication entry")
    remote = _validate_rows(remote_rows)
    root, instance_id, path = _active(home)
    added = conflict_count = 0
    with _lock(root):
        local = {row["id"]: row for row in read_rows(root, ENTRY_ID)}
        additions = []
        for entity_id in sorted(remote):
            remote_row, local_row = remote[entity_id], local.get(entity_id)
            if local_row is None:
                additions.append(remote_row)
                continue
            if conflicts.row_sha(local_row) != conflicts.row_sha(remote_row):
                if queue.record(
                    _conflict(
                        entity_id,
                        ancestors.get(entity_id, ""),
                        local_row,
                        remote_row,
                        now,
                    )
                ):
                    conflict_count += 1
        journal = UsageJournal(path)
        for row in additions:
            journal.append(_usage(row["data"], instance_id))
            added += 1
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(root, ENTRY_ID)}
    return ApplyResult(added, 0, 0, conflict_count, current)
