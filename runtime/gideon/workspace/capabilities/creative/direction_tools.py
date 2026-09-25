"""Approval-aware native tools for creative direction plans."""

import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .direction import DirectionStore
from .store import CatalogError


class DirectionTools(ToolProvider):
    def __init__(self, store=None):
        self.store = store or DirectionStore(config_dir())

    @property
    def name(self):
        return "gideon-creative-direction"

    @property
    def display_name(self):
        return "Creative direction and production plans"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="creative_direction_projects",
                provider=self.name,
                description="List persisted creative direction projects and production plan state.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="creative_direction_action",
                provider=self.name,
                description="Create or control a reviewed production plan or execute its next deterministic step.",
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "id": {"type": "string"},
                        "payload": {"type": "object"},
                    },
                    "required": ["action", "payload"],
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, name, arguments):
        try:
            if name == "creative_direction_projects" and arguments == {}:
                result = {"items": self.store.list()}
            elif name == "creative_direction_action" and isinstance(arguments, dict):
                action = arguments.get("action")
                payload = arguments.get("payload")
                identity = arguments.get("id")
                result = (
                    self.store.create(payload)
                    if action == "create"
                    else (
                        self.store.control(identity, payload)
                        if action == "control"
                        else (
                            self.store.replace_plan(identity, payload)
                            if action == "plan"
                            else (
                                self.store.advance(identity, payload)
                                if action == "advance"
                                else (_ for _ in ()).throw(
                                    CatalogError("Invalid direction action")
                                )
                            )
                        )
                    )
                )
            else:
                raise CatalogError("Invalid creative direction tool arguments")
            return ToolResult(success=True, output=json.dumps(result))
        except CatalogError as error:
            return ToolResult(
                success=False, error=str(error), metadata={"status": error.status}
            )


def create_provider(config=None):
    if config not in (None, {}):
        raise ValueError("Creative direction provider does not accept configuration")
    return DirectionTools()
