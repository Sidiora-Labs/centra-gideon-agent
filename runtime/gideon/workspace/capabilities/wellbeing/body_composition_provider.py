"""Approval-aware native tools for body-composition records."""
import asyncio
import json
from pathlib import Path

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .body_composition import BodyCompositionStore
from .store import MeasurementError


READ_SCHEMA = {
    "type": "object", "required": ["operation"], "additionalProperties": False,
    "properties": {
        "operation": {"type": "string", "enum": ["list", "get", "history", "export"]},
        "id": {"type": "string"}, "payload": {"type": "object"},
    },
}
WRITE_SCHEMA = {
    "type": "object", "required": ["operation", "payload"], "additionalProperties": False,
    "properties": {
        "operation": {"type": "string", "enum": ["create", "correct"]},
        "id": {"type": "string"}, "payload": {"type": "object"},
    },
}


class BodyCompositionProvider(ToolProvider):
    name = "gideon-body-composition"
    display_name = "Body composition"

    def __init__(self, home=None):
        self.home = Path(home) if home is not None else config_dir()

    async def list_tools(self):
        return [
            ToolDefinition(name="body_composition_read", provider=self.name, description="Read authored body-composition observations, immutable history, or canonical export without medical interpretation.", parameters=READ_SCHEMA, requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="body_composition_write", provider=self.name, description="Create or correct an authored body-composition observation. Explicit owner approval is required.", parameters=WRITE_SCHEMA, requires_approval=True, risk_level=RiskLevel.CAUTION),
        ]

    async def invoke(self, tool_name, arguments):
        try:
            store = BodyCompositionStore(self.home)
            operation, identity, payload = arguments.get("operation"), arguments.get("id"), arguments.get("payload", {})
            if tool_name == "body_composition_read" and operation == "list":
                result = await asyncio.to_thread(store.list, **payload)
            elif tool_name == "body_composition_read" and operation in {"get", "history"}:
                result = await asyncio.to_thread(getattr(store, operation), identity)
            elif tool_name == "body_composition_read" and operation == "export":
                result = await asyncio.to_thread(store.export)
            elif tool_name == "body_composition_write" and operation == "create":
                result = await asyncio.to_thread(store.create, payload)
            elif tool_name == "body_composition_write" and operation == "correct":
                result = await asyncio.to_thread(store.correct, identity, payload)
            else:
                raise MeasurementError("Unknown body-composition native operation")
            return ToolResult(success=True, output=json.dumps(result, allow_nan=False))
        except (MeasurementError, TypeError, ValueError) as error:
            return ToolResult(success=False, error=str(error))


def create_provider(config=None):
    return BodyCompositionProvider()
