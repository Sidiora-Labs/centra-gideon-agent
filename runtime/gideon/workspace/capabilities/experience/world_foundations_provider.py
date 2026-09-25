import json

from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .store import Conflict, ExperienceStore, NotFound
from .world_foundations import WorldFoundations


class WorldFoundationTools(ToolProvider):
    name = "gideon-world-foundations"
    display_name = "World foundations"

    def __init__(self, store=None):
        self.service = WorldFoundations(store or ExperienceStore())

    async def list_tools(self):
        fields = {"foundation_id": {"type": "string"}, "world": {"type": "string"}}
        return [
            ToolDefinition(
                name="experience_foundations_get",
                provider=self.name,
                description="Read durable foundation and controller lifecycle state.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="experience_controller_install",
                provider=self.name,
                description="Install the built-in controller into an existing canonical world.",
                parameters={
                    "type": "object",
                    "properties": fields,
                    "required": list(fields),
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="experience_controller_control",
                provider=self.name,
                description="Arm, restart, stop or retire a durable controller against the managed world engine.",
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": ["arm", "restart", "stop", "retire"],
                        },
                        "revision": {"type": "integer", "minimum": 1},
                    },
                    "required": ["id", "operation", "revision"],
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name == "experience_foundations_get" and arguments == {}:
                result = self.service.list()
            elif (
                tool_name == "experience_controller_install"
                and isinstance(arguments, dict)
                and set(arguments) == {"foundation_id", "world"}
            ):
                result = {
                    "controller": await self.service.install_controller(arguments)
                }
            elif (
                tool_name == "experience_controller_control"
                and isinstance(arguments, dict)
                and set(arguments) == {"id", "operation", "revision"}
            ):
                result = {
                    "controller": await self.service.control(
                        arguments["id"], arguments["operation"], arguments["revision"]
                    )
                }
            else:
                return ToolResult(
                    False,
                    error="arguments must match the declared world foundation operation",
                )
            return ToolResult(True, output=json.dumps(result))
        except (Conflict, NotFound, ValueError) as exc:
            return ToolResult(
                False, error=str(exc), metadata={"kind": type(exc).__name__}
            )


def create_provider(config=None):
    return WorldFoundationTools()
