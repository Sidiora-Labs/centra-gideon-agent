import json

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .lifestyle_profile import LifestyleProfileStore
from .store import MeasurementError


class LifestyleProfileProvider(ToolProvider):
    name = "gideon-lifestyle-profile"
    display_name = "Lifestyle profile observations"

    def __init__(self, store=None):
        self.store = store or LifestyleProfileStore(config_dir())

    async def list_tools(self):
        common = {"id":{"type":"string"}}
        definitions = {
            "create": ({"payload":{"type":"object"}}, ["payload"], True),
            "correct": ({**common,"payload":{"type":"object"}}, ["id","payload"], True),
            "get": (common, ["id"], False), "history": (common, ["id"], False),
            "list": ({}, [], False), "export": ({}, [], False),
        }
        descriptions = {
            "create":"Record one authored lifestyle profile observation without diagnosis.",
            "correct":"Correct a lifestyle observation at its exact revision while retaining history.",
            "get":"Read one canonical lifestyle observation.", "history":"Read immutable lifestyle correction history.",
            "list":"List current lifestyle observations.", "export":"Export canonical lifestyle observations and history.",
        }
        return [ToolDefinition(name="lifestyle_profile_"+name, provider=self.name, description=descriptions[name],
            parameters={"type":"object","properties":properties,"required":required,"additionalProperties":False},
            requires_approval=approval, risk_level=RiskLevel.CAUTION if approval else RiskLevel.SAFE)
            for name,(properties,required,approval) in definitions.items()]

    async def invoke(self, tool_name, arguments):
        name = tool_name.removeprefix("lifestyle_profile_")
        if name not in {"create","correct","get","history","list","export"} or not isinstance(arguments, dict):
            return ToolResult(False, error="tool and arguments must match a declared lifestyle-profile operation")
        try:
            if name == "create": value = self.store.create(arguments.get("payload"))
            elif name == "correct": value = self.store.correct(arguments.get("id"), arguments.get("payload"))
            elif name in ("get","history"): value = getattr(self.store, name)(arguments.get("id"))
            else: value = getattr(self.store, name)()
            return ToolResult(True, output=json.dumps(value, sort_keys=True, allow_nan=False))
        except MeasurementError as exc:
            return ToolResult(False, error=str(exc), metadata={"code":exc.code,"status":exc.status})


def create_provider(config=None):
    if config: raise ValueError("Lifestyle-profile provider accepts no caller storage configuration")
    return LifestyleProfileProvider()

