"""Stable ACP wire constants and shared event data structures."""

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
METHOD_INITIALIZE = "initialize"
METHOD_SESSION_NEW = "session/new"
METHOD_SET_MODEL = "session/set_model"
METHOD_SET_MODE = "session/set_mode"
METHOD_PROMPT = "session/prompt"
METHOD_CANCEL = "session/cancel"
METHOD_REQUEST_PERMISSION = "session/request_permission"
METHOD_SESSION_UPDATE = "session/update"
METHOD_METADATA = "_vendor.dev/metadata"
METHOD_COMMANDS_EXECUTE = "_vendor.dev/commands/execute"
METHOD_SESSION_LOAD = "session/load"
METHOD_COMPACTION_STATUS = "_vendor.dev/compaction/status"
METHOD_CLEAR_STATUS = "_vendor.dev/clear/status"
METHOD_AGENT_SWITCHED = "_vendor.dev/agent/switched"
CAP_COMMANDS = "_vendor.dev/commands"
CAP_LOAD_SESSION = "loadSession"
UPDATE_AGENT_MESSAGE_CHUNK = "agent_message_chunk"
UPDATE_TOOL_CALL = "tool_call"
UPDATE_TOOL_CALL_UPDATE = "tool_call_update"
OUTCOME_SELECTED = "selected"
OUTCOME_CANCELLED = "cancelled"
OPTION_ALLOW_ONCE = "allow_once"
OPTION_ALLOW_ALWAYS = "allow_always"
STOP_REASON_CANCELLED = "cancelled"
STOP_REASON_END_TURN = "end_turn"
STOP_REASON_STOPPED_BY_USER = "stopped_by_user"
CANCELLED_STOP_REASONS = (STOP_REASON_CANCELLED, STOP_REASON_STOPPED_BY_USER)
APPROVAL_AUTO = "auto"
APPROVAL_INTERACTIVE = "interactive"


def is_cancelled_stop(reason: str | None) -> bool:
    return reason in CANCELLED_STOP_REASONS


@dataclass
class JsonRpcRequest:
    method: str
    params: dict[str, Any]
    id: int
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        wire_fields = ("jsonrpc", "id", "method", "params")
        return {key: getattr(self, key) for key in wire_fields}


@dataclass
class JsonRpcMessage:
    id: Any = None
    method: str | None = None
    result: Any = None
    error: Any = None
    params: Any = None

    def is_response_for(self, req_id: int) -> bool:
        return self.id == req_id

    def is_method(self, name: str) -> bool:
        return self.method == name


@dataclass
class AcpEvent:
    kind: str
    text: str = ""
    tool_call_id: str = ""
    title: str = ""
    tool_kind: str = ""
    tool_purpose: str = ""
    context_usage_pct: float | None = None
    stop_reason: str = ""
    request_id: str | int = ""
    options: list[dict[str, str]] = field(default_factory=list)
    tool_input: str = ""
    tool_input_obj: dict[str, Any] | None = None
    file_change: dict[str, str] | None = None
    tool_output: str = ""
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


@dataclass
class AcpPromptStats:
    event_count: int = 0
    text_chunks: int = 0
    tool_calls: list[tuple[str, str]] = field(default_factory=list)
    context_pct: float | None = None
