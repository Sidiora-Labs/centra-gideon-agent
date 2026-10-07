"""Signed owner acceptance of one immutable maintenance sequence scope."""

from __future__ import annotations

import hashlib
import hmac
import json

from gideon.security.approval_answer import OWNER, principal_from_record
from gideon.security.durable_work import (
    accepted_origin_values,
    persist_accepted_origin,
    restore_accepted_origin,
)
from gideon.security.session_signing import KEY_BYTES, key_path, load_or_create_key

_DOMAIN = b"gideon.accepted-maintenance.v1\0"
_FIELDS = (
    "id",
    "project_id",
    "workspace",
    "workflow",
    "definition_hash",
    "verify_command",
    "guard_command",
    "actor",
    "task_list_id",
)


def _scope(row):
    return {field: row[field] for field in _FIELDS}


def _owner_origin(origin, actor):
    values = accepted_origin_values(origin)
    initiator = principal_from_record(values.get("initiator")) if values else None
    effective = principal_from_record(values.get("work_actor")) if values else None
    if (
        values is None
        or initiator is None
        or initiator.kind != OWNER
        or effective != initiator
        or not initiator.name
        or values.get("memory_mode") != "persistent"
        or values.get("created_by_app")
        or values.get("run_id")
        or actor != "user:" + initiator.name
    ):
        raise ValueError("Authenticated persistent owner maintenance source required")
    return origin


def seal_acceptance(row, origin):
    _owner_origin(origin, row["actor"])
    record = persist_accepted_origin(origin)
    if record is None:
        raise ValueError("Maintenance source proof unavailable")
    payload = json.dumps(
        {"scope": _scope(row), "origin": record}, sort_keys=True, separators=(",", ":")
    )
    return {
        "payload": payload,
        "signature": hmac.new(
            load_or_create_key(), _DOMAIN + payload.encode(), hashlib.sha256
        ).hexdigest(),
    }


def accepted_source(row):
    record = row.get("work_acceptance")
    try:
        if not isinstance(record, dict) or set(record) != {"payload", "signature"}:
            raise ValueError("Maintenance source proof unavailable")
        payload, signature = record["payload"], record["signature"]
        if not isinstance(payload, str) or not isinstance(signature, str):
            raise ValueError("Maintenance source proof invalid")
        key = key_path().read_bytes()
        expected = hmac.new(key, _DOMAIN + payload.encode(), hashlib.sha256).hexdigest()
        if len(key) < KEY_BYTES or not hmac.compare_digest(expected, signature):
            raise ValueError("Maintenance source proof invalid")
        values = json.loads(payload)
        if values.get("scope") != _scope(row):
            raise ValueError("Maintenance accepted scope changed")
        origin = restore_accepted_origin(values.get("origin"))
        return _owner_origin(origin, row["actor"])
    except (KeyError, TypeError, json.JSONDecodeError, OSError) as error:
        raise ValueError("Maintenance source proof unavailable or invalid") from error
