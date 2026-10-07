"""Native session-record persistence shared by auth and its HTTP facade."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
SESSIONS_FILE = "sessions.json"
MAX_ENDED_SESSIONS = 512
END_REASONS = frozenset({"expired", "evicted", "revoked", "signed_out", "replaced"})


def read_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"sessions": {}, "ended": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("session store unreadable — treating every session as absent")
        return {"sessions": {}, "ended": {}}
    if not isinstance(value, dict):
        return {"sessions": {}, "ended": {}}
    return {
        "sessions": (
            value.get("sessions") if isinstance(value.get("sessions"), dict) else {}
        ),
        "ended": value.get("ended") if isinstance(value.get("ended"), dict) else {},
    }


def bounded_ended(raw: dict[str, Any], *, limit: int = MAX_ENDED_SESSIONS, reasons: frozenset[str] = END_REASONS) -> dict[str, dict[str, Any]]:
    rows = {
        key: row
        for key, row in raw.items()
        if isinstance(key, str)
        and isinstance(row, dict)
        and row.get("reason") in reasons
    }
    return dict(
        sorted(rows.items(), key=lambda item: float(item[1].get("ended_at") or 0))[
            -limit:
        ]
    )


def write_payload(payload: dict[str, Any], path: Path, *, writer: Callable[..., None] = atomic_write) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        writer(path, json.dumps(payload, indent=2) + "\n", mode=0o600)
    except OSError:
        logger.warning("could not persist the session store", exc_info=True)


def clear_sessions() -> None:
    """Drop active session records, retaining their existing bounded ended history."""
    path = config_loader.config_dir() / SESSIONS_FILE
    ended = bounded_ended(read_payload(path)["ended"])
    write_payload({"sessions": {}, "ended": ended}, path)
