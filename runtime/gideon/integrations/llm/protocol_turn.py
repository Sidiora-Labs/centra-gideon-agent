"""Shared conversation state and neutral response records for HTTP protocols."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from gideon.integrations.llm.events import ContextUsage

from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_TOOL_CALL,
    CancelOutcome,
    LLMEvent,
    ModelProvider,
)

logger = logging.getLogger(__name__)


def wire_value(value: Any, key: str, default: Any = None) -> Any:
    return (
        value.get(key, default)
        if isinstance(value, dict)
        else getattr(value, key, default)
    )


@dataclass
class ToolFragment:
    identifier: str
    name: str = ""
    arguments: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    delivered: bool = False

    def emit(self, stop_reason: str = "") -> list[LLMEvent]:
        if self.delivered:
            return []
        self.delivered = True
        return [
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=self.identifier,
                title=self.name,
                tool_input=self.arguments,
                tool_meta=dict(self.metadata),
                stop_reason=stop_reason,
            )
        ]


@dataclass
class TurnUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    measured_input_tokens: int | None = None
    measured_total_input_tokens: int | None = None
    measured_cache_creation_tokens: int | None = None
    measured_cache_read_tokens: int | None = None

    def terminal(
        self, context: float | None, *, context_window_tokens: int | None = None
    ) -> LLMEvent:
        usage = None
        if any(
            value is not None
            for value in (
                self.measured_input_tokens,
                self.measured_total_input_tokens,
                self.measured_cache_creation_tokens,
                self.measured_cache_read_tokens,
                context_window_tokens,
            )
        ):
            usage = ContextUsage(
                input_tokens=self.measured_input_tokens,
                total_input_tokens=self.measured_total_input_tokens,
                cache_creation_tokens=self.measured_cache_creation_tokens,
                cache_read_tokens=self.measured_cache_read_tokens,
                context_window_tokens=context_window_tokens,
            )
        return LLMEvent(
            kind=EVENT_COMPLETE,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_creation_tokens=self.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens,
            context_usage_pct=context,
            context_usage=usage,
            cost_usd=0.0,
        )


class ConversationProtocol(ModelProvider):
    _client: Any

    def _initialize_conversation(self) -> None:
        self._history: list[dict[str, Any]] = []
        self._last_context_pct: float | None = None
        self._pending_image = ""

    def _begin_message(self, message: str, limit: int) -> list[dict[str, Any]]:
        self._history = [*self._history, {"role": "user", "content": message}][-limit:]
        return self._history

    def _record_completion(
        self, answer: list[str], context: float | None, *, remember: bool
    ) -> float | None:
        if not remember:
            return context
        if context is not None:
            self._last_context_pct = context
        text = "".join(answer)
        if text:
            self._history.append({"role": "assistant", "content": text})
        return self._last_context_pct

    def stage_image_part(self, data_url: str) -> bool:
        if data_url:
            self._pending_image = data_url
            return True
        return False

    def _image_content(self, data_url: str) -> dict | None:
        raise NotImplementedError

    def _with_pending_image(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        staged, self._pending_image = self._pending_image, ""
        if not staged:
            return messages
        image = self._image_content(staged)
        if image is None:
            return messages
        for position, message in reversed(list(enumerate(messages))):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            parts = (
                [*content]
                if isinstance(content, list)
                else [{"type": "text", "text": str(content or "")}]
            )
            amended = dict(message, content=[*parts, image])
            return [*messages[:position], amended, *messages[position + 1 :]]
        return messages

    async def shutdown(self) -> None:
        try:
            await self._client.close()
        except Exception:
            logger.warning("Protocol client shutdown failed", exc_info=True)
        self._history.clear()

    async def approve_tool(self, request_id: str | int) -> None:
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        return None

    def context_usage_pct(self) -> float | None:
        return self._last_context_pct

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        return "no_turn"
