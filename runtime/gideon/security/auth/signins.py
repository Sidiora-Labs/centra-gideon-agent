"""Consistent audit rows when owner or integration credentials start and end."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
SIGNED_IN = "session_signed_in"
SIGNED_OUT = "session_signed_out"


def record(
    operation: str,
    *,
    caller: str,
    session_id: str,
    issuer: str,
    kind: str,
    source: str,
    extra: dict[str, Any] | None = None,
) -> None:
    metadata: dict[str, Any] = {"session": session_id, "issuer": issuer, "kind": kind}
    metadata.update(extra or {})
    reason = str(metadata.get("reason") or "")
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=caller or "system",
            operation=operation,
            outcome="ok",
            source=source,
            resources=f"session={session_id} issuer={issuer}{' reason=' + reason if reason else ''}",
        )
    except Exception:  # noqa: BLE001 — auditing must never break auth
        logger.warning("could not record credential lifecycle in SEL", exc_info=True)
