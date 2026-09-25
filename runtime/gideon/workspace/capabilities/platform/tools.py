"""Native read tools for the live platform capability contracts."""
import json
import sqlite3
from jsonschema import validate, ValidationError
from gideon.sdk.tool import ToolProvider, ToolDefinition, ToolResult, RiskLevel
from gideon.workspace.capabilities.platform.catalog import current_catalog
from gideon.workspace.capabilities.platform.connections import projection
from gideon.workspace.capabilities.platform.prompt_usage import prompt_usage
from gideon.workspace.capabilities.platform.harnesses import inventory
from gideon.workspace.capabilities.platform.comparisons import view as comparison_view
from gideon.workspace.capabilities.platform.references import view as references_view
from gideon.workspace.capabilities.platform.ownership import view as ownership_view, mutate as ownership_mutate
from gideon.integrations.mcp_core import get_current_session_key

_SCHEMAS = {
    "platform_feature_ownership": {"type": "object", "properties": {}, "additionalProperties": False},
    "platform_feature_claim": {"type": "object", "properties": {"project_id": {"type": "string"}, "feature": {"type": "string"}, "action": {"enum": ["claim", "release"]}, "revision": {"type": "integer", "minimum": 0}, "request_id": {"type": "string"}}, "required": ["project_id", "feature", "action", "revision", "request_id"], "additionalProperties": False},
    "platform_reference_repositories": {"type": "object", "properties": {}, "additionalProperties": False},
    "platform_model_comparisons": {"type": "object", "properties": {"run_id": {"type": "string", "maxLength": 200}}, "additionalProperties": False},
    "platform_harness_inventory": {"type": "object", "properties": {}, "additionalProperties": False},
    "platform_api_catalog": {"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0, "maximum": 100000}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
    "prompt_dependency_usage": {"type": "object", "properties": {"provider": {"type": "string", "maxLength": 100}, "name": {"type": "string", "minLength": 1, "maxLength": 100}}, "required": ["name"], "additionalProperties": False},
    "provider_connections_get": {"type": "object", "properties": {}, "additionalProperties": False},
}
_DESCRIPTIONS = {
    "platform_feature_ownership": "Read durable project feature owners and revisions.",
    "platform_feature_claim": "Claim or release a project feature as this authenticated agent session.",
    "platform_reference_repositories": "Read reference repository snapshots and reviewed commit cursors without fetching.",
    "platform_model_comparisons": "Read attributed comparison observations and recorded judge benchmark tables.",
    "platform_harness_inventory": "Inspect real managed CLI adapters, installed versions and dependencies.",
    "platform_api_catalog": "Inspect actual registered dashboard routes and declared app events.",
    "prompt_dependency_usage": "Inspect active and declared consumers before removing a saved prompt.",
    "provider_connections_get": "Inspect shared provider connections and bindings without credential values.",
}


class PlatformTools(ToolProvider):
    name = "gideon-platform"
    display_name = "Platform tools"

    async def list_tools(self):
        return [ToolDefinition(name=name, description=_DESCRIPTIONS[name], provider=self.name, parameters=schema, requires_approval=name == "platform_feature_claim", risk_level=RiskLevel.CAUTION if name == "platform_feature_claim" else RiskLevel.SAFE) for name, schema in _SCHEMAS.items()]

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
            elif tool_name == "platform_model_comparisons":
                result = comparison_view(arguments.get("run_id"))
            elif tool_name == "platform_reference_repositories":
                result = references_view()
            elif tool_name == "platform_feature_ownership":
                result = ownership_view()
            elif tool_name == "platform_feature_claim":
                session = get_current_session_key()
                result = ownership_mutate(arguments, "session:" + session if session else "")
            else:
                result = projection()
            return ToolResult(success=True, output=json.dumps(result))
        except ValidationError:
            return ToolResult(success=False, error="Invalid platform tool arguments")
        except (ValueError, RuntimeError, sqlite3.Error):
            return ToolResult(success=False, error="Platform information is unavailable; check the configuration or dashboard")


def create_provider(config=None):
    return PlatformTools()
