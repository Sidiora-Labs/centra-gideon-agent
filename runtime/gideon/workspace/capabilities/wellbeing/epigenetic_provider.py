import json

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

from .epigenetic import EpigeneticStore
from .store import MeasurementError


class EpigeneticProvider(ToolProvider):
    name = "gideon-wellbeing-epigenetic"
    display_name = "Source-reported epigenetic results"

    def __init__(self, home=None):
        self.home = home if home is not None else config_dir()

    async def list_tools(self):
        empty = {"type": "object", "properties": {}, "additionalProperties": False}
        identity = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        }
        create = {
            "type": "object",
            "properties": {"payload": {"type": "object"}},
            "required": ["payload"],
            "additionalProperties": False,
        }
        correct = {
            "type": "object",
            "properties": {"id": {"type": "string"}, "payload": {"type": "object"}},
            "required": ["id", "payload"],
            "additionalProperties": False,
        }
        specs = [
            ("list", empty, False),
            ("get", identity, False),
            ("history", identity, False),
            ("export", empty, False),
            ("create", create, True),
            ("correct", correct, True),
        ]
        return [
            ToolDefinition(
                name="wellbeing_epigenetic_" + name,
                provider=self.name,
                description=f"{name.capitalize()} source-reported epigenetic records without diagnostic interpretation.",
                parameters=schema,
                requires_approval=write,
                risk_level=RiskLevel.CAUTION if write else RiskLevel.SAFE,
            )
            for name, schema, write in specs
        ]

    async def invoke(self, tool_name, arguments):
        operation = tool_name.removeprefix("wellbeing_epigenetic_")
        try:
            store = EpigeneticStore(self.home)
            if operation == "list":
                value = store.list()
            elif operation == "export":
                value = store.export()
            elif operation in ("get", "history"):
                value = getattr(store, operation)(arguments["id"])
            elif operation == "create":
                value = store.create(arguments["payload"])
            elif operation == "correct":
                value = store.correct(arguments["id"], arguments["payload"])
            else:
                raise MeasurementError("Unknown epigenetic operation", 404, "not_found")
            return ToolResult(True, output=json.dumps(value, allow_nan=False))
        except (MeasurementError, KeyError, TypeError) as exc:
            return ToolResult(
                False,
                error=str(exc),
                metadata={
                    "status": getattr(exc, "status", 400),
                    "code": getattr(exc, "code", "invalid_request"),
                },
            )


def create_provider(config=None):
    if config not in (None, {}):
        raise ValueError(
            "Epigenetic provider does not accept caller storage configuration"
        )
    return EpigeneticProvider()
