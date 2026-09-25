import json
from jsonschema import ValidationError, validate
from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.artifacts.registry import get_provider
from .media_shares import MediaShareError, MediaShares
from .peers import PeerError, PeerStore

ID = {"type": "string", "minLength": 1, "maxLength": 200}
SCHEMAS = {
    "platform_media_shares_list": {"type": "object", "properties": {}, "additionalProperties": False},
    "platform_media_share": {"type": "object", "required": ["request_id", "peer_id", "artifact_id", "artifact_version"], "properties": {"request_id": ID, "peer_id": ID, "artifact_id": ID, "artifact_version": {"type": "integer", "minimum": 1}}, "additionalProperties": False},
    "platform_media_share_revoke": {"type": "object", "required": ["share_id", "revision"], "properties": {"share_id": ID, "revision": {"type": "integer", "minimum": 1}}, "additionalProperties": False},
}


class MediaShareTools(ToolProvider):
    name = "gideon-media-sharing"; display_name = "Selective media sharing"
    def __init__(self, service=None): self.service = service or MediaShares(config_dir(), PeerStore(), get_provider())

    async def list_tools(self):
        descriptions = {"platform_media_shares_list": "Read selective media share receipts without media bytes or peer secrets.", "platform_media_share": "Share one explicit canonical media artifact version with one opt-in direct peer.", "platform_media_share_revoke": "Revoke one active selective media share at its current revision."}
        return [ToolDefinition(name=name, description=descriptions[name], provider=self.name, parameters=schema, requires_approval=name != "platform_media_shares_list", risk_level=RiskLevel.CAUTION if name != "platform_media_shares_list" else RiskLevel.SAFE) for name, schema in SCHEMAS.items()]

    async def invoke(self, tool_name, arguments):
        try:
            validate(arguments, SCHEMAS[tool_name])
            if tool_name == "platform_media_shares_list": result = self.service.list()
            elif tool_name == "platform_media_share": result = await self.service.share(arguments)
            else: result = await self.service.revoke(arguments["share_id"], arguments["revision"])
            return ToolResult(success=True, output=json.dumps(result))
        except KeyError: return ToolResult(success=False, error="Unknown selective media sharing tool")
        except (ValidationError, MediaShareError, PeerError) as exc: return ToolResult(success=False, error=str(exc), metadata={"status": getattr(exc, "status", 400)})


def create_provider(config=None): return MediaShareTools()
