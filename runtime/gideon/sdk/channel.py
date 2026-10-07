"""SDK: the channel-transport contract + the runtime surface a channel app needs.

A channel app (Slack, and future Telegram/Discord) owns a full inbound receiver +
outbound renderer, so it needs more of the platform than a leaf provider: session
routing, conversation history, cron/schedule, context building, transcription,
security redaction, audit (SEL), and the gateway-services / channel-delivery
contracts. Rather than let the app reach into core internals (which would freeze
those internals), every symbol it needs is re-exported here — the single stable
channel SDK facade. Core can move the underlying modules without breaking apps.

Grouped by concern below. All names are re-exports; see the owning core module for
the authoritative docs.
"""

from gideon import __version__
from gideon.assurance.testing.channel_conformance import (
    CapturingState,
    ChannelContractError,
    assert_channel_approvals,
    assert_channel_contract,
)
from gideon.automation.schedule import compute_next_run_ts, format_schedule
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.schedule_view import describe_cadence, to_schedule_row
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.triggers.tools import delete as delete_automation
from gideon.automation.triggers.tools import delete_all as delete_all_automations
from gideon.automation.triggers.tools import set_paused as set_automation_paused
from gideon.cognition.context import (
    PromptAssembler,
    build_cancelled_turn_preamble,
    compress_thread_history,
)
from gideon.cognition.doc_parser import extract_text, is_parseable_document
from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.memory_service import MemoryService
from gideon.core.atomic_write import atomic_write
from gideon.core.config.credentials import (
    owner_id_credential,
    owner_id_for,
    save_credential,
)
from gideon.core.config.loader import (
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
    AppConfig,
    config_dir,
    config_path,
)
from gideon.core.textfmt import extract_options, strip_thinking_tags
from gideon.engine import session_restrictions
from gideon.engine.gateway_services import GatewayServices
from gideon.engine.hooks import (
    HOOK_REPLY,
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    safe_read_file,
    validate_file_path,
)

