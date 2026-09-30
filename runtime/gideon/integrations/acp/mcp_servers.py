"""Render the managed core-tool server for an ACP session."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)
CORE_SERVER_NAME = "gideon-core"
_CORE_NAME_KINDS = frozenset(("", "other"))
_OWNER_NOTIFY_FIELDS = ("text", "title", "unfurl_links", "unfurl_media", "session")


def _arguments(tool_input: object) -> dict[str, Any] | None:
    if isinstance(tool_input, dict):
        return tool_input
    if isinstance(tool_input, str):
        try:
            parsed = json.loads(tool_input) if tool_input.strip() else {}
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _core_call(
    title: str, tool_kind: str, tool_input: object
) -> tuple[str, dict, bool]:
    """Resolve only wire forms that unambiguously name a Gideon core MCP tool."""
    title = title or ""
    kind = (tool_kind or "").lower()
    args = _arguments(tool_input)
    prefix = f"mcp__{CORE_SERVER_NAME}__"
    if kind in _CORE_NAME_KINDS and title.startswith(prefix):
        return title[len(prefix) :], args or {}, args is not None
    if (
        kind == "execute"
        and args is not None
        and args.get("server") == CORE_SERVER_NAME
    ):
        name = args.get("tool")
        raw = args.get("arguments")
        if isinstance(name, str) and title == f"mcp.{CORE_SERVER_NAME}.{name}":
            return name, raw if isinstance(raw, dict) else {}, isinstance(raw, dict)
    return "", {}, False


def core_tool_declaration(
    title: str, tool_kind: str, tool_input: object
) -> tuple[str, bool]:
    """Return canonical risk and owner-only effect for an exact, schema-valid core call."""
    from gideon.assurance.validation import ValidationError
    from gideon.engine.agents.native.tools import _describe_mcp_tool
    from gideon.integrations import mcp_core, mcp_shared

    name, args, exact = _core_call(title, tool_kind, tool_input)
    if not exact or not name:
        return "", False
    declaration = next(
        (tool for tool in mcp_core._list_tools() if tool.get("name") == name), None
    )
    if declaration is None:
        return "", False
    try:
        validated = mcp_core._validate_args(name, args)
    except ValidationError:
        return "", False
    risk = _describe_mcp_tool(declaration, CORE_SERVER_NAME).risk_level.value
    tells_owner = (
        name == "notify"
        and validated.get("session") == "channel"
        and mcp_shared.only_tells_the_owner(_OWNER_NOTIFY_FIELDS, args)
    )
    return risk, tells_owner


def _session_environment(
    session_key: str | None,
    leaf_context: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    from gideon.core.config import config_dir
    from gideon.engine import gateway_base
    from gideon.integrations.mcp_shared import current_leaf_lineage, leaf_lineage

    values = {"GIDEON_HOME": str(config_dir())}
    values.update(
        current_leaf_lineage()
        if leaf_context is None
        else leaf_lineage(leaf_context)
    )
    try:
        values["GIDEON_PORT"] = str(gateway_base.resolve_port())
    except gateway_base.GatewayBaseUnresolved as exc:
        logger.warning("Cannot declare GIDEON_PORT for %s: %s", CORE_SERVER_NAME, exc)
    if session_key:
        values["GIDEON_SESSION_KEY"] = str(session_key)
    return [{"name": name, "value": value} for name, value in values.items()]


def core_mcp_servers(
    *,
    session_key: str | None = None,
    leaf_context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
        env=_session_environment(session_key, leaf_context),
    )
    return [server]
