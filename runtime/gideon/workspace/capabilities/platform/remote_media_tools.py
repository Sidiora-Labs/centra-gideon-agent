import json

from jsonschema import ValidationError, validate

from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.capabilities.platform.peers import PeerError
from gideon.workspace.capabilities.platform.remote_media import RemoteMediaError, create_remote_media


class RemoteMediaTools(ToolProvider):
    name = "gideon-remote-media"
    display_name = "Remote media execution"

    def __init__(self, service=None):
        self.service = service or create_remote_media()

    async def list_tools(self):
        return [
            ToolDefinition(name="remote_media_list", description="List durable remote media executions and eligible direct peers.", provider=self.name, parameters={"type": "object", "properties": {}, "additionalProperties": False}, requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="remote_media_dispatch", description="Submit a bounded image generation request to an authorized direct peer.", provider=self.name, parameters={"type": "object", "required": ["peer_id", "request_id", "prompt"], "properties": {"peer_id": {"type": "string"}, "request_id": {"type": "string"}, "prompt": {"type": "string"}, "size": {"type": "string"}}, "additionalProperties": False}, requires_approval=True, risk_level=RiskLevel.CAUTION),
            ToolDefinition(name="remote_media_cancel", description="Cancel an admitted remote media job at its current revision.", provider=self.name, parameters={"type": "object", "required": ["execution_id", "state_revision"], "properties": {"execution_id": {"type": "string"}, "state_revision": {"type": "integer", "minimum": 1}}, "additionalProperties": False}, requires_approval=True, risk_level=RiskLevel.CAUTION),
        ]

    async def invoke(self, tool_name, arguments):
        definitions = {tool.name: tool for tool in await self.list_tools()}
        if tool_name not in definitions:
            return ToolResult(success=False, error="Unknown remote media tool")
        try:
            validate(arguments, definitions[tool_name].parameters)
            if tool_name == "remote_media_list":
                result = self.service.list()
            elif tool_name == "remote_media_dispatch":
                value = {"prompt": arguments["prompt"]}
                if arguments.get("size"):
                    value["size"] = arguments["size"]
                result = await self.service.dispatch(arguments["peer_id"], {"request_id": arguments["request_id"], "operation": "image_generate", "input": value})
            else:
                result = await self.service.cancel(arguments["execution_id"], arguments["state_revision"])
            return ToolResult(success=True, output=json.dumps(result))
        except (ValidationError, RemoteMediaError, PeerError, OSError) as error:
            return ToolResult(success=False, error=str(error))


def create_provider(config=None):
    return RemoteMediaTools()
