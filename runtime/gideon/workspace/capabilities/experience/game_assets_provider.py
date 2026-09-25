import json
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .game_assets import GameAssets
from .store import Conflict, ExperienceStore, NotFound


class GameAssetTools(ToolProvider):
    name, display_name = "gideon-game-assets", "Game assets"

    def __init__(self, store=None, artifacts=None): self.service = GameAssets(store or ExperienceStore(), artifacts)

    async def list_tools(self):
        return [
            ToolDefinition(name="experience_game_assets_get", provider=self.name, description="Read game asset projects and verified compile/publication receipts.", parameters={"type":"object","properties":{},"additionalProperties":False}, requires_approval=False, risk_level=RiskLevel.SAFE),
            ToolDefinition(name="experience_game_assets_compile", provider=self.name, description="Compile immutable artifact bindings into a runnable game export.", parameters={"type":"object","properties":{"id":{"type":"string"},"revision":{"type":"integer","minimum":1}},"required":["id","revision"],"additionalProperties":False}, requires_approval=True, risk_level=RiskLevel.CAUTION),
            ToolDefinition(name="experience_game_assets_publish", provider=self.name, description="Copy and verify a compiled export inside its bound managed app storage.", parameters={"type":"object","properties":{"id":{"type":"string"},"revision":{"type":"integer","minimum":1}},"required":["id","revision"],"additionalProperties":False}, requires_approval=True, risk_level=RiskLevel.CAUTION),
        ]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name == "experience_game_assets_get" and arguments == {}: result = self.service.list()
            elif tool_name in ("experience_game_assets_compile", "experience_game_assets_publish") and isinstance(arguments, dict) and set(arguments) == {"id", "revision"}:
                action = tool_name.rsplit("_", 1)[-1]
                result = {action: getattr(self.service, action)(arguments["id"], arguments["revision"])}
            else: return ToolResult(False, error="arguments must match the declared game asset operation")
            return ToolResult(True, output=json.dumps(result))
        except (Conflict, NotFound, ValueError) as exc: return ToolResult(False, error=str(exc), metadata={"kind": type(exc).__name__})


def create_provider(config=None): return GameAssetTools()
