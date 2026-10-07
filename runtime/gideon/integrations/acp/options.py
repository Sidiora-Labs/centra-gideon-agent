"""Validated ACP session declarations."""

from __future__ import annotations

import json


def session_metadata(value: dict | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("session_meta must be a JSON object")
    try:
        encoded = json.dumps(value, allow_nan=False)
        result = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("session_meta must be a JSON object") from error
    if result != value or any(not isinstance(key, str) for key in value):
        raise ValueError("session_meta must use JSON object keys and values")
    return result


def compacts_itself(value: bool = False) -> bool:
    if not isinstance(value, bool):
        raise ValueError("compacts_itself must be a boolean")
    return value
