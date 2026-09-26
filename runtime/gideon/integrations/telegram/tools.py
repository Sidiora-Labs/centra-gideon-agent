"""Telegram tools bound to the currently executing Gideon conversation."""

from gideon.integrations.tool_providers.base import (
    ToolProvider,
    ToolDefinition,
    ToolResult,
    RiskLevel,
)
from gideon.integrations.mcp_core import get_current_session_key
from .api import TelegramError


class TelegramTools(ToolProvider):
    name = "telegram"
    display_name = "Telegram"

    def __init__(self, transport):
        self.transport = transport

    async def list_tools(self):
        return [
            ToolDefinition(
                name="telegram_send_media",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description="Send generated files to the current Telegram conversation; two to ten photos/videos are delivered as an album. File paths must be inside Gideon's allowed workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 10,
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["files"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="telegram_clarify",
                provider=self.name,
                interactive=True,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Ask the owner a question in the current Telegram conversation and wait for a button or typed answer. Only works in a Telegram-linked chat.",
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 3500,
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 200},
                            "maxItems": 10,
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            ),
        ]

    async def invoke(self, tool_name, arguments):
        if tool_name not in ("telegram_clarify", "telegram_send_media"):
            return ToolResult(success=False, error="Unknown Telegram tool")
        key = get_current_session_key()
        state = self.transport.services.dashboard_state
        from gideon.interfaces.dashboard.chat_utils import _history_key_for

        session = state.get_session(key) or next(
            (s for s in state._sessions.values() if _history_key_for(s.key) == key),
            None,
        )
        if (
            not session
            or not session._channel_linked
            or not session._channel_thread_ts.startswith("telegram:")
        ):
            return ToolResult(
                success=False, error="This conversation is not linked to Telegram."
            )
        transport = self.transport
        channel = session._channel_id
        if hasattr(transport, "bots"):
            try:
                delivery, channel = transport.delivery.select(
                    channel, session._channel_thread_ts
                )
                transport = delivery.transport
            except TelegramError as exc:
                return ToolResult(success=False, error=str(exc))
        if tool_name == "telegram_send_media":
            paths = arguments.get("files")
            if (
                not isinstance(paths, list)
                or not 1 <= len(paths) <= 10
                or any(not isinstance(p, str) for p in paths)
            ):
                return ToolResult(success=False, error="Provide one to ten file paths")
            try:
                if len(paths) == 1:
                    result = await transport.delivery.upload_attachment(
                        channel,
                        paths[0],
                        thread_ts=session._channel_thread_ts,
                    )
                else:
                    result = await transport.api.upload_album(
                        channel,
                        paths,
                        **transport.delivery.options(session._channel_thread_ts),
                    )
                return ToolResult(
                    success=True,
                    output="Media delivered to Telegram.",
                    metadata={"messages": result},
                )
            except (TelegramError, OSError) as exc:
                return ToolResult(success=False, error=str(exc))
        question = arguments.get("question")
        options = arguments.get("options", [])
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question) > 3500
            or not isinstance(options, list)
            or len(options) > 10
            or any(not isinstance(v, str) or len(v) > 200 for v in options)
        ):
            return ToolResult(
                success=False, error="Provide a question and up to ten text options."
            )
        try:
            answer = await transport.delivery.clarify(
                channel, session._channel_thread_ts, question, options
            )
            return ToolResult(success=True, output=answer)
        except TelegramError as exc:
            return ToolResult(success=False, error=str(exc))
