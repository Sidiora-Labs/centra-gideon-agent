"""Native approval-aware tools for the Moltbook adapter."""

import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .moltbook import MoltbookAdapter, MoltbookError


class MoltbookTools(ToolProvider):
    def __init__(self, adapter=None):
        self.adapter = adapter or MoltbookAdapter(config_dir())

    @property
    def name(self):
        return "gideon-moltbook"

    @property
    def display_name(self):
        return "Moltbook"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="moltbook_read",
                provider=self.name,
                description="Read the configured Moltbook profile, status, feed or post comments.",
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "post_id": {"type": "string"},
                        "sort": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="moltbook_history",
                provider=self.name,
                description="Inspect durable Moltbook read and write receipts without credentials or post bodies.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="moltbook_write",
                provider=self.name,
                description="Publish an explicitly approved Moltbook post or comment through the configured account.",
                parameters={
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                        "kind": {"type": "string"},
                        "post_id": {"type": "string"},
                        "parent_id": {"type": "string"},
                        "submolt": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["request_id", "kind", "content"],
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, name, arguments):
        try:
            if name == "moltbook_history" and arguments == {}:
                result = {"items": self.adapter.history()}
            elif name == "moltbook_read" and isinstance(arguments, dict):
                args = dict(arguments)
                action = args.pop("action", None)
                result = await self.adapter.read(action, **args)
            elif name == "moltbook_write" and isinstance(arguments, dict):
                result = await self.adapter.write(arguments, approved=True)
            else:
                raise MoltbookError("Invalid Moltbook tool arguments")
            return ToolResult(success=True, output=json.dumps(result))
        except MoltbookError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                metadata={"status": exc.status, "code": exc.code},
            )


def create_provider(config=None):
    return MoltbookTools()
