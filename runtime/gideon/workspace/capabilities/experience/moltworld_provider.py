import json

from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .moltworld import Moltworld, RemoteError
from .store import Conflict, ExperienceStore, NotFound


class MoltworldTools(ToolProvider):
    name, display_name = "gideon-moltworld", "Moltworld"

    def __init__(self, store=None, service=None):
        self.service = service or Moltworld(store or ExperienceStore())

    async def list_tools(self):
        return [
            ToolDefinition(
                name="experience_moltworld_get",
                provider=self.name,
                description="Read local connection readiness and durable action receipts without contacting Moltworld.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="experience_moltworld_configure",
                provider=self.name,
                description="Enable or disable the adapter using a named canonical credential; the secret is never copied into capability state.",
                parameters={
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "credential_name": {"type": "string"},
                        "revision": {"type": "integer", "minimum": 0},
                    },
                    "required": ["enabled", "credential_name", "revision"],
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="experience_moltworld_status",
                provider=self.name,
                description="Read the authenticated agent state from the current Moltworld v1 protocol.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="experience_moltworld_observe",
                provider=self.name,
                description="Read a bounded public Moltworld area.",
                parameters={
                    "type": "object",
                    "properties": {
                        key: {"type": "integer", "minimum": 0, "maximum": 99}
                        for key in ("x1", "y1", "x2", "y2")
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="experience_moltworld_action",
                provider=self.name,
                description="Queue one explicitly user-approved v1 world action; queued is not completed and timeouts are not retried.",
                parameters={
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                        "action": {"type": "string"},
                        "params": {"type": "object"},
                        "approval": {
                            "type": "object",
                            "description": "Must equal {approved:true,source:user}",
                        },
                    },
                    "required": ["request_id", "action", "params", "approval"],
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=RiskLevel.DESTRUCTIVE,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name == "experience_moltworld_get" and arguments == {}:
                result = {
                    "readiness": self.service.readiness(),
                    "history": self.service.history(),
                }
            elif tool_name == "experience_moltworld_configure" and isinstance(
                arguments, dict
            ):
                result = {"config": self.service.configure(arguments)}
            elif tool_name == "experience_moltworld_status" and arguments == {}:
                result = await self.service.status()
            elif tool_name == "experience_moltworld_observe" and isinstance(
                arguments, dict
            ):
                result = await self.service.observe(arguments)
            elif tool_name == "experience_moltworld_action" and isinstance(
                arguments, dict
            ):
                result = {"receipt": await self.service.action(arguments)}
            else:
                return ToolResult(
                    False, error="arguments must match the declared Moltworld operation"
                )
            return ToolResult(True, output=json.dumps(result))
        except (Conflict, NotFound, RemoteError, ValueError) as exc:
            return ToolResult(
                False, error=str(exc), metadata={"kind": type(exc).__name__}
            )


def create_provider(config=None):
    return MoltworldTools()
