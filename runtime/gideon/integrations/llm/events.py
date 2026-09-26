"""Neutral agent-event type — the provider-agnostic event the chat runner reads.

Defines the backend-neutral :class:`AgentEvent` that every backend (ACP via
``acp/adapter.py``, the native loop, the HTTP model providers) emits and the
chat runner consumes. ``LLMEvent`` aliases it.

Common fields mirror ``acp.types.AcpEvent`` so the runner can consume either.
The event-kind constants live here as the canonical home; ``acp.types`` imports
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EVENT_TEXT_CHUNK = "text_chunk"
EVENT_THINKING_CHUNK = "thinking_chunk"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_CALL_UPDATE = "tool_call_update"
EVENT_TOOL_RESULT = "tool_result"
EVENT_PERMISSION_REQUEST = "permission_request"
EVENT_COMPLETE = "complete"
EVENT_COMPACTION_STATUS = "compaction_status"
EVENT_CLEAR_STATUS = "clear_status"
EVENT_AGENT_SWITCHED = "agent_switched"


@dataclass
class ContextUsage:
    input_tokens: int | None = None
    total_input_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    context_window_tokens: int | None = None

    def as_payload(self) -> dict[str, int | None]:
        payload = {
            "input_tokens": self.input_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "context_window_tokens": self.context_window_tokens,
        }
        if self.total_input_tokens is not None:
            payload["total_input_tokens"] = self.total_input_tokens
        return payload


@dataclass
class AgentEvent:
    """A neutral event from any agent/model backend's turn stream.

    Common fields match ``acp.types.AcpEvent`` so the chat runner consumes
    either without change. ``tool_input``/``tool_output`` are
    typed ``Any`` (the native loop may pass structured values; ACP passes str).
    """

    kind: str
    text: str = ""
    tool_call_id: str = ""
    title: str = ""
    tool_kind: str = ""
    tool_purpose: str = ""
    risk_level: str = ""
    context_usage_pct: float | None = None
    context_usage: ContextUsage | None = None
    stop_reason: str = ""
    request_id: str | int = ""
    options: Any = field(default_factory=list)
    tool_input: Any = ""
    tool_input_obj: dict[str, Any] | None = None
    file_change: dict[str, str] | None = None
    tool_output: Any = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    event_count: int = 0
    tool_call_count: int = 0
    tool_meta: dict[str, Any] = field(default_factory=dict)
