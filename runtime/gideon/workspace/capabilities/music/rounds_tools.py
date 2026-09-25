"""Native authored canon and multi-part practice operations."""

import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider

from .catalog import MusicCatalog
from .rounds import RoundStore
from .store import DomainError


class RoundTools(ToolProvider):
    def __init__(self, store=None):
        home = config_dir()
        self.store = store or RoundStore(
            home / "capabilities" / "music",
            MusicCatalog(
                home / "capabilities" / "music",
                NativeArtifactProvider(root=home / "artifacts"),
            ),
        )

    @property
    def name(self):
        return "gideon-music-rounds"

    @property
    def display_name(self):
        return "Musical canons and practice"

    async def list_tools(self):
        tools = []
        for action in ("list", "get", "create", "update", "practice", "history"):
            properties = {}
            if action not in ("list", "create"):
                properties["id"] = {"type": "string"}
            if action in ("create", "update", "practice"):
                properties["data"] = {
                    "type": "object",
                    "description": "Round: title,tempo_bpm,meter_beats,notes,parts[{id,name,entry_beats,notation,catalog_ref}],partner_ids; update adds revision. Practice: request_id,round_revision,part_ids,occurred_at offsetISO,grade0..5,notes.",
                }
            read_only = action in ("list", "get", "history")
            tools.append(
                ToolDefinition(
                    name="music_rounds_" + action,
                    provider=self.name,
                    description=action.capitalize()
                    + " authored musical canons and pinned part practice.",
                    parameters={
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                    requires_approval=not read_only,
                    risk_level=RiskLevel.SAFE if read_only else RiskLevel.CAUTION,
                )
            )
        return tools

    async def invoke(self, tool_name, arguments):
        definitions = {tool.name: tool.parameters for tool in await self.list_tools()}
        if (
            tool_name not in definitions
            or not isinstance(arguments, dict)
            or set(arguments) != set(definitions[tool_name]["required"])
        ):
            return ToolResult(success=False, error="Invalid round tool arguments")
        args = [arguments[key] for key in ("id", "data") if key in arguments]
        try:
            result = getattr(self.store, tool_name.removeprefix("music_rounds_"))(*args)
            return ToolResult(success=True, output=json.dumps(result))
        except (DomainError, TypeError, ValueError) as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                metadata={
                    "code": getattr(exc, "code", "invalid_input"),
                    "status": getattr(exc, "status", 400),
                },
            )


def create_provider(**kwargs):
    return RoundTools()
