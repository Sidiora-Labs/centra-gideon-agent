"""Native read tools for the live platform capability contracts."""
import json
from jsonschema import validate, ValidationError
from gideon.sdk.tool import ToolProvider, ToolDefinition, ToolResult, RiskLevel
from gideon.workspace.capabilities.platform.catalog import current_catalog
from gideon.workspace.capabilities.platform.connections import projection
from gideon.workspace.capabilities.platform.prompt_usage import prompt_usage
from gideon.workspace.capabilities.platform.harnesses import inventory

_SCHEMAS = {
    "platform_harness_inventory": {"type": "object", "properties": {}, "additionalProperties": False},
    "platform_api_catalog": {"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0, "maximum": 100000}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
    "prompt_dependency_usage": {"type": "object", "properties": {"provider": {"type": "string", "maxLength": 100}, "name": {"type": "string", "minLength": 1, "maxLength": 100}}, "required": ["name"], "additionalProperties": False},
    "provider_connections_get": {"type": "object", "properties": {}, "additionalProperties": False},
}
_DESCRIPTIONS = {
    "platform_harness_inventory": "Inspect real managed CLI adapters, installed versions and dependencies.",
    "platform_api_catalog": "Inspect actual registered dashboard routes and declared app events.",
    "prompt_dependency_usage": "Inspect active and declared consumers before removing a saved prompt.",
    "provider_connections_get": "Inspect shared provider connections and bindings without credential values.",
}


class PlatformTools(ToolProvider):
    name = "gideon-platform"
    display_name = "Platform tools"

    async def list_tools(self):
        return [ToolDefinition(name=name, description=_DESCRIPTIONS[name], provider=self.name, parameters=schema, requires_approval=False, risk_level=RiskLevel.SAFE) for name, schema in _SCHEMAS.items()]

    async def invoke(self, tool_name, arguments):
        if tool_name not in _SCHEMAS:
            return ToolResult(success=False, error="Unknown platform tool")
        try:
            validate(arguments, _SCHEMAS[tool_name])
            if tool_name == "platform_api_catalog":
                result = current_catalog(**arguments)
            elif tool_name == "prompt_dependency_usage":
                result = prompt_usage(arguments.get("provider", "native"), arguments["name"])
            elif tool_name == "platform_harness_inventory":
                result = inventory()
            else:
                result = projection()
            return ToolResult(success=True, output=json.dumps(result))
        except ValidationError:
            return ToolResult(success=False, error="Invalid platform tool arguments")
        except (ValueError, RuntimeError):
            return ToolResult(success=False, error="Platform information is unavailable; check the configuration or dashboard")


def create_provider(config=None):
    return PlatformTools()
