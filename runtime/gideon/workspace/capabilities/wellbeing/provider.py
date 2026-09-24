"""Measurement tools use the same home-scoped ledger as the dashboard."""

import asyncio
import json
from pathlib import Path

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .store import MeasurementError, MeasurementStore


class WellbeingProvider(ToolProvider):
    name = "gideon-wellbeing"
    display_name = "Wellbeing records"

    def __init__(self, home=None):
        self.home = Path(home) if home is not None else config_dir()

    async def list_tools(self):
        return [ToolDefinition(
            name="wellbeing_records", provider=self.name,
            description="List, read, export, enter or correct personal weight and blood pressure records. Corrections preserve provenance and history. Writes require a stable request_id; correct also requires revision.",
            parameters={"type": "object", "required": ["operation"], "additionalProperties": False,
                "properties": {"operation": {"type": "string", "enum": ["list", "get", "history", "export", "create", "correct"]},
                    "id": {"type": "string"}, "payload": {"type": "object", "description": "Create: request_id, kind (body_weight/blood_pressure), observed_at with UTC offset, unit (kg/lb/mmHg), values (weight or systolic/diastolic), source, notes. Correct: request_id, revision, optional observed_at/unit/values/notes. List: optional from_date/to_date/kind/limit/offset."}}},
            requires_approval=True, risk_level=RiskLevel.CAUTION)]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name != "wellbeing_records" or arguments.get("operation") not in ("list", "get", "history", "export", "create", "correct"):
                raise MeasurementError("Unknown wellbeing operation")
            store = MeasurementStore(self.home)
            operation, payload = arguments["operation"], arguments.get("payload", {})
            if operation == "list":
                value = await asyncio.to_thread(store.list, **payload)
            elif operation in ("get", "history"):
                value = await asyncio.to_thread(getattr(store, operation), arguments.get("id"))
            elif operation == "correct":
                value = await asyncio.to_thread(store.correct, arguments.get("id"), payload)
            elif operation == "create":
                value = await asyncio.to_thread(store.create, payload)
            else:
                value = await asyncio.to_thread(store.export)
            return ToolResult(success=True, output=json.dumps(value, allow_nan=False))
        except (MeasurementError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc))


def create_provider(config=None):
    return WellbeingProvider()
