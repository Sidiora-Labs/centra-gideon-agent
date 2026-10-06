"""Short-lived owner receipts for exact agent grant changes."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

_RECEIPTS: dict[str, tuple[float, str, str]] = {}


def _digest(name: str, current: dict, staged: dict) -> str:
    return hashlib.sha256(json.dumps([name, current, staged], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def widening(current: dict, staged: dict) -> list[dict[str, Any]]:
    from gideon.engine.agents.tool_list import widens
    rows = []
    for field in ("tools", "skills"):
        before, after = current.get(field), staged.get(field)
        gains = widens(before, after) if field == "tools" else isinstance(before, (list, tuple)) and bool(before) and (not after or not set(after).issubset(set(before)))
        if field in staged and gains:
            rows.append({"field": field, "before": current.get(field) or [], "after": staged[field], "every": not bool(staged[field])})
    return rows


def preview(owner: str, name: str, current: dict, staged: dict) -> dict:
    rows = widening(current, staged)
    if not rows:
        return {"confirmation_required": False, "changes": []}
    now = time.monotonic()
    for token, row in list(_RECEIPTS.items()):
        if row[0] <= now:
            _RECEIPTS.pop(token, None)
    if len(_RECEIPTS) >= 256:
        _RECEIPTS.pop(next(iter(_RECEIPTS)))
    token = secrets.token_urlsafe(32)
    _RECEIPTS[token] = (now + 300, owner, _digest(name, current, staged))
    return {"confirmation_required": True, "changes": rows, "grant_receipt": token}


def accept(token: Any, owner: str, name: str, current: dict, staged: dict) -> bool:
    if not isinstance(token, str):
        return False
    row = _RECEIPTS.pop(token, None)
    return bool(row and row[0] > time.monotonic() and row[1] == owner and row[2] == _digest(name, current, staged))
