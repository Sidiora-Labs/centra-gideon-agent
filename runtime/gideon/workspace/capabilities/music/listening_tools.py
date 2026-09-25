"""Native listening evidence and read-only remote Spotify ingestion operations."""

import inspect
import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider

from .listening import ListeningStore
from .spotify import SpotifyBridge
from .store import DomainError


class ListeningTools(ToolProvider):
    def __init__(self, store=None, bridge=None):
        home = config_dir()
        self.store = store or ListeningStore(
            home, NativeArtifactProvider(root=home / "artifacts")
        )
        self.bridge = bridge or SpotifyBridge(home, self.store)

    @property
    def name(self):
        return "gideon-music-listening"

    @property
    def display_name(self):
        return "Listening history and playlists"

    async def list_tools(self):
        result = []
        for action in (
            "import",
            "history",
            "playlists",
            "playlist",
            "imports",
            "stats",
            "spotify_config",
            "spotify_configure",
            "spotify_readiness",
            "spotify_sync",
        ):
            props = (
                {"id": {"type": "string"}}
                if action == "playlist"
                else (
                    {"data": {"type": "object"}}
                    if action
                    in ("import", "history", "spotify_configure", "spotify_sync")
                    else {}
                )
            )
            readonly = action not in ("import", "spotify_configure", "spotify_sync")
            result.append(
                ToolDefinition(
                    name="music_listening_" + action,
                    provider=self.name,
                    description=action.replace("_", " ")
                    + " actual listening evidence.",
                    parameters={
                        "type": "object",
                        "properties": props,
                        "required": list(props),
                        "additionalProperties": False,
                    },
                    requires_approval=not readonly,
                    risk_level=RiskLevel.SAFE if readonly else RiskLevel.CAUTION,
                )
            )
        return result

    async def invoke(self, tool_name, arguments):
        definitions = {tool.name: tool.parameters for tool in await self.list_tools()}
        if (
            tool_name not in definitions
            or not isinstance(arguments, dict)
            or set(arguments) != set(definitions[tool_name]["required"])
        ):
            return ToolResult(success=False, error="Invalid listening tool arguments")
        action = tool_name.removeprefix("music_listening_")
        target = self.bridge if action.startswith("spotify_") else self.store
        action = action.removeprefix("spotify_")
        action = "import_data" if action == "import" else action
        try:
            result = getattr(target, action)(
                *[arguments[key] for key in ("id", "data") if key in arguments]
            )
            if inspect.isawaitable(result):
                result = await result
            return ToolResult(success=True, output=json.dumps(result))
        except (DomainError, ValueError, TypeError) as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                metadata={
                    "code": getattr(exc, "code", "invalid_input"),
                    "status": getattr(exc, "status", 400),
                },
            )


def create_provider(**kwargs):
    return ListeningTools()
