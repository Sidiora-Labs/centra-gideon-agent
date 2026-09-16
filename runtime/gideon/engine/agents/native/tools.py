"""Schema adaptation, executor invocation and model-facing tool messages."""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import json
import logging
from functools import partial
from typing import Any

from gideon.integrations.tool_providers.base import (
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

logger = logging.getLogger(__name__)
ARGUMENTS_UNREADABLE = object()


def _object_schema() -> dict:
    return {"type": "object", "properties": {}}


def tool_definitions_to_openai_schema(tools: list[ToolDefinition]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description or "",
                "parameters": definition.parameters or _object_schema(),
            },
        }
        for definition in tools
    ]


def _describe_mcp_tool(raw: dict, provider: str) -> ToolDefinition:
    from gideon.engine.task_modes import infer_risk_from_name
    from gideon.integrations.tool_providers.base import RiskLevel

    name = str(raw.get("name", ""))
    declared = str(raw.get("risk_level", "")).lower()
    if declared not in {"safe", "caution", "destructive"}:
        declared = infer_risk_from_name(name)
    parameters = next(
        (
            raw[key]
            for key in ("inputSchema", "input_schema", "parameters")
            if raw.get(key)
        ),
        None,
    )
    return ToolDefinition(
        name=name,
        description=str(raw.get("description", "")),
        parameters=parameters or _object_schema(),
        provider=provider,
        requires_approval=True,
        risk_level=RiskLevel(declared),
    )


class InProcessMcpToolProvider(ToolProvider):
    def __init__(
        self,
        *,
        module: str = "gideon.integrations.mcp_core",
        provider_name: str = "gideon-core",
        display: str = "Gideon Core",
    ) -> None:
        self._module = module
        self._provider_name = provider_name
        self._display = display
        self._tools: list[ToolDefinition] | None = None

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return self._display

    def _import_module(self):
        return importlib.import_module(self._module)

    async def list_tools(self) -> list[ToolDefinition]:
        if self._tools is None:
            discover = self._import_module()._list_tools
            declarations = await asyncio.get_event_loop().run_in_executor(
                None, discover
            )
            self._tools = [
                _describe_mcp_tool(item, self.name) for item in declarations or ()
            ]
        return self._tools

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        handler = self._import_module()._call_tool
        try:
            bound_call = partial(
                contextvars.copy_context().run, handler, tool_name, arguments
            )
            response = await asyncio.get_event_loop().run_in_executor(None, bound_call)
        except Exception as exc:
            logger.debug("in-process tool %s failed: %s", tool_name, exc, exc_info=True)
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, output=response or "")


def format_tool_result(result: ToolResult) -> str:
    if not result.success:
        message = (
            result.agent_error.render()
            if result.agent_error is not None
            else f"Error: {result.error or 'tool failed'}"
        )
        return "\n".join(
            [message, *(f"Hint: {hint}" for hint in result.recovery_hints)]
        )
    suffix = ""
    if result.truncated and result.original_length is not None:
        suffix = (
            f"\n[output truncated — showing part of {result.original_length} "
            "chars; narrow the query or request the specific portion you need]"
        )
    return (result.output or "") + suffix


def read_tool_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    if not isinstance(raw, str):
        return ARGUMENTS_UNREADABLE
    document = raw.strip()
    if document.startswith("```"):
        document = document.partition("\n")[2]
        document = document.rsplit("```", 1)[0].strip()
    for _ in range(2):
        if not isinstance(document, str):
            break
        try:
            document = json.loads(document)
        except (json.JSONDecodeError, ValueError):
            break
        if isinstance(document, dict):
            return document
    return ARGUMENTS_UNREADABLE
