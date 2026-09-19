"""Render the managed core-tool server for an ACP session."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
CORE_SERVER_NAME = "gideon-core"


def _session_environment(session_key: str | None) -> list[dict[str, str]]:
    from gideon.core.config import config_dir
    from gideon.engine import gateway_base

    values = {"GIDEON_HOME": str(config_dir())}
    try:
        values["GIDEON_PORT"] = str(gateway_base.resolve_port())
    except gateway_base.GatewayBaseUnresolved as exc:
        logger.warning("Cannot declare GIDEON_PORT for %s: %s", CORE_SERVER_NAME, exc)
    if session_key:
        values["GIDEON_SESSION_KEY"] = str(session_key)
    return [{"name": name, "value": value} for name, value in values.items()]


def core_mcp_servers(*, session_key: str | None = None) -> list[dict[str, Any]]:
    from gideon.engine.agent import _MANAGED_MCP_SERVERS

    definition = _MANAGED_MCP_SERVERS.get(CORE_SERVER_NAME)
    if not definition:
        return []
    executable = definition.get("command")
    if not executable:
        command_fn = definition.get("command_fn")
        executable = command_fn() if callable(command_fn) else None
    if not executable:
        return []
    raw_args = definition.get("args")
    args = raw_args if isinstance(raw_args, (list, tuple)) else ()
    server = dict(
        name=CORE_SERVER_NAME,
        command=str(executable),
        args=list(map(str, args)),
        env=_session_environment(session_key),
    )
    return [server]
