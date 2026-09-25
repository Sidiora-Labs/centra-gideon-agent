"""Native music generation controls; secrets resolve from canonical credentials."""

import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider

from .catalog import MusicCatalog
from .generation import MusicGeneration
from .store import DomainError


class MusicGenerationTools(ToolProvider):
    def __init__(self, service=None):
        home = config_dir()
        self.service = service or MusicGeneration(
            home,
            MusicCatalog(
                home / "capabilities" / "music",
                NativeArtifactProvider(root=home / "artifacts"),
            ),
        )

    @property
    def name(self):
        return "gideon-music-generation"

    @property
    def display_name(self):
        return "Music generation"

    async def list_tools(self):
        definitions = []
        for operation in ("readiness", "configure", "submit", "get", "list", "cancel"):
            field = (
                "data"
                if operation in ("configure", "submit")
                else "id" if operation in ("get", "cancel") else None
            )
            properties = (
                {field: {"type": "object" if field == "data" else "string"}}
                if field
                else {}
            )
            definitions.append(
                ToolDefinition(
                    name="music_generation_" + operation,
                    provider=self.name,
                    description={
                        "configure": "Set enabled,model,credential_name,revision; credentials remain in the canonical store.",
                        "submit": "Compose using request_id,track_id,track_revision,prompt,music_length_ms (null or3000..600000),force_instrumental,license. Explicit paid provider request; never automatically retry.",
                        "readiness": "Read configuration and local credential availability; remote status remains unverified.",
                        "get": "Read a persisted generation job by id.",
                        "list": "List the first50 generation job receipts.",
                        "cancel": "Cancel local work; remote billing/completion may remain unknown.",
                    }[operation],
                    parameters={
                        "type": "object",
                        "properties": properties,
                        "required": [field] if field else [],
                        "additionalProperties": False,
                    },
                    requires_approval=operation in ("configure", "submit", "cancel"),
                    risk_level=(
                        RiskLevel.CAUTION
                        if operation in ("configure", "submit", "cancel")
                        else RiskLevel.SAFE
                    ),
                )
            )
        return definitions

    async def invoke(self, tool_name, arguments):
        schemas = {tool.name: tool.parameters for tool in await self.list_tools()}
        if (
            tool_name not in schemas
            or not isinstance(arguments, dict)
            or set(arguments) != set(schemas[tool_name]["required"])
        ):
            return ToolResult(success=False, error="Invalid generation tool arguments")
        operation = tool_name.removeprefix("music_generation_")
        try:
            result = getattr(self.service, operation)(*arguments.values())
            if operation in ("submit", "cancel"):
                result = await result
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
    return MusicGenerationTools()
