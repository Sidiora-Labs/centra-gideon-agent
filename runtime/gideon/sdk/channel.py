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

# ── Process-global trust + session-restriction state (shared by all surfaces) ──
from gideon import __version__  # noqa: F401
from gideon import session_restrictions, trust_mode  # noqa: F401
from gideon.acp.errors import (  # noqa: F401
    AcpError,
    AcpProcessDied,
    AcpTimeoutError,
)
from gideon.acp.types import (  # noqa: F401
    STOP_REASON_CANCELLED,
    STOP_REASON_END_TURN,
)
from gideon.atomic_write import atomic_write  # noqa: F401
from gideon.channel_delivery import ChannelDelivery  # noqa: F401

# ── Transport ABC + data types ──
from gideon.channel_transports.base import (  # noqa: F401
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)

# ── Config + credentials ──
# (Channel activation modes are the channel APP's own concept now —
# slack_runtime.settings owns ACTIVATION_* for the Slack app.)
# CRED_SLACK_* are the slack app's credential KEYS in the generic cred store;
# they are defined in config/loader.py (the store's home) and re-exported here
# as the surface apps import — see the definition site for the layering note.
from gideon.config.loader import (  # noqa: F401
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
    AppConfig,
    config_dir,
    config_path,
    save_credential,
)
from gideon.context import (  # noqa: F401
    ContextBuilder,
    build_cancelled_turn_preamble,
    compress_thread_history,
)

# ── Dashboard integration (link/handoff/mirror/update surfaces a channel drives) ──
from gideon.dashboard.chat import _run_chat, _save_session_to_history  # noqa: F401
from gideon.dashboard.handlers import get_update_info  # noqa: F401
from gideon.dashboard.origin import (  # noqa: F401
    dashboard_origin,
    devspaces_proxy_url,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from gideon.dashboard.token_auth import (  # noqa: F401
    LINK_WINDOW_SECS,
    MAX_SESSION_TTL_SECS,
    generate_token,
    parse_duration,
)
from gideon.doc_parser import extract_text, is_parseable_document  # noqa: F401

# ── The core↔channel seams ──
from gideon.gateway_services import GatewayServices  # noqa: F401
from gideon.history import ConversationLog, HistoryConsolidator  # noqa: F401

# ── Hooks + LLM streaming events + ACP ──
from gideon.hooks import (  # noqa: F401
    HOOK_REPLY,
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    safe_read_file,
    validate_file_path,
)
from gideon.llm.base import (  # noqa: F401
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
    ModelProvider,
)
from gideon.llm_helpers import save_conversation_turn  # noqa: F401
from gideon.mcp_discovery import list_servers  # noqa: F401
from gideon.memory_service import MemoryService  # noqa: F401
from gideon.prompt_providers.runtime import render_use_case_prompt  # noqa: F401
from gideon.providers.settings import ProviderSettings  # noqa: F401
from gideon.providers.use_cases import (  # noqa: F401
    load_use_case_settings,
    save_use_case_settings,
)

# ── Automations (the unified trigger store) ──
#
# `ScheduleService` is GONE (S112). A channel app's `/cron` surface reads and mutates automations
# through the same store, projection and tool functions the API and the chat tools use, so there is
# exactly one behaviour to reason about — a channel that kept its own scheduler view would drift
# from the Automations page the moment either changed.
#
# `describe_cadence` replaces `format_schedule(job.schedule)`: it takes a `Trigger` and delegates to
# the same shipped formatter, so the wording stays identical while the input becomes the store's.
# `to_schedule_row` is the wire projection (id, enabled, message, next_run_ts, last_status) that the
# API already publishes, which is what a list command needs.
from gideon.schedule import compute_next_run_ts, format_schedule  # noqa: F401

# ── Security + audit ──
from gideon.security import (  # noqa: F401
    is_sensitive_path,
    redact,
    redact_and_truncate,
    redact_credentials,
    redact_exfiltration_urls,
    should_record_observe_history,
)
from gideon.sel import sel  # noqa: F401

# ── Session + conversation runtime ──
from gideon.session import (  # noqa: F401
    BACKGROUND_KEY,
    SessionManager,
    SessionMap,
)
from gideon.skills import SkillsLoader  # noqa: F401
from gideon.stats import Stats  # noqa: F401
from gideon.subagent import SubagentManager  # noqa: F401
from gideon.task import Task  # noqa: F401
from gideon.textfmt import extract_options, strip_thinking_tags  # noqa: F401

# ── Media + prompts + discovery ──
from gideon.transcribe import is_available as stt_available  # noqa: F401
from gideon.transcribe import transcribe_audio  # noqa: F401
from gideon.triggers.models import Trigger  # noqa: F401
from gideon.triggers.schedule_view import (  # noqa: F401
    describe_cadence,
    to_schedule_row,
)
from gideon.triggers.store import TriggerStore  # noqa: F401
from gideon.triggers.tools import delete as delete_automation  # noqa: F401
from gideon.triggers.tools import delete_all as delete_all_automations  # noqa: F401
from gideon.triggers.tools import set_paused as set_automation_paused  # noqa: F401
from gideon.tts.registry import active_voice_params  # noqa: F401
from gideon.voice_reply import voice_reply  # noqa: F401
