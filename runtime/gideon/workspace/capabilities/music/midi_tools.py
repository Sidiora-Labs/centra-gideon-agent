"""Native monophonic transcription and MIDI note operations."""

import asyncio
import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider

from .catalog import MusicCatalog
from .midi import MidiStore
from .store import DomainError


class MidiTools(ToolProvider):
    def __init__(self, store=None):
        home = config_dir()
        self.store = store or MidiStore(
            home / "capabilities" / "music",
            MusicCatalog(
                home / "capabilities" / "music",
                NativeArtifactProvider(root=home / "artifacts"),
            ),
        )

    @property
    def name(self):
        return "gideon-music-midi"

    @property
    def display_name(self):
        return "Monophonic transcription and MIDI"

    async def list_tools(self):
        tools = []
        for action in ("list", "get", "transcribe", "update", "export"):
            properties = {}
            if action not in ("list", "transcribe"):
                properties["id"] = {"type": "string"}
            if action in ("transcribe", "update", "export"):
                properties["data"] = {
                    "type": "object",
                    "description": "Transcribe: request_id,track_id,render_id,title,tempo_bpm. Update: revision,title,notes[{id,pitch,start_seconds,duration_seconds,velocity}]. Export: revision.",
                }
            read_only = action in ("list", "get")
            tools.append(
                ToolDefinition(
                    name="music_midi_" + action,
                    provider=self.name,
                    description=action.capitalize()
                    + " monophonic PCM transcription and MIDI notes.",
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
            return ToolResult(success=False, error="Invalid MIDI tool arguments")
        args = [arguments[key] for key in ("id", "data") if key in arguments]
        try:
            result = await asyncio.to_thread(
                getattr(self.store, tool_name.removeprefix("music_midi_")), *args
            )
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
    return MidiTools()
