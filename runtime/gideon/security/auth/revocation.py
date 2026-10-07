"""Native session revocation operation with optional in-process state hooks."""

from __future__ import annotations

import logging
from collections.abc import Callable

from gideon.security.auth import session_payload
from gideon.security.sel import sel

logger = logging.getLogger(__name__)


def revoke_all_sessions(
    *,
    clear_memory: Callable[[], None] | None = None,
    clear_durable: Callable[[], None] | None = None,
    audit: Callable[..., None] | None = None,
    log: logging.Logger | None = None,
) -> None:
    audit_event = sel().log_api_access if audit is None else audit
    audit_event(
        caller="system",
        operation="dashboard_sessions_revoked",
        outcome="ok",
        source="token_auth",
        resources="action=revoke_all",
    )
    if clear_memory is not None:
        clear_memory()
    try:
        durable_clear = (
            session_payload.clear_sessions if clear_durable is None else clear_durable
        )
        durable_clear()
    except Exception:  # noqa: BLE001
        (logger if log is None else log).warning(
            "could not clear the durable session store during revoke", exc_info=True
        )