# ── Session + conversation runtime ──
from gideon.engine.session import BACKGROUND_KEY, ConversationDirectory, SessionMap
from gideon.engine.subagent import DelegationSupervisor
from gideon.engine.task import Task
from gideon.extensions.providers.settings import ProviderSettings
from gideon.extensions.providers.use_cases import (
    load_use_case_settings,
    save_use_case_settings,
)
from gideon.extensions.skills import ProcedureLibrary
from gideon.integrations.acp.errors import AcpError, AcpProcessDied, AcpTimeoutError
from gideon.integrations.acp.types import (
    CANCELLED_STOP_REASONS,
    STOP_REASON_CANCELLED,
    STOP_REASON_END_TURN,
    STOP_REASON_STOPPED_BY_USER,
    is_cancelled_stop,
)
from gideon.integrations.channel_delivery import (
    ONE_CALL_ANSWERS,
    ApprovalAnswer,
    ChannelDelivery,
    offered_answers,
    raw_delivery_for,
)
from gideon.integrations.channel_transports import (
    get_transport,
    register_transport,
    unregister_transport,
)
from gideon.integrations.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)
from gideon.integrations.channel_trust import (
    CANNED_OWNER_PAIRED_REPLY,
    CANNED_PAIRING_REPLY,
    TrustVerdict,
    allow_sender,
    apply_trust_action,
    cancel_owner_pairing,
    cancel_pairing_code,
    create_owner_pairing_code,
    create_pairing_code,
    deny_sender,
    fence_channel_content,
    guard_inbound,
    is_allowed_sender,
    is_tracked_channel,
    list_seen_channels,
    note_seen_channel,
    note_unknown_sender,
    owner_pairing_status,
    redeem_owner_pairing_code,
    redeem_pairing_code,
    set_trust_policies,
    track,
    trust_policies,
    untrack,
)
from gideon.integrations.llm.base import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
    ModelProvider,
)
from gideon.integrations.llm_helpers import save_conversation_turn
from gideon.integrations.mcp_discovery import list_servers
from gideon.integrations.prompt_providers.runtime import render_use_case_prompt
from gideon.integrations.transcribe import is_available as stt_available
from gideon.integrations.transcribe import transcribe_audio
from gideon.integrations.tts.registry import active_voice_params
from gideon.integrations.voice_reply import voice_reply
from gideon.interfaces.dashboard.chat import save_session_to_history
from gideon.interfaces.dashboard.handlers import get_update_info
from gideon.interfaces.dashboard.origin import (
    dashboard_origin,
    devspaces_proxy_url,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from gideon.interfaces.dashboard.token_auth import (
    LINK_WINDOW_SECS,
    MAX_SESSION_TTL_SECS,
    NOT_THE_OWNER_SENTENCE,
    generate_token,
    owner_sign_in_token,
    parse_duration,
)
from gideon.operations.stats import Stats
from gideon.security import trust_mode
from gideon.security.approval_answer import Principal, on_channel
from gideon.security.approval_brief import approval_brief_for, channel_approval_brief
from gideon.security.approval_grants import approval_window_secs

# ── Security + audit ──
from gideon.security.security import (
    is_sensitive_path,
    redact,
    redact_and_truncate,
    redact_credentials,
    redact_exfiltration_urls,
    should_record_observe_history,
)
from gideon.security.sel import sel

__all__ = [
    "ApprovalAnswer",
    "ONE_CALL_ANSWERS",
    "offered_answers",
    "raw_delivery_for",
    "Principal",
    "on_channel",
    "approval_brief_for",
    "approval_window_secs",
    "AcpError",
    "AcpProcessDied",
    "AcpTimeoutError",
    "AppConfig",
    "BACKGROUND_KEY",
    "CANNED_PAIRING_REPLY",
    "CANNED_OWNER_PAIRED_REPLY",
    "NOT_THE_OWNER_SENTENCE",
    "CRED_OWNER_ID",
    "CRED_SLACK_APP_TOKEN",
    "CRED_SLACK_BOT_TOKEN",
    "owner_id_credential",
    "owner_id_for",
    "CapturingState",
    "ChannelCapabilities",
    "ChannelContractError",
    "ChannelDelivery",
    "ChannelMessage",
    "ChannelTransportProvider",
    "PromptAssembler",
    "ConversationLog",
    "EVENT_COMPACTION_STATUS",
    "EVENT_COMPLETE",
    "EVENT_PERMISSION_REQUEST",
    "EVENT_TEXT_CHUNK",
    "EVENT_THINKING_CHUNK",
    "EVENT_TOOL_CALL",
    "GatewayServices",
    "HOOK_REPLY",
    "HistoryConsolidator",
    "LINK_WINDOW_SECS",
    "LLMEvent",
    "MAX_SESSION_TTL_SECS",
    "MemoryService",
    "ModelProvider",
    "OutboundMessage",
    "ProviderSettings",
    "STOP_REASON_CANCELLED",
    "STOP_REASON_END_TURN",
    "CANCELLED_STOP_REASONS",
    "STOP_REASON_STOPPED_BY_USER",
    "is_cancelled_stop",
    "ConversationDirectory",
    "SessionMap",
    "ProcedureLibrary",
    "Stats",
    "DelegationSupervisor",
    "TOOL_AUTO_APPROVE",
    "TOOL_DENY",
    "Task",
    "Trigger",
    "TriggerStore",
    "TrustVerdict",
    "__version__",
    "active_voice_params",
    "allow_sender",
    "apply_trust_action",
    "assert_channel_contract",
    "assert_channel_approvals",
    "atomic_write",
    "build_cancelled_turn_preamble",
    "compress_thread_history",
    "compute_next_run_ts",
    "config_dir",
    "config_path",
    "create_pairing_code",
    "cancel_pairing_code",
    "channel_approval_brief",
    "create_owner_pairing_code",
    "cancel_owner_pairing",
    "owner_pairing_status",
    "owner_sign_in_token",
    "dashboard_origin",
    "delete_all_automations",
    "delete_automation",
    "deny_sender",
    "describe_cadence",
    "devspaces_proxy_url",
    "extract_options",
    "extract_text",
    "fence_channel_content",
    "format_schedule",
    "generate_token",
    "get_update_info",
    "guard_inbound",
    "get_transport",
    "is_allowed_sender",
    "is_local_bind",
    "is_parseable_document",
    "is_sensitive_path",
    "is_tracked_channel",
    "list_servers",
    "load_use_case_settings",
    "note_unknown_sender",
    "note_seen_channel",
    "list_seen_channels",
    "set_trust_policies",
    "parse_dashboard_url",
    "parse_duration",
    "redact",
    "redact_and_truncate",
    "redact_credentials",
    "redact_exfiltration_urls",
    "register_transport",
    "redeem_pairing_code",
    "redeem_owner_pairing_code",
    "render_use_case_prompt",
    "resolve_bind_host",
    "resolve_dashboard_host",
    "safe_read_file",
    "save_conversation_turn",
    "save_credential",
    "save_session_to_history",
    "save_use_case_settings",
    "sel",
    "session_restrictions",
    "set_automation_paused",
    "should_record_observe_history",
    "strip_thinking_tags",
    "stt_available",
    "to_schedule_row",
    "track",
    "transcribe_audio",
    "trust_mode",
    "trust_policies",
    "untrack",
    "unregister_transport",
    "validate_file_path",
    "voice_reply",
]
