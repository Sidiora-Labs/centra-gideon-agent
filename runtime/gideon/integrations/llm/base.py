"""Provider contracts and fallback routing shared by inference integrations."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from gideon.integrations.llm.events import (
    EVENT_AGENT_SWITCHED,
    EVENT_CLEAR_STATUS,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
)
from gideon.integrations.llm.events import AgentEvent as LLMEvent
from gideon.integrations.llm.prompt_cache import PromptCache

CancelOutcome = Literal["acked", "timeout", "no_turn", "error"]


def _last_user_text(messages: list[dict]) -> str:
    selected = next(
        (message for message in reversed(messages) if message.get("role") == "user"), {}
    )
    return str(selected.get("content", ""))


async def _forward_events(events: AsyncIterator[LLMEvent]) -> AsyncIterator[LLMEvent]:
    async for event in events:
        yield event


class ModelProvider(ABC):
    supports_tools: bool = False
    prompt_cache: PromptCache = PromptCache.NONE

    @abstractmethod
    async def start(self) -> None:
        """Prepare the provider for requests."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release provider-owned resources."""

    @abstractmethod
    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        yield LLMEvent(kind=EVENT_COMPLETE)

    @abstractmethod
    async def approve_tool(self, request_id: str | int) -> None:
        """Accept an interactive tool request."""

    @abstractmethod
    async def reject_tool(self, request_id: str | int) -> None:
        """Decline an interactive tool request."""

    @abstractmethod
    def context_usage_pct(self) -> float | None:
        """Return the last measurement, or None when none exists."""

    @property
    def session_id(self) -> str:
        return ""

    async def cleanup_session(self, session_id: str) -> None:
        return None

    @property
    def supports_native_commands(self) -> bool:
        return False

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        async for event in _forward_events(self.stream(command)):
            yield event

    async def compact(self, context: str = "") -> None:
        return None

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        return {"type": "timeout"}

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        return "no_turn"

    def is_alive(self) -> bool:
        return True

    def touch_activity(self) -> None:
        return None

    def stage_image_part(self, data_url: str) -> bool:
        return False

    def set_workspace(self, path: Path) -> None:
        return None

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        return None

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        async for event in _forward_events(self.stream(_last_user_text(messages))):
            yield event
