"""Native tools for canonical eye-prescription records."""
import asyncio
import json

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .eyes import EyePrescriptionStore
from .store import MeasurementError


EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
ID = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False}


class EyePrescriptionTools(ToolProvider):
    name = "gideon-wellbeing-eyes"
    display_name = "Eye prescriptions"

    def __init__(self, store=None):
        self.store = store or EyePrescriptionStore(config_dir())

    async def list_tools(self):
        return [
            ToolDefinition(name="wellbeing_eye_prescriptions_list", provider=self.name,
                description="List authored eye prescriptions without interpretation.", parameters=EMPTY,
                requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="wellbeing_eye_prescriptions_get", provider=self.name,
                description="Read one authored eye prescription.", parameters=ID,
                requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="wellbeing_eye_prescriptions_history", provider=self.name,
                description="Read immutable corrections for one eye prescription.", parameters=ID,
                requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="wellbeing_eye_prescriptions_export", provider=self.name,
                description="Export canonical eye prescriptions and correction history.", parameters=EMPTY,
                requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="wellbeing_eye_prescriptions_create", provider=self.name,
                description="Write an explicitly approved authored eye prescription.",
                parameters={"type":"object","properties":{"payload":{"type":"object"}},"required":["payload"],"additionalProperties":False},
                requires_approval=True, risk_level=RiskLevel.CAUTION),
            ToolDefinition(name="wellbeing_eye_prescriptions_correct", provider=self.name,
                description="Append an explicitly approved correction to an eye prescription.",
                parameters={"type":"object","properties":{"id":{"type":"string"},"payload":{"type":"object"}},"required":["id","payload"],"additionalProperties":False},
                requires_approval=True, risk_level=RiskLevel.CAUTION),
        ]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name == "wellbeing_eye_prescriptions_list" and arguments == {}:
                result = self.store.list()
            elif tool_name == "wellbeing_eye_prescriptions_export" and arguments == {}:
                result = self.store.export()
            elif tool_name in {"wellbeing_eye_prescriptions_get", "wellbeing_eye_prescriptions_history"} and isinstance(arguments, dict) and set(arguments) == {"id"}:
                method = self.store.get if tool_name.endswith("_get") else self.store.history
                result = await asyncio.to_thread(method, arguments["id"])
            elif tool_name == "wellbeing_eye_prescriptions_create" and isinstance(arguments, dict) and set(arguments) == {"payload"}:
                result = await asyncio.to_thread(self.store.create, arguments["payload"])
            elif tool_name == "wellbeing_eye_prescriptions_correct" and isinstance(arguments, dict) and set(arguments) == {"id", "payload"}:
                result = await asyncio.to_thread(self.store.correct, arguments["id"], arguments["payload"])
            else:
                return ToolResult(False, error="arguments must match the declared eye prescription operation")
            return ToolResult(True, output=json.dumps(result, allow_nan=False, sort_keys=True))
        except MeasurementError as exc:
            return ToolResult(False, error=str(exc), metadata={"code": exc.code, "status": exc.status})


def create_provider(config=None):
    if config not in (None, {}):
        raise ValueError("Eye prescription tools do not accept caller storage configuration")
    return EyePrescriptionTools()
