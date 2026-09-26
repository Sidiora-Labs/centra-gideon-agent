"""Provider-advertised controls for an active ACP session."""

from __future__ import annotations

import asyncio
from typing import Any

from gideon.integrations.acp.errors import AcpError


class LiveControlUnavailable(ValueError):
    """The current ACP session does not advertise this live control."""


class LiveControlRefused(RuntimeError):
    """The ACP provider did not accept a requested live control."""


def offered_controls(
    snapshot: dict[str, Any], dialect: Any, *, model: str = "", effort: str = ""
) -> dict[str, Any]:
    discovered = dialect.normalize_discovery(snapshot)
    models = list(dict.fromkeys(str(value) for value in discovered.models if value))
    efforts = [
        {"value": str(row["value"]), "label": str(row.get("label") or row["value"])}
        for row in discovered.supported_efforts
        if row.get("value")
    ]
    return {
        "model": model,
        "effort": effort,
        "models": models,
        "efforts": efforts,
    }


async def apply_live_control(
    connection: Any,
    dialect: Any,
    session_id: str,
    snapshot: dict[str, Any],
    *,
    axis: str,
    value: str,
    model: str = "",
    effort: str = "",
) -> None:
    offered = offered_controls(snapshot, dialect, model=model, effort=effort)
    if axis == "model":
        choices = offered["models"]
        request = dialect.set_model_request(
            session_id=session_id, model=value, default_model="\0"
        )
    elif axis == "effort":
        choices = [row["value"] for row in offered["efforts"]]
        request = dialect.set_effort_request(session_id=session_id, effort=value)
    else:
        raise LiveControlUnavailable("Unknown live control")
    if not value or value not in choices or request is None:
        raise LiveControlUnavailable(f"This agent does not offer live {axis} selection")
    try:
        reply = await connection.request(request.method, request.params, timeout=15.0)
    except (AcpError, asyncio.TimeoutError, OSError) as exc:
        raise LiveControlRefused(f"The agent did not confirm the {axis} change") from exc
    if reply.error:
        raise LiveControlRefused(f"The agent refused the {axis} change")
