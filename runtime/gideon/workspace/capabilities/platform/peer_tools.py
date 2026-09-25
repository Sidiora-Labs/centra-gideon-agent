import json
from jsonschema import ValidationError, validate
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.capabilities.platform.peers import PeerError, PeerStore

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class PeerTools(ToolProvider):
    name = "gideon-peers"
    display_name = "Peer identity tools"

    async def list_tools(self):
        return [ToolDefinition(name="platform_peer_projection", description="Read public peer identities and directional category policy without private keys or proof nonces.", provider=self.name, parameters=SCHEMA, requires_approval=False, risk_level=RiskLevel.SAFE)]

    async def invoke(self, tool_name, arguments):
        if tool_name != "platform_peer_projection":
            return ToolResult(success=False, error="Unknown peer tool")
        try:
            validate(arguments, SCHEMA)
            return ToolResult(success=True, output=json.dumps(PeerStore().snapshot()))
        except (ValidationError, PeerError, OSError):
            return ToolResult(success=False, error="Peer projection unavailable")


def create_provider(config=None):
    return PeerTools()
