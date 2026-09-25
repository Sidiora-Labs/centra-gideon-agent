"""Native agent access to the same repertoire and catalog stores as the console."""

import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider

from .catalog import MusicCatalog
from .store import DomainError, RepertoireStore


class MusicToolProvider(ToolProvider):
    def __init__(self, repertoire=None, catalog=None):
        home = config_dir()
        artifacts = NativeArtifactProvider(root=home / "artifacts")
        self.repertoire = repertoire or RepertoireStore(
            home / "capabilities" / "music", artifacts
        )
        self.catalog = catalog or MusicCatalog(
            home / "capabilities" / "music", artifacts
        )

    @property
    def name(self):
        return "gideon-music-tools"

    @property
    def display_name(self):
        return "Music repertoire and catalog"

    async def list_tools(self):
        tools = []
        for domain, actions in (
            ("repertoire", ("list", "get", "create", "update", "practice")),
            ("catalog", ("list", "get", "create", "update", "attach", "select")),
        ):
            for action in actions:
                properties, required = {}, []
                if domain == "catalog" and action not in ("attach", "select"):
                    properties["kind"] = {
                        "type": "string",
                        "enum": ["artists", "albums", "tracks"],
                    }
                    required.append("kind")
                if action not in ("list", "create"):
                    properties["id"] = {
                        "type": "string",
                        "description": "Existing repertoire item or catalog record ID",
                    }
                    required.append("id")
                if action == "list":
                    properties.update(
                        offset={"type": "integer", "minimum": 0},
                        limit={"type": "integer", "minimum": 1, "maximum": 100},
                    )
                    if domain == "catalog":
                        properties.update(
                            q={"type": "string", "maxLength": 200},
                            archived={"type": "boolean"},
                        )
                elif action != "get":
                    descriptions = {
                        "create": "Repertoire: title,artist,instrument,body,tags,key,capo,tuning,notation{format,text},source_url,links[{type,id,label}],scroll_duration_seconds,attachment_refs[{slug,version}]. Catalog artists:name,bio; albums:title,artist_id,track_ids; tracks:title,artist_id,notes.",
                        "update": "Changed editable fields plus current revision. Catalog archived is boolean. Server-owned history, renders and scheduling are immutable.",
                        "practice": "attempt_id, grade integer0..5, occurred_at offset ISO timestamp, timezone IANA name, revision. Retry identical attempt input.",
                        "attach": "revision,artifact_ref{slug,version},source{kind:imported,label,license}. Actual canonical PCM WAV only; unverified generation claims unavailable.",
                        "select": "revision and render_id already attached to this track.",
                    }
                    properties["data"] = {
                        "type": "object",
                        "description": descriptions[action],
                    }
                    required.append("data")
                read_only = action in ("list", "get")
                tools.append(
                    ToolDefinition(
                        name=f"music_{domain}_{action}",
                        provider=self.name,
                        description=f"{action.capitalize()} music {domain} records through the persistent versioned store.",
                        parameters={
                            "type": "object",
                            "properties": properties,
                            "required": required,
                            "additionalProperties": False,
                        },
                        requires_approval=not read_only,
                        risk_level=RiskLevel.SAFE if read_only else RiskLevel.CAUTION,
                    )
                )
        return tools

    async def invoke(self, tool_name, arguments):
        definitions = {tool.name: tool for tool in await self.list_tools()}
        if tool_name not in definitions:
            return ToolResult(
                success=False,
                error="Unknown music tool",
                metadata={"code": "unknown_tool"},
            )
        schema = definitions[tool_name].parameters
        if (
            not isinstance(arguments, dict)
            or set(arguments) - set(schema["properties"])
            or set(schema["required"]) - set(arguments)
        ):
            return ToolResult(
                success=False,
                error="Invalid music tool arguments",
                metadata={"code": "invalid_input", "status": 400},
            )
        domain, action = tool_name.removeprefix("music_").split("_", 1)
        store = self.repertoire if domain == "repertoire" else self.catalog
        params = dict(arguments)
        args = []
        if "kind" in params:
            args.append(params.pop("kind"))
        if "id" in params:
            args.append(params.pop("id"))
        if "data" in params:
            args.append(params.pop("data"))
        try:
            result = getattr(store, action)(*args, **params)
            return ToolResult(
                success=True,
                output=json.dumps(result),
                metadata={"domain": domain, "operation": action},
            )
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
    return MusicToolProvider()
