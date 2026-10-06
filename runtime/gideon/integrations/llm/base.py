"""Provider contracts and fallback routing shared by inference integrations."""

from abc import ABC, abstractmethod

from gideon.core.turn_streams import closing_stream
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gideon.integrations.llm.events import (
    EVENT_AGENT_SWITCHED,
    EVENT_CLEAR_STATUS,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_MODEL_SUBSTITUTION,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
)
from gideon.integrations.llm.events import AgentEvent as LLMEvent
from gideon.integrations.llm.prompt_cache import PromptCache

CancelOutcome = Literal["acked", "timeout", "no_turn", "error"]


@dataclass(frozen=True)
class ModelSubstitution:
    """A named model could not serve and a configured model answered instead."""

    requested: str
    served: str
    why: str
    fix: str = ""
    who: str = ""

    def sentence(self) -> str:
        whose = f"{self.who} " if self.who else ""
        line = f"ran on {self.served} instead of {whose}{self.requested}: {self.why}"
        return f"{line}. {self.fix}" if self.fix else line

    def notice(self) -> str:
        sentence = self.sentence()
        return sentence[:1].upper() + sentence[1:] + "."

    def to_dict(self) -> dict[str, str]:
        return {
            "requested": self.requested,
            "served": self.served,
            "why": self.why,
            "fix": self.fix,
            "who": self.who,
            "sentence": self.sentence(),
        }


def _last_user_text(messages: list[dict]) -> str:
    selected = next(
        (message for message in reversed(messages) if message.get("role") == "user"), {}
    )
    return str(selected.get("content", ""))


async def _forward_events(events: AsyncIterator[LLMEvent]) -> AsyncIterator[LLMEvent]:
    async with closing_stream(events) as _owned_events:
        async for event in _owned_events:
            yield event


class ModelProvider(ABC):
    supports_tools: bool = False
    prompt_cache: PromptCache = PromptCache.NONE
    served_model_ref: str = ""

    @property
    def first_token_timeout_secs(self) -> float | None:
        """Provider-owned startup deadline, when the provider enforces one."""
        return None

    async def served_context_window(self) -> int | None:
        """Capacity of this request-bound provider/model, or unknown."""
        return None

    @property
    def sampling_temperature(self) -> float | None:
        """The sampling temperature this provider sends, when it can report one."""
        return None

    @property
    def unsent_options(self) -> dict[str, str]:
        """Requested call options omitted by the provider, with their reasons."""
        return {}

    @property
    def output_token_limit(self) -> int | None:
        """Output-token ceiling applied to requests through this provider."""
        return None

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

    @property
    def compacts_automatically(self) -> bool:
        """Whether an external runtime owns its own context compaction."""
        return False

    @property
    def compacts_in_process(self) -> bool:
        return False

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        async with closing_stream(_forward_events(self.stream(command))) as _owned_events:
            async for event in _owned_events:
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
        async with closing_stream(_forward_events(self.stream(_last_user_text(messages)))) as _owned_events:
            async for event in _owned_events:
                yield event
