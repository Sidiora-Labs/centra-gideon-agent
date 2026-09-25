"""Native tools for the remote-session bridge."""

import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .remote_sessions import RemoteSessionBridge


class RemoteSessionTools(ToolProvider):
    name = "remote-agent-sessions"
    display_name = "Remote agent sessions"

    async def list_tools(self):
        common = {"connection_id": {"type": "string"}}
        return [
            ToolDefinition(
                name="remote_agent_sessions",
                description="List actual sessions from one configured remote agent runtime.",
                provider=self.name,
                parameters={
                    "type": "object",
                    "properties": common,
                    "required": ["connection_id"],
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="remote_agent_history",
                description="Read remote session history with remote provenance retained.",
                provider=self.name,
                parameters={
                    "type": "object",
                    "properties": common
                    | {"session_id": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["connection_id", "session_id"],
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="remote_agent_message",
                description="Send an approved message to an existing remote agent session and consume its SSE reply.",
                provider=self.name,
                parameters={
                    "type": "object",
                    "properties": common
                    | {"session_id": {"type": "string"}, "message": {"type": "string"}},
                    "required": ["connection_id", "session_id", "message"],
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=RiskLevel.DESTRUCTIVE,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        definitions = {row.name: row for row in await self.list_tools()}
        definition = definitions.get(tool_name)
        try:
            if definition is None or not isinstance(arguments, dict):
                raise ValueError("Unknown remote session tool")
            schema = definition.parameters
            if set(arguments) - set(schema["properties"]) or set(
                schema["required"]
            ) - set(arguments):
                raise ValueError("Invalid remote session arguments")
            bridge = RemoteSessionBridge(config_dir())
            if tool_name == "remote_agent_sessions":
                result = await bridge.sessions(arguments["connection_id"])
            elif tool_name == "remote_agent_history":
                result = await bridge.history(
                    arguments["connection_id"],
                    arguments["session_id"],
                    arguments.get("limit", 50),
                )
            else:
                chunks = bytearray()
                async for chunk in bridge.stream(
                    arguments["connection_id"],
                    arguments["session_id"],
                    {"message": arguments["message"]},
                ):
                    chunks.extend(chunk)
                    if len(chunks) > 1_000_000:
                        raise ValueError("Remote reply exceeds native tool limit")
                result = {
                    "connection_id": arguments["connection_id"],
                    "session_id": arguments["session_id"],
                    "stream": chunks.decode("utf-8", "replace"),
                }
            return ToolResult(success=True, output=json.dumps(result, sort_keys=True))
        except Exception as error:
            return ToolResult(success=False, error=str(error))


def create_provider(config=None):
    return RemoteSessionTools()
