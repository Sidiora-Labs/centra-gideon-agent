import json

from jsonschema import ValidationError, validate

from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.capabilities.platform.replication import ReplicationService

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class ReplicationTools(ToolProvider):
    name = "gideon-replication"
    display_name = "Domain replication"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="platform_replication_status",
                description="Read direct-peer replication coverage, cursors, and conflicts without peer secrets or domain payloads.",
                provider=self.name,
                parameters=SCHEMA,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        if tool_name != "platform_replication_status":
            return ToolResult(success=False, error="Unknown replication tool")
        try:
            validate(arguments, SCHEMA)
            return ToolResult(
                success=True, output=json.dumps(ReplicationService().status())
            )
        except (ValidationError, ValueError, OSError):
            return ToolResult(success=False, error="Replication status unavailable")


def create_provider(config=None):
    return ReplicationTools()
