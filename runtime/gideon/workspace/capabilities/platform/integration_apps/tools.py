"""Native approval-gated tools for persistent integration application receipts."""
import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.capabilities.music.store import DomainError
from .contracts import MUTATIONS
from .store import IntegrationApps


class IntegrationAppTools(ToolProvider):
    def __init__(self, store=None):
        self.store = store or IntegrationApps(config_dir())

    @property
    def name(self):
        return "gideon-integration-apps"

    @property
    def display_name(self):
        return "Jira, Datadog and GitHub"

    async def list_tools(self):
        object_schema = {"type": "object"}
        return [
            ToolDefinition(name="integration_apps_overview", provider=self.name, description="Read configured integration names and durable action receipts without secrets.", parameters={"type": "object", "properties": {}, "additionalProperties": False}, requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="integration_apps_prepare", provider=self.name, description="Persist an exact reviewed API request without contacting the provider.", parameters={"type": "object", "properties": {"connection_id": {"type": "string"}, "request_id": {"type": "string"}, "operation": {"type": "string"}, "input": object_schema}, "required": ["connection_id", "request_id", "operation", "input"], "additionalProperties": False}, requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="integration_apps_execute", provider=self.name, description="Execute one previously reviewed API request and retain its remote receipt.", parameters={"type": "object", "properties": {"run_id": {"type": "string"}, "revision": {"type": "integer"}}, "required": ["run_id", "revision"], "additionalProperties": False}, requires_approval=True, risk_level=RiskLevel.CAUTION),
        ]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name == "integration_apps_overview" and arguments == {}:
                result = {"connections": self.store.connections(), "runs": self.store.runs()}
            elif tool_name == "integration_apps_prepare" and isinstance(arguments, dict) and set(arguments) == {"connection_id", "request_id", "operation", "input"}:
                result = self.store.prepare(arguments["connection_id"], {key: arguments[key] for key in ("request_id", "operation", "input")})
            elif tool_name == "integration_apps_execute" and isinstance(arguments, dict) and set(arguments) == {"run_id", "revision"}:
                result = await self.store.execute(arguments["run_id"], {"revision": arguments["revision"], "confirm": True})
            else:
                raise DomainError("Invalid integration tool arguments")
            return ToolResult(success=True, output=json.dumps(result))
        except DomainError as error:
            return ToolResult(success=False, error=str(error), metadata={"code": error.code, "status": error.status})


def create_provider(**kwargs):
    return IntegrationAppTools()
