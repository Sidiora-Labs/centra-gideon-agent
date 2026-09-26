"""Credential-free model tools for configured MCP resources and prompts."""

from __future__ import annotations

import json
from typing import Any

from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult


class McpDelegatedToolProvider(ToolProvider):
    def __init__(self, session_key: str) -> None:
        self.session_key = session_key

    @property
    def name(self) -> str:
        return "gideon-mcp-delegated"

    @property
    def display_name(self) -> str:
        return "MCP Resources and Prompts"

    async def list_tools(self) -> list[ToolDefinition]:
        from gideon.integrations.mcp_client import get_mcp_client_registry

        registry = get_mcp_client_registry()
        if registry is None or not registry._specs:
            return []
        common = {"server": {"type": "string", "description": "Configured MCP server name"}}
        return [
            ToolDefinition("mcp_resources_list", "List resources from a configured MCP server.", self.name, {"type": "object", "properties": {**common, "cursor": {"type": "string"}}, "required": ["server"]}, True, RiskLevel.CAUTION),
            ToolDefinition("mcp_resource_read", "Read one MCP resource by URI.", self.name, {"type": "object", "properties": {**common, "uri": {"type": "string"}}, "required": ["server", "uri"]}, True, RiskLevel.CAUTION),
            ToolDefinition("mcp_prompts_list", "List prompts from a configured MCP server.", self.name, {"type": "object", "properties": {**common, "cursor": {"type": "string"}}, "required": ["server"]}, True, RiskLevel.CAUTION),
            ToolDefinition("mcp_prompt_get", "Get an MCP prompt with string arguments.", self.name, {"type": "object", "properties": {**common, "name": {"type": "string"}, "arguments": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["server", "name"]}, True, RiskLevel.CAUTION),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        from gideon.integrations.mcp_client import get_mcp_client_registry

        registry = get_mcp_client_registry()
        name = arguments.get("server")
        if registry is None or not isinstance(name, str):
            return ToolResult(False, error="MCP server is unavailable")
        conn = registry.get(name, self.session_key)
        if conn is None:
            return ToolResult(False, error="MCP server is not configured")
        mapping = {
            "mcp_resources_list": ("resources/list", {"cursor": arguments.get("cursor")}),
            "mcp_resource_read": ("resources/read", {"uri": arguments.get("uri")}),
            "mcp_prompts_list": ("prompts/list", {"cursor": arguments.get("cursor")}),
            "mcp_prompt_get": ("prompts/get", {"name": arguments.get("name"), "arguments": arguments.get("arguments") or {}}),
        }
        if tool_name not in mapping:
            return ToolResult(False, error="unknown MCP operation")
        method, params = mapping[tool_name]
        if method in ("resources/read", "prompts/get") and not isinstance(params.get("uri") or params.get("name"), str):
            return ToolResult(False, error="resource URI or prompt name is required")
        if method == "prompts/get" and (not isinstance(params["arguments"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in params["arguments"].items())):
            return ToolResult(False, error="prompt arguments must be strings")
        try:
            result = await conn.protocol_call(method, **params)
            output = json.dumps(result, ensure_ascii=False)
            capped = len(output) > 40000
            return ToolResult(True, output=output[:40000], truncated=capped, original_length=len(output) if capped else None)
        except Exception as exc:
            return ToolResult(False, error=str(exc)[:300])
