"""Message handler — streams LLM responses to Slack with tool approval UI.

Routes incoming Slack messages through hooks, cron command interception,
and the LLM provider.  Supports interactive tool approval via Block Kit
buttons.

Session privacy modes
---------------------
Temporary (blank-slate): no memory reads, no memory writes, no persistence.
    The session starts with zero context and discards everything on close.
Incognito: memory reads allowed but writes blocked; persists an ephemeral
    conversation log that is discarded on close.

Both modes are tracked in the core, channel-agnostic
:mod:`gideon.session_restrictions` registry (keyed by session_key). Use
:func:`_is_slack_desk_restricted` to check whether a Slack session should skip memory
writes.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from gideon.sdk.channel import AcpError, AcpProcessDied, AcpTimeoutError
from gideon.sdk.channel import STOP_REASON_CANCELLED, STOP_REASON_END_TURN
from gideon.sdk.channel import AppConfig, config_dir, config_path
from slack_desk_runtime.settings import ACTIVATION_REVIEW
from gideon.sdk.channel import PromptAssembler as ContextBuilder
from gideon.sdk.channel import (
    TriggerStore,
    delete_all_automations,
    delete_automation,
    describe_cadence,
    set_automation_paused,
    to_schedule_row,
)
from gideon.sdk.channel import ConversationLog, HistoryConsolidator
from gideon.sdk.channel import HOOK_REPLY, TOOL_AUTO_APPROVE, TOOL_DENY, validate_file_path
from gideon.sdk.channel import save_conversation_turn
from gideon.sdk.channel import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
    ModelProvider,
)
from gideon.sdk.channel import session_restrictions, trust_mode
from gideon.sdk.channel import is_sensitive_path, redact_credentials, redact_exfiltration_urls
from gideon.sdk.channel import sel
from gideon.sdk.channel import ConversationDirectory as SessionManager
from slack_desk_runtime.blocks import deprecation_warning_block
from slack_desk_runtime.client import SlackDeskClientOps
from slack_desk_runtime.format import (
    SLACK_MSG_LIMIT,
    TRUNCATION_NOTICE,
    split_message,
    strip_thinking_tags,
    to_slack_desk_mrkdwn,
)
from gideon.sdk.channel import Stats
from gideon.sdk.channel import DelegationSupervisor as SubagentManager
from gideon.sdk.channel import Task
from gideon.sdk.channel import voice_reply as _voice_reply_fn

logger = logging.getLogger(__name__)

# Mapping of bang commands to their /gideon slash equivalents.
_BANG_TO_SLASH: dict[str, str] = {
    "!yolo": "/gideon yolo",
    "!stop": "/gideon stop",
    "!voice": "/gideon voice",
    "!agent": "/gideon agent",
    "!dashboard": "/gideon dashboard",
    "!ta": "/gideon agent",
    # "!allowlist" removed — multi-user access disabled for security
    "!channel": "/gideon channel",
    "!link-to-dashboard": "/gideon link-to-dashboard",
}

# Approval modes (UX-level, not provider-specific)
APPROVAL_AUTO = "auto"
APPROVAL_INTERACTIVE = "interactive"


def _should_auto_approve_spawn(context_builder, event_title: str) -> bool:
    """Check if a subagent_run tool call should be auto-approved."""
    return bool(
        context_builder
        and context_builder.hooks
        and context_builder.hooks.auto_approve_subagent_spawn
        and event_title == "subagent_run"
    )


# Min interval between Slack message edits (avoid rate limits)
_EDIT_INTERVAL = 1.0

# Timeout for user to click approve/reject before auto-rejecting
_APPROVAL_TIMEOUT = 120.0

# Slack Block Kit section text limit (3000 chars max); leave room for
# markdown fences (``` ... ```) that wrap the tool input.
_SLACK_SECTION_TEXT_LIMIT = 2900

# Truncation marker appended when tool_input exceeds the limit
_TRUNCATION_MARKER = "\n… [truncated]"

# Slack UX strings
_THINKING = "_Thinking…_"
_CURSOR = " ▍"
_NO_RESPONSE = "_No response._"
_STATUS_WORKING = "is working on your request"

# Pending approvals: keyed by f"{channel}:{approval_msg_ts}"
# Module-level dict — safe because gateway runs in a single asyncio event loop.
_pending_approvals: "dict[str, _PendingApproval]" = {}

# ── Phase-aware reaction constants ──────────────────────────────────────

_DEFAULT_PHASE_EMOJIS: dict[str, str] = {
    "queued": "eyes",
    "thinking": "thinking_face",
    "coding": "man_technologist",
    "browsing": "globe_with_meridians",
    "tool": "wrench",
    "done": "white_check_mark",
    "error": "scream",
}


def _build_phase_emojis(
    overrides: dict[str, str | None] | None = None,
) -> tuple[dict[str, str | None], list[str]]:
    """Return ``(phase_emoji_dict, unknown_keys)`` with optional overrides applied.

    A phase value may be ``None`` to suppress that phase entirely (no emoji
    will be added or swapped in for it).  Stall emojis and transitions from
    other phases are unaffected.

    Unknown keys are collected and returned so callers can surface them
    to the user (e.g. startup warning) rather than silently dropping them.
    """
    result: dict[str, str | None] = dict(_DEFAULT_PHASE_EMOJIS)
    unknown: list[str] = []
    for key, value in (overrides or {}).items():
        if key in _DEFAULT_PHASE_EMOJIS:
            result[key] = value
        else:
            unknown.append(key)
    return result, unknown


try:
    from slack_desk_runtime.settings import SlackDeskSettings as _SlackDeskSettings

    _overrides = _SlackDeskSettings.load().reactions
except Exception:
    logger.warning("Failed to load reaction overrides from config; using defaults", exc_info=True)
    _overrides = {}
_PHASE_EMOJIS, _unknown_phases = _build_phase_emojis(_overrides)
del _overrides
if _unknown_phases:
    logger.warning(
        "Ignoring unknown slack.reactions keys: %s (valid: %s)",
        ", ".join(repr(k) for k in _unknown_phases),
        ", ".join(sorted(_DEFAULT_PHASE_EMOJIS)),
    )
del _unknown_phases


async def _add_phase_reaction(
    slack_desk: SlackDeskClientOps, channel: str, ts: str, phase: str
) -> None:
    """Add the reaction for *phase* if the user hasn't suppressed it.

    Used by one-shot emoji-ack sites outside ``StatusReactionController``
    (e.g. ``!command`` handlers).  Honours ``slack.reactions`` ``null``
    suppression sentinels.
    """
    emoji = _PHASE_EMOJIS.get(phase)
    if emoji is None:
        return
    await slack_desk.add_reaction(channel, ts, emoji)


_STALL_EMOJI_SOFT = "yawning_face"
_STALL_EMOJI_HARD = "fearful"

_STALL_SOFT_SECS = 15.0
_STALL_HARD_SECS = 45.0
_PHASE_DEBOUNCE_SECS = 0.7

_TERMINAL_PHASES = frozenset({"done", "error"})
_IMMEDIATE_PHASES = frozenset({"queued"})

_CODING_TOOLS: frozenset[str] = frozenset(
    {"Bash", "Write", "Edit", "Read", "Glob", "Grep", "NotebookEdit"}
)
_WEB_TOOLS: frozenset[str] = frozenset({"WebFetch", "WebSearch", "Browser"})

_CODING_KINDS: frozenset[str] = frozenset(t.lower() for t in _CODING_TOOLS)
_WEB_KINDS: frozenset[str] = frozenset(t.lower() for t in _WEB_TOOLS)


def _tool_to_phase(tool_name: str, tool_kind: str = "") -> str:
    """Map a tool name/kind to a reaction phase."""
    kind_lower = tool_kind.lower()
    if kind_lower:
        if kind_lower in _CODING_KINDS:
            return "coding"
        if kind_lower in _WEB_KINDS:
            return "browsing"
    # Extract base tool name for MCP tools (mcp__my-server__Bash → Bash)
    base = tool_name.split("__")[-1] if "__" in tool_name else tool_name
    if base in _CODING_TOOLS:
        return "coding"
    if base in _WEB_TOOLS:
        return "browsing"
    return "tool"


class StatusReactionController:
    """Phase-aware Slack reaction controller with debounce and stall detection.

    Intermediate phases are debounced so rapid tool transitions don't spam
    the Slack API.  A stall watchdog adds yawning/fearful reactions when
    the agent appears stuck.
    """

    def __init__(self, slack_desk: SlackDeskClientOps, channel: str, ts: str, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._slack_desk = slack_desk
        self._channel = channel
        self._ts = ts
        self._loop = asyncio.get_running_loop()

        self._current_emoji: str | None = None
        self._pending_phase: str | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._stall_soft_handle: asyncio.TimerHandle | None = None
        self._stall_hard_handle: asyncio.TimerHandle | None = None
        self._stall_emoji: str | None = None
        self._stall_paused = False
        self._finalized = False

    # ── public API ──────────────────────────────────────────────────

    def set_phase(self, phase: str) -> None:
        """Request a phase transition (may be debounced)."""
        if self._finalized or not self._enabled:
            return

        if phase in _TERMINAL_PHASES:
            self.finalize(error=(phase == "error"))
            return

        if phase in _IMMEDIATE_PHASES:
            self._cancel_debounce()
            emoji = _PHASE_EMOJIS.get(phase, phase)
            asyncio.ensure_future(self._swap_emoji(emoji))
            self._reset_stall_watchdog()
            return

        # Intermediate phase — debounce
        self._pending_phase = phase
        self._cancel_debounce()
        self._debounce_handle = self._loop.call_later(_PHASE_DEBOUNCE_SECS, self._fire_debounce)

    def on_progress(self) -> None:
        """Reset stall watchdog — call on any LLM/tool activity."""
        if not self._finalized and not self._stall_paused and self._enabled:
            self._reset_stall_watchdog()

    def pause_stall_watchdog(self) -> None:
        """Pause stall detection (e.g. waiting for user approval)."""
        self._stall_paused = True
        self._cancel_stall_timers()

    def resume_stall_watchdog(self) -> None:
        """Resume stall detection after a pause."""
        self._stall_paused = False
        if not self._finalized and self._enabled:
            self._reset_stall_watchdog()

    def finalize(self, error: bool = False) -> None:
        """Swap to terminal emoji. Idempotent."""
        if self._finalized or not self._enabled:
            return
        self._finalized = True
        self._cancel_debounce()
        self._cancel_stall_timers()
        # Clean up stall emoji before setting terminal
        asyncio.ensure_future(self._do_finalize(error))

    # ── internal ────────────────────────────────────────────────────

    async def _do_finalize(self, error: bool) -> None:
        if self._stall_emoji:
            try:
                await self._slack_desk.remove_reaction(self._channel, self._ts, self._stall_emoji)
            except Exception:
                pass
            self._stall_emoji = None
        terminal = _PHASE_EMOJIS["error" if error else "done"]
        await self._swap_emoji(terminal)

    def _fire_debounce(self) -> None:
        """Timer callback — bridge to async."""
        asyncio.ensure_future(self._apply_pending())

    async def _apply_pending(self) -> None:
        if self._finalized or self._pending_phase is None:
            return
        emoji = _PHASE_EMOJIS.get(self._pending_phase, self._pending_phase)
        self._pending_phase = None
        await self._swap_emoji(emoji)
        self._reset_stall_watchdog()

    async def _swap_emoji(self, new_emoji: str | None) -> None:
        """Remove old reaction and add new one (skip if same).

        ``new_emoji=None`` means the phase is suppressed by config: remove
        any previously-applied reaction but do not add a replacement.
        """
        if new_emoji == self._current_emoji:
            return
        old = self._current_emoji
        self._current_emoji = new_emoji
        if old:
            try:
                await self._slack_desk.remove_reaction(self._channel, self._ts, old)
            except Exception:
                pass
        if new_emoji is None:
            return
        try:
            await self._slack_desk.add_reaction(self._channel, self._ts, new_emoji)
        except Exception:
            pass

    def _cancel_debounce(self) -> None:
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
            self._debounce_handle = None

    def _cancel_stall_timers(self) -> None:
        if self._stall_soft_handle is not None:
            self._stall_soft_handle.cancel()
            self._stall_soft_handle = None
        if self._stall_hard_handle is not None:
            self._stall_hard_handle.cancel()
            self._stall_hard_handle = None

    def _reset_stall_watchdog(self) -> None:
        if not self._enabled:
            return
        self._cancel_stall_timers()
        # Remove existing stall emoji
        if self._stall_emoji:
            emoji_to_remove = self._stall_emoji
            self._stall_emoji = None
            asyncio.ensure_future(self._remove_stall_emoji(emoji_to_remove))
        if self._stall_paused or self._finalized:
            return
        self._stall_soft_handle = self._loop.call_later(_STALL_SOFT_SECS, self._on_stall_soft)
        self._stall_hard_handle = self._loop.call_later(_STALL_HARD_SECS, self._on_stall_hard)

    async def _remove_stall_emoji(self, emoji: str) -> None:
        try:
            await self._slack_desk.remove_reaction(self._channel, self._ts, emoji)
        except Exception:
            pass

    def _on_stall_soft(self) -> None:
        asyncio.ensure_future(self._add_stall_emoji(_STALL_EMOJI_SOFT))

    def _on_stall_hard(self) -> None:
        asyncio.ensure_future(self._add_stall_emoji(_STALL_EMOJI_HARD))

    async def _add_stall_emoji(self, emoji: str) -> None:
        if self._finalized:
            return
        # Remove previous stall emoji if upgrading
        if self._stall_emoji and self._stall_emoji != emoji:
            try:
                await self._slack_desk.remove_reaction(self._channel, self._ts, self._stall_emoji)
            except Exception:
                pass
        self._stall_emoji = emoji
        try:
            await self._slack_desk.add_reaction(self._channel, self._ts, emoji)
        except Exception:
            pass


# Trust state
# trust: auto-approve tools for a specific Slack thread (via Trust button) —
#   Slack-specific, owned here.
# yolo: auto-approve all tools globally — process-global trust posture owned by
#   gideon.trust_mode (single source of truth). The Slack !yolo command
#   delegates there; _trusted_sessions is cleared when YOLO turns off via the
#   registered on_disable callback below.
_trusted_sessions: set[str] = set()
_YOLO_TTL_SECS = trust_mode.YOLO_CHANNEL_TTL_SECS  # 30 min for !yolo on command
_YOLO_DASHBOARD_TTL_SECS = trust_mode.YOLO_DASHBOARD_TTL_SECS  # 6h dashboard button


def _clear_trusted_sessions_on_yolo_disable(reason: str) -> None:
    """trust_mode callback — drop per-thread trust when global YOLO turns off."""
    _trusted_sessions.clear()


trust_mode.register_on_disable(_clear_trusted_sessions_on_yolo_disable)

# Allowed user IDs for Slack access (set by gateway at startup).
_allowed_users: set[str] = set()


# ── Voice reply state ──
@dataclass
class _VoiceConfig:
    """Per-session and global voice reply *runtime* state.

    The TTS voice + speaking speed are resolved from the unified model store
    (``active_models.json`` ``tts`` + ``use_case_settings/tts.json``) via
    ``tts.registry.active_voice_params`` — only liveness/session toggles live here.
    """

    sessions: set[str] = None  # type: ignore[assignment]  # threads with voice on
    global_enabled: bool = False
    auto_speak: bool = False
    # If True, a message carrying voice input (a transcribed voice memo)
    # automatically receives a voice reply, even without `!voice on`. The
    # config-load default follows ``enabled`` (see ``set_orch_cfg``); the
    # in-memory default below is False so an unconfigured ``_VoiceConfig``
    # behaves the same as a default-config user (``enabled=false``).
    auto_reply_to_voice: bool = False

    def __post_init__(self) -> None:
        self.sessions = self.sessions or set()


_vc = _VoiceConfig()

# Primary owner ID — for owner-only commands like !agent.
_owner_id: str = ""

# Tracked channel IDs for member_joined_channel monitoring.
_tracking_channels: set[str] = set()
_open_channels: set[str] = set()

# Live reference to the orchestrator's config — set by events.py, reloaded
# after !channel writes so activation changes take effect immediately.
_orch_cfg: AppConfig | None = None

# Dashboard state reference for pushing refresh events (set by gateway).
_dashboard_state: object | None = None
_gateway_services = None


_cached_default_agent: str | None = None  # None = not yet loaded from disk

# Per-thread agent overrides: session_key → agent name.
# Set via !ta command (thread-agent).
_thread_agents: dict[str, str] = {}

# Temporary (blank-slate) + incognito thread modes are generic session
# restrictions owned by gideon.session_restrictions (single source of
# truth, read by core memory-gating). These thin wrappers keep the Slack call
# sites/naming; the state lives in core.
_RESTRICTED_WRITE_MSG = "Memory writes are not allowed in this session mode."


def _mark_temporary(key: str) -> None:
    session_restrictions.mark_temporary(key)


def is_thread_temporary(session_key: str) -> bool:
    """Public check — used by API handlers to gate memory writes."""
    return session_restrictions.is_temporary(session_key)


def _mark_incognito(key: str) -> None:
    session_restrictions.mark_incognito(key)


def is_thread_incognito(session_key: str) -> bool:
    """Public check — used by API handlers."""
    return session_restrictions.is_incognito(session_key)


def _is_slack_desk_restricted(session_key: str) -> bool:
    """Return True if this Slack session should skip memory writes."""
    return session_restrictions.is_restricted(session_key)


_INCOGNITO_TOKEN_RE = re.compile(r"(?<!\S)!incognito(?!\S)", re.IGNORECASE)


def _strip_incognito_token(text: str) -> tuple[str, bool]:
    """Remove standalone ``!incognito`` token from *text*."""
    new, n = _INCOGNITO_TOKEN_RE.subn("", text)
    if not n:
        return text, False
    return " ".join(new.split()), True


_TEMPORARY_TOKEN_RE = re.compile(r"(?<!\S)!temporary(?!\S)", re.IGNORECASE)


def _strip_temporary_token(text: str) -> tuple[str, bool]:
    """Remove standalone ``!temporary`` token from *text*.

    Returns ``(cleaned_text, found)`` where *found* is True if the token
    was present.  The cleaned text has the token removed and excess
    whitespace collapsed.
    """
    new, n = _TEMPORARY_TOKEN_RE.subn("", text)
    if not n:
        return text, False
    return " ".join(new.split()), True


async def _apply_temporary_modifier(
    session_key: str,
    user_id: str,
    channel: str,
    slack_desk: SlackDeskClientOps,
    sessions: SessionManager,
    reply_ts: str,
) -> None:
    """Mark a session as temporary and notify the user (idempotent)."""
    if session_restrictions.is_temporary(session_key):
        return
    _mark_temporary(session_key)
    sel().log_api_access(
        caller=user_id,
        operation="slack.temporary_mode",
        outcome="allowed",
        source="slack",
        resources=f"{channel}:{session_key}",
    )
    # Register thread so follow-up messages pass the in_active_thread
    # gate in mention/observe channels without needing another @mention.
    sessions.set_channel_link(session_key, session_key, channel)
    await slack_desk.post_message(
        channel,
        "🔒 Temporary mode ON — this thread won't read or save memory.",
        reply_ts,
    )


async def _apply_incognito_modifier(
    session_key: str,
    user_id: str,
    channel: str,
    slack_desk: SlackDeskClientOps,
    sessions: SessionManager,
    reply_ts: str,
) -> None:
    """Mark a session as incognito and notify the user (idempotent)."""
    if session_restrictions.is_incognito(session_key):
        return
    _mark_incognito(session_key)
    sel().log_api_access(
        caller=user_id,
        operation="slack.incognito_mode",
        outcome="allowed",
        source="slack",
        resources=f"{channel}:{session_key}",
    )
    sessions.set_channel_link(session_key, session_key, channel)
    await slack_desk.post_message(
        channel,
        "🕶️ Incognito mode ON — this thread can read memory but won't save anything.",
        reply_ts,
    )


# Tracks Slack threads that already have a title (auto or manual).
# Bounded LRU to prevent unbounded growth in long-running bots.

_TITLED_THREADS_MAX = 10_000
_titled_threads: OrderedDict[str, str | None] = OrderedDict()


def _mark_titled(key: str, kind: str | None = None) -> None:
    """Add key to the bounded LRU title tracker."""
    _titled_threads[key] = kind
    _titled_threads.move_to_end(key)
    if len(_titled_threads) > _TITLED_THREADS_MAX:
        _titled_threads.popitem(last=False)


# Background tasks kept alive to prevent GC mid-execution.
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


# Review mode: stores draft text keyed by "channel|thread_ts|uuid" for button/modal
# handlers. Each entry includes the *requester* user_id so handlers can authorize the
# requester (in addition to bot owner) to act on their own drafts.
# Bounded with TTL to prevent memory leaks from abandoned drafts.
_REVIEW_PLACEHOLDER_TS = "review_placeholder"
_REVIEW_DRAFT_TTL = 3600  # 1 hour
_REVIEW_DRAFT_MAX = 1024
# key → (draft, requester_user_id, timestamp)
_review_drafts: dict[str, tuple[str, str, float]] = {}


def _review_drafts_get(key: str) -> tuple[str, str]:
    """Get (draft, requester_user_id), returning ("","") if missing or expired."""
    entry = _review_drafts.get(key)
    if entry is None:
        return "", ""
    draft, requester, ts = entry
    if time.monotonic() - ts > _REVIEW_DRAFT_TTL:
        _review_drafts.pop(key, None)
        return "", ""
    return draft, requester


def _review_drafts_set(key: str, draft: str, requester_user_id: str) -> None:
    """Store a draft with TTL + requester id, evicting oldest if at capacity."""
    now = time.monotonic()
    # Evict expired entries
    expired = [k for k, (_, _, ts) in _review_drafts.items() if now - ts > _REVIEW_DRAFT_TTL]
    for k in expired:
        _review_drafts.pop(k, None)
    # Evict oldest if still at capacity
    if len(_review_drafts) >= _REVIEW_DRAFT_MAX:
        oldest_key = min(_review_drafts, key=lambda k: _review_drafts[k][2])
        _review_drafts.pop(oldest_key, None)
    _review_drafts[key] = (draft, requester_user_id, now)


def _review_drafts_pop(key: str) -> tuple[str, str]:
    """Pop (draft, requester_user_id), returning ("","") if missing or expired."""
    entry = _review_drafts.pop(key, None)
    if entry is None:
        return "", ""
    draft, requester, ts = entry
    if time.monotonic() - ts > _REVIEW_DRAFT_TTL:
        return "", ""
    return draft, requester


def _get_default_agent() -> str:
    """Read persisted default agent, cached to avoid disk I/O on every message."""
    global _cached_default_agent
    if _cached_default_agent is None:
        _cached_default_agent = AppConfig.load().default_agent
    return _cached_default_agent



def _resolve_agent_name(name: str) -> str | None:
    """Resolve an agent name via suffix matching against installed agents.

    Returns the resolved name, or None if not found.
    """
    agents_dir = Path.home() / ".gideon" / "agents"
    jsons = (
        sorted(agents_dir.glob("*.json"), key=lambda f: (len(f.stem), f.stem))
        if agents_dir.is_dir()
        else []
    )
    match = next(
        (f for f in jsons if f.stem == name or f.stem.endswith(f"-{name}")),
        None,
    )
    if not match:
        return None
    safe = validate_file_path(str(match))
    if not safe:
        return None
    try:
        return json.loads(Path(safe).read_text(encoding="utf-8")).get("name", match.stem)
    except (json.JSONDecodeError, OSError):
        return match.stem


def _set_default_agent(name: str) -> None:
    """Persist default agent to config (shared with dashboard)."""
    global _cached_default_agent
    path = config_path()
    if is_sensitive_path(str(path)):
        raise ValueError(f"Refusing to write to sensitive path: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    data["default_agent"] = name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from gideon.sdk.channel import atomic_write

        atomic_write(path, json.dumps(data, indent=2) + "\n")
    except OSError as e:
        raise ValueError(f"Failed to write config: {e}") from e
    _cached_default_agent = name


def _persist_channel_config(
    channel_id: str,
    activation: str | None = None,
    agent: str | None = None,
) -> None:
    """Update a single channel's config in the APP's own store (merge, not overwrite)."""
    from gideon.sdk.channel import ProviderSettings

    cur = ProviderSettings.load("gideonai-slack-desk")
    channels = dict(cur.get("channels", {}))
    ch = dict(channels.get(channel_id, {}))
    if activation is not None:
        ch["activation"] = activation
    if agent is not None:
        ch["agent"] = agent
    channels[channel_id] = ch
    ProviderSettings.update("gideonai-slack-desk", {"channels": channels})


class _PendingApproval:
    __slots__ = ("provider", "request_id", "session_key", "future")

    def __init__(self, provider: ModelProvider, request_id: str | int, session_key: str = "") -> None:
        self.provider = provider
        self.request_id = request_id
        self.session_key = session_key
        self.future: asyncio.Future[str] = asyncio.get_running_loop().create_future()


_OUTCOME_APPROVED = "approved"
_OUTCOME_REJECTED = "rejected"

# Block Kit action IDs
_ACTION_APPROVE = "approve_tool"
_ACTION_TRUST = "trust_tool"
_ACTION_REJECT = "reject_tool"


def set_allowed_users(user_ids: set[str]) -> None:
    """Set the allowed user IDs for Slack access (called by gateway)."""
    global _allowed_users
    _allowed_users = user_ids


def set_owner_id(owner_id: str) -> None:
    """Set the primary owner ID for owner-only commands (called by gateway)."""
    global _owner_id
    _owner_id = owner_id


def get_owner_id() -> str:
    """Current owner id ('' when unclaimed)."""
    return _owner_id


def claim_owner(user_id: str) -> bool:
    """First-contact owner claim: when no owner is set yet, adopt *user_id* as the
    owner and persist it (process env + ~/.gideon/.env) so it survives restart.

    Trust-on-first-use bootstrap for a fresh Slack install with no preset owner —
    the FIRST human to message the bot becomes its sole authorized owner. A no-op
    once an owner exists (returns False), so it can never transfer ownership.
    Returns True iff the claim happened.
    """
    global _owner_id, _allowed_users
    if _owner_id or not user_id:
        return False
    _owner_id = user_id
    _allowed_users = {user_id}
    try:
        from gideon.sdk.channel import CRED_OWNER_ID, save_credential
        save_credential(CRED_OWNER_ID, user_id)
    except Exception:
        logger.warning("Failed to persist auto-claimed Slack owner", exc_info=True)
    logger.info("Slack owner auto-claimed on first contact: %s", user_id)
    # EA-7: the claimed owner is slack's ONE allowed sender — seed core's
    # channel_trust store so the guarded inbound door admits them.
    try:
        from gideon.sdk.channel import allow_sender

        allow_sender("slack", user_id, via="owner")
    except Exception:
        logger.warning("Failed to seed channel_trust with claimed Slack owner", exc_info=True)
    return True


def set_yolo_mode(enabled: bool) -> None:
    """Set YOLO mode at startup from config (called by gateway). No expiry."""
    if enabled:
        trust_mode.enable_yolo(from_config=True)
    else:
        trust_mode.disable_yolo()


def set_orch_cfg(cfg: AppConfig) -> None:
    """Store a live reference to the orchestrator's config (called by events.py).

    Voice *behavior* toggles come from ``use_case_settings/tts.json`` (the voice
    model + speed are resolved per-reply from the unified store).
    """
    global _orch_cfg
    _orch_cfg = cfg
    from gideon.sdk.channel import load_use_case_settings

    _vr = load_use_case_settings("tts")
    _enabled = bool(_vr.get("enabled", False))
    if _enabled:
        _vc.global_enabled = True
    _vc.auto_speak = bool(_vr.get("auto_speak", False))
    # ``auto_reply_to_voice`` defaults to ``enabled``'s value: users with
    # explicit ``enabled=false`` keep the existing zero-voice behavior, and
    # users who turn voice on globally also get symmetric voice-in/voice-out
    # without needing to set a second flag.
    _vc.auto_reply_to_voice = bool(_vr.get("auto_reply_to_voice", _enabled))


def set_dashboard_state(state: object) -> None:
    """Store dashboard state reference for push_refresh (called by gateway)."""
    global _dashboard_state
    _dashboard_state = state


def _track_linked_channel(channel_id: str) -> None:
    """Record a just-linked channel in core's channel_trust store (EA-7).

    Linking a thread is the owner's explicit invitation for the agent to converse
    there, so the guarded door must admit the thread's messages — for a group
    channel that means the channel is tracked. DMs need no tracking: the owner is
    an allowed sender and a DM passes the door's DM branch directly."""
    if not channel_id or channel_id.startswith("D"):
        return
    try:
        from gideon.sdk.channel import is_tracked_channel as _ct_is_tracked
        from gideon.sdk.channel import track as _ct_track

        if not _ct_is_tracked("slack", channel_id):
            _ct_track("slack", channel_id)
    except Exception:
        logger.warning("Failed to track linked channel in channel_trust", exc_info=True)


def set_gateway_services(services: object) -> None:
    """Store the GatewayServices handle (called by events.py at socket init).

    The linked-thread intercept routes through the platform's guarded inbound
    door (``services.deliver_channel_inbound``, EA-7) instead of hand-rolling
    the session routing — this is how the handler reaches that door."""
    global _gateway_services
    _gateway_services = services


def _reload_orch_cfg() -> None:
    """Reload the app's SlackDeskSettings after !channel writes so changes take effect
    immediately (channel activation reads go through settings.get_settings())."""
    from slack_desk_runtime.settings import reload_settings

    reload_settings()


def is_owner(user_id: str) -> bool:
    """Check if *user_id* is the primary owner (with W/U prefix cross-match)."""
    if not _owner_id or not user_id:
        return False
    if user_id == _owner_id:
        return True
    return user_id.replace("W", "U", 1) == _owner_id or user_id.replace("U", "W", 1) == _owner_id


def disable_yolo() -> None:
    """Disable YOLO mode (global auto-approve). Delegates to trust_mode; the
    registered callback clears ``_trusted_sessions``."""
    trust_mode.disable_yolo()


def enable_yolo_with_ttl(ttl_secs: int) -> None:
    """Enable YOLO mode with a specific TTL.

    No-op when config-level YOLO is already active (permanent, no TTL).
    """
    trust_mode.enable_yolo(ttl_secs=ttl_secs)


def is_yolo_mode() -> bool:
    """Return whether YOLO mode is currently active (auto-expires unless config-driven)."""
    return trust_mode.is_yolo_active()


def is_allowed_user(user_id: str) -> bool:
    """Is *user_id* the owner, or on the operator's allowlist?

    **Fail-CLOSED, and deny-by-default.** An empty ``user_id`` is refused; with no owner
    and an empty allowlist the set is empty and NOBODY is authorized. The only way this
    returns True for a non-owner is an id an operator explicitly wrote down — in the
    dashboard's "Allowed Users", or by clicking Approve on an in-Slack request. Both
    persist to the same ``allowed_users`` list; :class:`~slack_desk_runtime.runtime.SlackDeskRuntime`
    seeds this set from it at boot and the interaction handlers keep it in step.

    This used to ignore ``_allowed_users`` entirely and answer :func:`is_owner`, with the
    comment "multi-user access is disabled for security" (#953). Three surfaces were
    lying as a result: the dashboard's Allowed Users field, the in-Slack Approve button
    (which DMs the approved user "You can now message me!" and then refused them), and the
    two-tier command gate right here in this module, whose ``is_owner(u) or
    is_allowed_user(u)`` sites exist precisely so that a non-owner CAN be allowed.

    Note the tier that non-owner grant carries: ``!dashboard`` is in the "any allowed
    user" tier, so an allowlisted user can have a dashboard session link DMed to them.
    That is the app's pre-existing design (see the tier comment in ``handle_message``),
    not something restored here — but it is what "add this person to the allowlist" now
    means again, so it belongs in the docstring rather than in a surprise.
    """
    if not user_id:
        return False
    if is_owner(user_id):
        return True
    return user_id in _allowed_users


def set_tracking_channels(channel_ids: set[str]) -> None:
    """Set the tracked channel IDs (called by gateway/interactions)."""
    global _tracking_channels
    _tracking_channels = channel_ids


def set_open_channels(channel_ids: set[str]) -> None:
    """Set channel IDs where all users are authorized (no allowlist needed)."""
    global _open_channels
    _open_channels = channel_ids


def is_open_channel(channel_id: str) -> bool:
    """Open channels are disabled — multi-user access is blocked for security."""
    return False


def is_tracked_channel(channel_id: str) -> bool:
    """Check if *channel_id* is in the tracking set."""
    return bool(channel_id and channel_id in _tracking_channels)


async def _safe_voice_reply(
    slack_desk: SlackDeskClientOps,
    channel: str,
    thread_ts: str,
    text: str,
) -> None:
    """Fire-and-forget voice reply.  Never raises."""
    from gideon.sdk.channel import active_voice_params

    params = active_voice_params()
    if params is None:
        logger.debug("Voice reply skipped — no TTS voice selected")
        return
    try:
        await _voice_reply_fn(
            slack_desk,
            channel,
            thread_ts,
            text,
            provider=params["provider"],
            voice=params["voice"],
            speed=params["speed"],
            speech_voice=params["speech_voice"],
        )
    except Exception:
        logger.debug("Voice reply failed", exc_info=True)


async def _handle_slash_command(
    cmd_text: str,
    slack_desk: SlackDeskClientOps,
    sessions: SessionManager,
    channel: str,
    reply_ts: str,
    msg_ts: str,
    session_key: str,
    user_id: str,
    conversation_log: ConversationLog | None = None,
) -> str | None:
    """Dispatch owner-only ``!commands``.  Returns a string (even empty) if handled, None if not."""

    cmd = cmd_text.split()[0].lower()

    # ── Deprecation warning for all bang commands ──
    slash_equiv = _BANG_TO_SLASH.get(cmd)
    if slash_equiv:
        logger.warning("Deprecated bang command %s used — suggest %s", cmd, slash_equiv)
        warn_block = deprecation_warning_block(cmd, slash_equiv)
        await slack_desk.post_blocks(channel, [warn_block], f"{cmd} is deprecated", reply_ts)

    # ── !yolo on / !yolo off ──
    if cmd == "!yolo":
        parts = cmd_text.split()
        yolo_active = is_yolo_mode()  # triggers expiry check
        if len(parts) >= 2 and parts[1].lower() == "off":
            if yolo_active:
                disable_yolo()
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.yolo_mode",
                    outcome="allowed",
                    source="slack",
                    resources="yolo_off",
                )
                await slack_desk.post_message(channel, "🔒 YOLO mode disabled.", reply_ts)
            else:
                await slack_desk.post_message(channel, "YOLO mode is already off.", reply_ts)
        elif len(parts) >= 2 and parts[1].lower() == "on":
            if trust_mode.yolo_from_config():
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.yolo_mode",
                    outcome="noop_config_permanent",
                    source="slack",
                    resources="yolo_on",
                )
                await slack_desk.post_message(channel, "🟢 YOLO mode is already permanently ON from config (`agent.yolo=true`).", reply_ts)
            elif not yolo_active:
                enable_yolo_with_ttl(_YOLO_TTL_SECS)
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.yolo_mode",
                    outcome="allowed",
                    source="slack",
                    resources="yolo_on",
                )
                await slack_desk.post_message(channel, f"🔓 YOLO mode enabled (auto-expires in {_YOLO_TTL_SECS // 60}min).", reply_ts)
            else:
                await slack_desk.post_message(channel, "YOLO mode is already on.", reply_ts)
        else:
            status = "ON 🔓" if yolo_active else "OFF 🔒"
            await slack_desk.post_message(
                channel, f"YOLO mode: *{status}*. Use `!yolo on` / `!yolo off`.", reply_ts
            )
        return ""

    # ── !stop — defensive fallback (normally intercepted in events.py
    #    _route_message before handle_message is called) ──
    if cmd == "!stop":
        has_session = sessions.has_session(session_key)
        if not has_session:
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!stop",
                tool_kind="command",
                outcome="no_session",
                metadata={"user": user_id, "channel": channel},
            )
            await slack_desk.post_message(channel, "Nothing running.", reply_ts)
            return ""

        # Post ephemeral "Stopping…" block with Kill Now button
        from slack_desk_runtime.blocks import build_stopping_blocks

        await slack_desk.post_ephemeral(
            channel,
            user_id,
            "Stopping…",
            blocks=build_stopping_blocks(session_key),
            thread_ts=reply_ts,
        )

        async def _on_soft() -> None:
            await slack_desk.post_message(channel, "⏹ Execution stopped.", reply_ts)

        async def _on_hard() -> None:
            await slack_desk.post_message(
                channel, "⛔ Execution stopped — session reset.", reply_ts
            )

        outcome = await sessions.stop_turn(
            session_key, on_soft=_on_soft, on_hard=_on_hard
        )
        # If stop_turn returned "idle" (no active turn), neither callback
        # fired — dismiss the stale "Stopping…" ephemeral explicitly.
        if outcome == "idle":
            await slack_desk.post_message(channel, "Nothing running.", reply_ts)
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="!stop",
            tool_kind="command",
            outcome=outcome,
            metadata={"user": user_id, "channel": channel},
        )
        return ""

    # ── !voice on/off/global ──
    if cmd == "!voice":
        parts = cmd_text.split()
        arg = parts[1].lower() if len(parts) >= 2 else ""
        if arg == "on":
            _vc.sessions.add(session_key)
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!voice",
                tool_kind="command",
                outcome="voice_on",
                metadata={"user": user_id, "channel": channel},
            )
            await slack_desk.post_message(channel, "\U0001f50a Voice ON.", reply_ts)
        elif arg == "off":
            _vc.sessions.discard(session_key)
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!voice",
                tool_kind="command",
                outcome="voice_off",
                metadata={"user": user_id, "channel": channel},
            )
            await slack_desk.post_message(channel, "\U0001f507 Voice OFF.", reply_ts)
        elif arg == "global":
            _vc.global_enabled = not _vc.global_enabled
            state = "ON \U0001f50a" if _vc.global_enabled else "OFF \U0001f507"
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!voice",
                tool_kind="command",
                outcome="voice_global_" + ("on" if _vc.global_enabled else "off"),
                metadata={"user": user_id, "channel": channel},
            )
            await slack_desk.post_message(channel, f"Voice global: *{state}*", reply_ts)
        else:
            on = session_key in _vc.sessions or _vc.global_enabled
            await slack_desk.post_message(
                channel,
                f"\U0001f50a Voice: *{'ON' if on else 'OFF'}*\n"
                "`!voice on` `!voice off` `!voice global`",
                reply_ts,
            )
        await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
        return ""

    # ── !agent <name> / !agent off — always global ──
    if cmd == "!agent":
        parts = cmd_text.split()
        if len(parts) == 1:
            name = _get_default_agent() or "gideon"
            await slack_desk.post_message(
                channel,
                f"Current agent: *{name}*. Usage: `!agent <name>` or `!agent off`",
                reply_ts,
            )
            return ""
        if len(parts) != 2:
            await slack_desk.post_message(channel, "Usage: `!agent <name>` or `!agent off`", reply_ts)
            return ""
        agent_name = parts[1]
        if agent_name.lower() in ("default", "off"):
            try:
                _set_default_agent("")
            except ValueError as e:
                await slack_desk.post_message(channel, f"❌ {e}", reply_ts)
                return ""
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!agent",
                tool_kind="command",
                outcome="agent_reset",
                metadata={"user": user_id, "channel": channel},
            )
            await sessions.remove(session_key)
            await slack_desk.post_message(channel, "🔄 Reset to default agent.", reply_ts)
            await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
            return ""
        resolved = _resolve_agent_name(agent_name)
        if not resolved:
            agents_dir = Path.home() / ".gideon" / "agents"
            jsons = sorted(agents_dir.glob("*.json")) if agents_dir.is_dir() else []
            names = ", ".join(sorted(f.stem for f in jsons)) if jsons else "(none found)"
            await slack_desk.post_message(
                channel, f"❌ Unknown agent `{agent_name}`. Available: {names}", reply_ts
            )
            return ""
        try:
            _set_default_agent(resolved)
        except ValueError as e:
            await slack_desk.post_message(channel, f"❌ {e}", reply_ts)
            return ""
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="!agent",
            tool_kind="command",
            outcome="agent_switch",
            metadata={"agent": resolved, "user": user_id, "channel": channel},
        )
        await sessions.remove(session_key)
        await slack_desk.post_message(channel, f"🔄 Switched to agent: *{resolved}*", reply_ts)
        await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
        return ""

    # ── !dashboard [duration] ──
    if cmd == "!dashboard":
        from gideon.sdk.channel import parse_duration
        from slack_desk_runtime.allowlist import send_dashboard_link

        parts = cmd_text.split()
        ttl = 3600
        if len(parts) >= 2:
            parsed = parse_duration(parts[1])
            if parsed is None:
                await slack_desk.post_message(
                    channel,
                    "Usage: `!dashboard [<N>h|<N>m]` — e.g. `!dashboard 2h`, `!dashboard 30m`",
                    reply_ts,
                )
                return ""
            ttl = parsed

        url = await send_dashboard_link(slack_desk, user_id, ttl)
        if url:
            await slack_desk.post_message(channel, "🔗 Dashboard link sent via DM.", reply_ts)
        else:
            await slack_desk.post_message(channel, "❌ Failed to send dashboard link.", reply_ts)
        return ""

    # ── !link-to-dashboard -- import Slack thread into dashboard ──
    if cmd == "!link-to-dashboard":
        if not is_allowed_user(user_id):
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="link_to_dashboard", tool_kind="command",
                outcome="denied",
                metadata={"user_id": user_id, "channel": channel, "reason": "not_allowed_user"},
            )
            await slack_desk.post_message(channel, "Not authorized.", reply_ts)
            return ""
        if not _dashboard_state or not hasattr(_dashboard_state, "get_or_create_session"):
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="link_to_dashboard", tool_kind="command",
                outcome="failure",
                metadata={"user_id": user_id, "channel": channel, "reason": "no_dashboard"},
            )
            await slack_desk.post_message(channel, "Dashboard not available.", reply_ts)
            return ""
        if reply_ts == msg_ts:
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="link_to_dashboard", tool_kind="command",
                outcome="failure",
                metadata={"user_id": user_id, "channel": channel, "reason": "not_in_thread"},
            )
            await slack_desk.post_message(channel, "Use this command inside a thread to import it.", reply_ts)
            return ""
        # Fetch thread history and import to dashboard
        from slack_desk_runtime.interactions import _import_thread_to_session
        session = await _import_thread_to_session(slack_desk, _dashboard_state, channel, reply_ts)
        if not session:
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="link_to_dashboard", tool_kind="command",
                outcome="failure",
                metadata={"channel": channel, "thread_ts": reply_ts, "reason": "empty_thread"},
            )
            await slack_desk.post_message(channel, "Could not fetch thread history.", reply_ts)
            return ""
        _track_linked_channel(channel)
        sel().log_tool_invocation(
            session_key=session.key, agent="gideon", source="slack",
            tool_name="link_to_dashboard", tool_kind="command",
            outcome="success",
            metadata={"session": session.key, "channel": channel, "thread_ts": reply_ts, "msg_count": len(session.messages)},
        )
        await slack_desk.post_message(
            channel,
            f"Imported {len(session.messages)} messages to dashboard session *{session.key}*. Thread is now linked.",
            reply_ts,
        )
        return ""

    # ── !ta <name> / !ta off — thread-scoped agent ──
    if cmd == "!ta":
        parts = cmd_text.split()
        if len(parts) < 2:
            current = _thread_agents.get(session_key, "")
            if current:
                await slack_desk.post_message(
                    channel,
                    f"Thread agent: *{current}*. `!ta off` to reset.",
                    reply_ts,
                )
            else:
                await slack_desk.post_message(
                    channel,
                    "No thread agent set. Usage: `!ta <name>` or `!ta off`",
                    reply_ts,
                )
            return ""
        agent_name = parts[1]
        if agent_name.lower() in ("default", "off"):
            _thread_agents.pop(session_key, None)
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!ta",
                tool_kind="command",
                outcome="agent_reset",
                metadata={"user": user_id, "channel": channel, "scope": "thread"},
            )
            await sessions.remove(session_key)
            await slack_desk.post_message(channel, "🔄 Thread agent reset.", reply_ts)
            await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
            return ""
        resolved = _resolve_agent_name(agent_name)
        if not resolved:
            agents_dir = Path.home() / ".gideon" / "agents"
            jsons = sorted(agents_dir.glob("*.json")) if agents_dir.is_dir() else []
            names = ", ".join(sorted(f.stem for f in jsons)) if jsons else "(none found)"
            await slack_desk.post_message(
                channel, f"❌ Unknown agent `{agent_name}`. Available: {names}", reply_ts
            )
            return ""
        _thread_agents[session_key] = resolved
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="!ta",
            tool_kind="command",
            outcome="agent_switch",
            metadata={"agent": resolved, "user": user_id, "channel": channel, "scope": "thread"},
        )
        await sessions.remove(session_key)
        await slack_desk.post_message(channel, f"🔄 Thread agent: *{resolved}*", reply_ts)
        await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
        return ""

    # ── !allowlist — multi-user access disabled ──
    if cmd == "!allowlist":
        await slack_desk.post_message(
            channel,
            "⛔ Multi-user access is disabled for security. Only the owner can use Gideon via Slack.",
            reply_ts,
        )
        return ""

    # ── !channel always|mention|observe|off / !channel agent <name> (owner-only) ──
    if cmd == "!channel":
        if not is_owner(user_id):
            sel().log_api_access(
                caller=user_id,
                operation="slack.channel_config",
                outcome="denied",
                source="slack",
                resources=channel,
                error="not owner",
            )
            await slack_desk.post_message(channel, "⛔ Only the bot owner can use `!channel`.", reply_ts)
            return ""
        from slack_desk_runtime.settings import _VALID_ACTIVATIONS

        parts = cmd_text.split()
        if len(parts) == 1:
            from slack_desk_runtime.settings import reload_settings

            ch_cfg = reload_settings().channel_config(channel)
            agent_info = f", agent=*{ch_cfg.agent}*" if ch_cfg.agent else ""
            await slack_desk.post_message(
                channel,
                f"Channel `{channel}` activation: *{ch_cfg.activation}*{agent_info}\n"
                f"Usage: `!channel always|mention|observe|off` or `!channel agent <name|off>`",
                reply_ts,
            )
            return ""

        subcmd = parts[1].lower()

        # !channel agent <name|off>
        if subcmd == "agent":
            if len(parts) < 3:
                await slack_desk.post_message(
                    channel, "Usage: `!channel agent <name>` or `!channel agent off`", reply_ts
                )
                return ""
            agent_name = parts[2]
            if agent_name.lower() == "off":
                agent_name = ""
            else:
                agents_dir = Path.home() / ".gideon" / "agents"
                if not agents_dir.is_dir():
                    await slack_desk.post_message(
                        channel,
                        "Cannot validate agent: agents directory not found.",
                        reply_ts,
                    )
                    return ""
                known = {f.stem for f in agents_dir.glob("*.json")}
                if agent_name not in known:
                    names = ", ".join(sorted(known)) if known else "(none found)"
                    await slack_desk.post_message(
                        channel,
                        f"Unknown agent `{agent_name}`. Available: {names}",
                        reply_ts,
                    )
                    return ""
            _persist_channel_config(channel, agent=agent_name)
            _reload_orch_cfg()
            sel().log_api_access(
                caller=user_id,
                operation="slack.channel_agent",
                outcome="allowed",
                source="slack",
                resources=f"{channel}:{agent_name or 'default'}",
            )
            label = f"*{agent_name}*" if agent_name else "default"
            await slack_desk.post_message(channel, f"Agent for this channel: {label}", reply_ts)
            return ""

        # !channel always|mention|observe|off
        if subcmd not in _VALID_ACTIVATIONS:
            await slack_desk.post_message(
                channel,
                f"Invalid mode `{subcmd}`. Use: `always`, `mention`, `observe`, or `off`.",
                reply_ts,
            )
            return ""

        _persist_channel_config(channel, activation=subcmd)
        _reload_orch_cfg()
        sel().log_api_access(
            caller=user_id,
            operation="slack.channel_activation",
            outcome="allowed",
            source="slack",
            resources=f"{channel}:{subcmd}",
        )
        await slack_desk.post_message(channel, f"Channel activation set to *{subcmd}*.", reply_ts)
        return ""

    # ── !title — set/generate Slack thread title ──
    if cmd == "!title":
        parts = cmd_text.split()
        title_text = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
        if title_text:
            title_text, _ = redact_exfiltration_urls(title_text)
            title_text, _ = redact_credentials(title_text)
            await slack_desk.set_thread_title(channel, session_key, title_text[:80])
            _mark_titled(session_key, "manual")
            if conversation_log and not _is_slack_desk_restricted(session_key):
                try:
                    conversation_log.set_title(session_key, title_text[:80])
                except Exception:
                    logger.debug("Failed to set conversation log title for %s", session_key, exc_info=True)
            sel().log_api_access(
                caller=user_id,
                operation="slack.thread_title",
                outcome="allowed",
                source="slack",
                resources=f"{channel}:{session_key}",
            )
            await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
        else:
            await slack_desk.post_message(
                channel, "Usage: `!title <text>` — set a title for this thread.", reply_ts
            )
        return ""

    # Catch-all: unrecognized ! command — post error instead of falling through to LLM
    await slack_desk.post_message(
        channel, f"❌ Unknown command `{cmd}`. Type `/gideon help` for available commands.", reply_ts
    )
    return ""


def _filter_options_brackets(text: str, bracket_hold: str, stream_buffer: str) -> tuple[str, str]:
    """Filter ``[OPTIONS: ...]`` tags from streaming text character-by-character.

    Returns the updated *(bracket_hold, stream_buffer)* tuple.
    """
    for ch in text:
        if bracket_hold or ch == "[":
            bracket_hold += ch
            if ch == "]":
                if bracket_hold.startswith("[OPTIONS:"):
                    bracket_hold = ""
                else:
                    stream_buffer += bracket_hold
                    bracket_hold = ""
        else:
            stream_buffer += ch
    return bracket_hold, stream_buffer


def build_timing_footer(
    elapsed: float,
    client: ModelProvider | None = None,
) -> tuple[list[dict], str]:
    """Build the timing/context footer blocks for a Slack response.

    Returns ``(blocks, fallback_text)`` suitable for ``post_blocks``.
    """
    if elapsed < 60:
        duration = f"{int(elapsed)}s"
    else:
        mins, secs = divmod(int(elapsed), 60)
        duration = f"{mins}m {secs}s"
    footer_text = f"Finished in {duration}"
    if client is not None:
        try:
            ctx_pct = round(client.context_usage_pct())
            ctx_icon = "🔴" if ctx_pct >= 70 else "🟠" if ctx_pct >= 50 else "🟡" if ctx_pct >= 30 else "🟢"
            footer_text = f"Finished in {duration} · {ctx_icon} ctx {ctx_pct}%"
        except Exception:
            logger.debug("Failed to retrieve context usage", exc_info=True)
    blocks: list[dict] = [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer_text}]}
    ]
    return blocks, footer_text


def _append_footer_actions(
    footer_blocks: list[dict],
    options: list[str] | None,
    thread_ts: str | None,
    linked_session_key: str | None,
    dashboard_state: object | None,
) -> list[dict]:
    """Append OPTIONS checkboxes and/or Link to Dashboard button to footer blocks."""
    if options:
        from slack_desk_runtime.format import build_options_blocks

        footer_blocks.extend(build_options_blocks(options))
    if thread_ts and not linked_session_key and dashboard_state:
        from slack_desk_runtime.format import build_link_dashboard_button

        if footer_blocks and footer_blocks[-1].get("type") == "actions":
            footer_blocks[-1]["elements"].append(build_link_dashboard_button())
        else:
            footer_blocks.append({"type": "actions", "elements": [build_link_dashboard_button()]})
    return footer_blocks


async def _handle_compact_command(
    slack_desk: SlackDeskClientOps,
    sessions: SessionManager,
    channel: str,
    reply_ts: str,
    msg_ts: str,
    session_key: str,
) -> None:
    """Trigger in-place ACP ``/compact`` on the current thread's session."""
    provider = sessions.get_provider(session_key)
    if not provider:
        await slack_desk.post_message(channel, "No active session to compact.", reply_ts)
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="compact",
            tool_kind="command",
            outcome="no_session",
        )
        return

    _t0 = time.monotonic()

    # --- Phase 1: Pre-compaction UI (cosmetic — log failures, don't abort) ---
    try:
        await slack_desk.add_reaction(channel, msg_ts, "recycle")
        await slack_desk.post_message(channel, "🔄 Compacting context…", reply_ts)
    except Exception:
        logger.debug("Pre-compact UI failed for %s", session_key, exc_info=True)

    # --- Phase 2: Actual compaction (failures warrant error + session teardown) ---
    result_text: str | None = None
    outcome = "unknown"
    try:
        async def _run_compact_stream() -> None:
            nonlocal result_text, outcome
            async for event in provider.stream_command("/compact"):
                if event.kind == EVENT_COMPACTION_STATUS:
                    if event.text == "completed":
                        summary = event.title or ""
                        result_text = (
                            f"✅ Compacted: {summary}" if summary else "✅ Context compacted."
                        )
                        outcome = "completed"
                    elif event.text == "failed":
                        error = event.title or "unknown error"
                        result_text = f"❌ Compaction failed: {error}"
                        outcome = "failed"
                elif event.kind == EVENT_COMPLETE:
                    break

        await asyncio.wait_for(_run_compact_stream(), timeout=120)

        # ACP agent fires compaction asynchronously after EVENT_COMPLETE —
        # wait for the real result, mirroring the dashboard's deferred path.
        if not result_text:
            cr = await provider.wait_for_compaction(timeout=120.0)
            if cr["type"] == "completed":
                summary = cr.get("summary", "")
                result_text = (
                    f"✅ Compacted: {summary}" if summary else "✅ Context compacted."
                )
                outcome = "completed"
            elif cr["type"] == "failed":
                error = cr.get("summary", "")
                result_text = f"❌ Compaction failed: {error}" if error else "❌ Compaction failed."
                outcome = "failed"
            else:
                result_text = "⚠️ Compaction timed out."
                outcome = "timeout"
    except Exception:
        logger.warning("Compact command failed for %s", session_key, exc_info=True)
        try:
            await slack_desk.post_message(channel, "❌ Compaction failed unexpectedly.", reply_ts)
        except Exception:
            logger.debug("Failed to post compact error for %s", session_key, exc_info=True)
        try:
            await sessions.destroy(session_key)
        except Exception:
            logger.warning("Failed to destroy session %s after compact failure", session_key, exc_info=True)
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="compact",
            tool_kind="command",
            outcome="failed",
            error="exception",
        )
        try:
            await slack_desk.remove_reaction(channel, msg_ts, "recycle")
            await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
        except Exception:
            pass
        return

    # --- Phase 3: Post-compaction reporting (log failures, don't mislead) ---
    try:
        result_text, _ = redact_exfiltration_urls(result_text)
        result_text, _ = redact_credentials(result_text)
        await slack_desk.post_message(channel, result_text, reply_ts)

        elapsed = time.monotonic() - _t0
        footer_blocks, footer_text = build_timing_footer(elapsed)
        await slack_desk.post_blocks(channel, footer_blocks, footer_text, reply_ts)
    except Exception:
        logger.debug("Post-compact reporting failed for %s", session_key, exc_info=True)

    try:
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="compact",
            tool_kind="command",
            outcome=outcome,
        )
    except Exception:
        logger.debug("Failed to log compact outcome for %s", session_key, exc_info=True)
    try:
        await slack_desk.remove_reaction(channel, msg_ts, "recycle")
        await _add_phase_reaction(slack_desk, channel, msg_ts, "done")
    except Exception:
        pass


async def handle_message(
    slack_desk: SlackDeskClientOps,
    sessions: SessionManager,
    channel: str,
    text: str,
    thread_ts: str | None,
    msg_ts: str,
    user_id: str,
    team_id: str = "",
    approval_mode: str = APPROVAL_AUTO,
    context_builder: ContextBuilder | None = None,
    conversation_log: ConversationLog | None = None,
    consolidator: HistoryConsolidator | None = None,
    subagent_manager: SubagentManager | None = None,
    channel_agent: str | None = None,
    user_display_name: str | None = None,
    action_context: str | None = None,
    from_trusted_bot: bool = False,
    channel_activation: str | None = None,
    had_voice_input: bool = False,
) -> None:
    """Route a Slack message through ACP with streaming and tool approval.

    NOTE: ``from_trusted_bot`` is consumed only in the error path (echo-loop
    suppression). Early-reply paths (hook auto-reply, !status, !sessions) still
    post to Slack unconditionally — safe today because trusted bots send
    structured commands (``[TASK:id]``, ``[ACK:id]``) that don't match those
    patterns. Extend if that assumption changes.

    *channel_agent* overrides the default agent for this channel (set via
    per-channel config in ``slack.channels``).
    """
    _t0 = time.monotonic()
    session_key = thread_ts or msg_ts
    reply_ts = thread_ts or msg_ts

    # ── Linked thread intercept: route to dashboard session if linked ──
    if _dashboard_state and hasattr(_dashboard_state, "get_linked_session"):
        _linked_session = _dashboard_state.get_linked_session(session_key)
        if _linked_session:
            # Auth check FIRST — deny all messages from unauthorized users
            if not is_allowed_user(user_id):
                logger.warning("Unauthorized user %s in linked thread %s", user_id, session_key)
                sel().log_tool_invocation(
                    session_key=session_key, agent="gideon", source="slack",
                    tool_name="linked_thread_intercept", tool_kind="permission",
                    outcome="denied",
                    metadata={"user_id": user_id, "reason": "not_allowed_user"},
                )
                await slack_desk.post_message(channel, "Not authorized.", reply_ts)
                return
            # Let bang commands fall through to normal handling
            _first_word = text.strip().split(maxsplit=1)[0] if text.strip() else ""
            if _first_word in _BANG_TO_SLASH:
                pass  # fall through
            elif _gateway_services is None:
                # No services handle (never true on the production path — events.py
                # sets it at socket init). Fall through to normal handling so the
                # message is still answered in-thread rather than dropped.
                logger.warning(
                    "Linked thread %s: no gateway services handle — falling through "
                    "to normal handling", session_key,
                )
            else:
                # The guarded door (EA-7). Core applies the trust gate, redaction,
                # session linking, the live WS broadcast/session-list push and the
                # turn itself — the routing this intercept used to hand-roll (the
                # LLM still receives the original text; only the displayed line is
                # redacted, both now core-owned). Slack keeps its own owner gate
                # above: the "Not authorized." reply and SEL denial are
                # slack-specific UX that core's silent verdict does not carry.
                from gideon.sdk.channel import ChannelMessage

                _cm = ChannelMessage(
                    channel_id=channel,
                    text=text,
                    sender=user_id,
                    thread_id=session_key,
                    message_id=msg_ts,
                    metadata={"sender_name": user_display_name or ""},
                )
                verdict = await _gateway_services.deliver_channel_inbound(
                    "slack", _cm, is_dm=channel.startswith("D")
                )
                if verdict.canned_reply:
                    await slack_desk.post_message(channel, verdict.canned_reply, reply_ts)
                sel().log_tool_invocation(
                    session_key=session_key, agent="gideon", source="slack",
                    tool_name="linked_thread_intercept", tool_kind="permission",
                    outcome="allowed" if verdict.allowed else "denied",
                    metadata={
                        "user_id": user_id,
                        "session": _linked_session.key,
                        **({} if verdict.allowed else {"reason": verdict.reason}),
                    },
                )
                logger.info(
                    "Linked Slack message for session %s: %s",
                    _linked_session.key,
                    "routed via the guarded door" if verdict.allowed else f"denied ({verdict.reason})",
                )
                return
    logger.info(
        "🔍 handle_message: thread_ts=%s msg_ts=%s → session_key=%s channel=%s",
        thread_ts,
        msg_ts,
        session_key,
        channel,
    )

    # ── Hook: check for auto-reply before touching ACP ──
    if context_builder:
        hook_result = context_builder.hooks.on_message(text)
        if hook_result.action == HOOK_REPLY:
            await slack_desk.post_message(channel, hook_result.text, reply_ts)
            if conversation_log and not _is_slack_desk_restricted(session_key):
                save_conversation_turn(
                    conversation_log,
                    session_key,
                    text,
                    hook_result.text,
                    source_thread=session_key,
                    source_user=user_id,
                )
            return

    # ── Status keyword: reply with stats summary ──
    if text.strip().lower() == "status":
        await slack_desk.post_message(channel, Stats().summary(), reply_ts)
        return

    # ── Sessions keyword: list recent sessions ──
    if text.strip().lower() == "sessions":
        if is_owner(user_id) or is_allowed_user(user_id):
            sel().log_api_access(
                caller=user_id,
                operation="slack.sessions_command",
                outcome="allowed",
                source="slack",
                resources=channel,
            )
            await _handle_sessions_command(
                text.strip(), slack_desk, channel, reply_ts, msg_ts, session_key, conversation_log
            )
        return

    # ── Compact keyword: trigger in-place context compaction ──

    _cmd_text = re.sub(r"^<@[A-Z0-9]+(?:\|[^>]*)?>\s*", "", text.strip())

    # ── !temporary modifier: strip token and mark session before dispatch ──
    _cmd_text_stripped, _had_temporary = _strip_temporary_token(_cmd_text)
    if _had_temporary:
        await _apply_temporary_modifier(
            session_key, user_id, channel, slack_desk, sessions, reply_ts,
        )
        _cmd_text = _cmd_text_stripped
        text = _TEMPORARY_TOKEN_RE.sub("", text)
        text = " ".join(text.split()) or text  # collapse whitespace
        if not _cmd_text:
            # Message was *only* "!temporary" with no remaining content
            return

    # ── !incognito modifier: strip token and mark session before dispatch ──
    _cmd_text_stripped, _had_incognito = _strip_incognito_token(_cmd_text)
    if _had_incognito:
        await _apply_incognito_modifier(
            session_key, user_id, channel, slack_desk, sessions, reply_ts,
        )
        _cmd_text = _cmd_text_stripped
        text = _INCOGNITO_TOKEN_RE.sub("", text)
        text = " ".join(text.split()) or text
        if not _cmd_text:
            return

    if _cmd_text.strip().lower() == "!compact":
        if is_owner(user_id) or is_allowed_user(user_id):
            sel().log_api_access(
                caller=user_id,
                operation="slack.compact_command",
                outcome="allowed",
                source="slack",
                resources=channel,
            )
            await _handle_compact_command(slack_desk, sessions, channel, reply_ts, msg_ts, session_key)
            return
        else:
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="compact",
                tool_kind="command",
                outcome="denied",
                error=f"unauthorized user {user_id}",
            )
            await slack_desk.post_message(channel, "⛔ Not authorized to compact.", reply_ts)
            return  # deny-by-default: do not fall through

    # ── Owner commands: all "!" prefixed messages are reserved for owner ──
    # Strip leading bot mention from app_mention events so the ! prefix is exposed.
    # DM:       "!agent foo"                    → "!agent foo"       (no-op)
    # @mention: "<@UBOT|gideon> !agent foo"   → "!agent foo"      (strip prefix)
    if _cmd_text.startswith("!"):
        # !dashboard and !stop are available to any allowed user
        _cmd_word = _cmd_text.split()[0]
        if _cmd_word in ("!dashboard", "!stop", "!title"):
            if is_owner(user_id) or is_allowed_user(user_id):
                reply = await _handle_slash_command(
                    _cmd_text,
                    slack_desk,
                    sessions,
                    channel,
                    reply_ts,
                    msg_ts,
                    session_key,
                    user_id,
                    conversation_log=conversation_log,
                )
                if reply is not None:
                    return
            else:
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.allowed_command",
                    outcome="denied",
                    source="slack",
                    resources=_cmd_word,
                    error="unauthorized sender",
                )
                await slack_desk.post_message(channel, "⛔ Not authorized.", reply_ts)
                return
        # All other ! commands are owner-only
        elif not is_owner(user_id):
            sel().log_api_access(
                caller=user_id,
                operation="slack.owner_command",
                outcome="denied",
                source="slack",
                resources=_cmd_word,
                error="unauthorized sender",
            )
            await slack_desk.post_message(channel, "⛔ Owner-only command.", reply_ts)
            return
        else:
            reply = await _handle_slash_command(
                _cmd_text,
                slack_desk,
                sessions,
                channel,
                reply_ts,
                msg_ts,
                session_key,
                user_id,
                conversation_log=conversation_log,
            )
            if reply is not None:
                return

    # ── Subagent spawn: "spawn <task>" (before cron to avoid NL overlap) ──
    if subagent_manager:
        spawn_reply = _handle_spawn_command(text, subagent_manager, session_key)
        if spawn_reply:
            await slack_desk.post_message(channel, spawn_reply, reply_ts)
            if conversation_log and not _is_slack_desk_restricted(session_key):
                save_conversation_turn(
                    conversation_log,
                    session_key,
                    text,
                    spawn_reply,
                    source_thread=session_key,
                    source_user=user_id,
                )
            return

    # ── Automation commands (`cron list` / `remove` / `pause` / `resume`) ──
    # The store is built from the ACTIVE HOME rather than injected (S112): a `cron_service`
    # parameter let the caller decide which scheduler this surface read, and six call sites passed
    # `orch.cron_svc` — the legacy service core has deleted. One home, one store, one answer.
    cron_reply = _handle_cron_command(text, TriggerStore(base_dir=config_dir()), channel, reply_ts)
    if cron_reply:
        await slack_desk.post_message(channel, cron_reply, reply_ts)
        if conversation_log and not _is_slack_desk_restricted(session_key):
            save_conversation_turn(
                conversation_log,
                session_key,
                text,
                cron_reply,
                source_thread=session_key,
                source_user=user_id,
            )
        return

    from slack_desk_runtime.settings import get_settings

    status_ctrl = StatusReactionController(
        slack_desk, channel, msg_ts, enabled=get_settings().reactions_enabled,
    )
    status_ctrl.set_phase("queued")
    _had_error = False
    _stop_reason = ""

    # Set assistant thread status while we wait for the LLM to respond.
    # Defer start_stream until the first text chunk arrives so the user
    # sees the status indicator instead of a blank bot message.
    await slack_desk.set_thread_status(channel, reply_ts, _STATUS_WORKING)

    use_slack_desk_stream = False
    stream_ts: str | None = None

    accumulated = ""
    thinking_accumulated = ""
    stream_buffer = ""  # unsent chunks for streaming API (buffered between rate-limited appends)
    bracket_hold = ""  # text held back from '[' until ']' to filter [OPTIONS: ...]
    last_edit = 0.0
    _task_counter = 0  # incrementing task ID for task cards
    _active_task_id = ""  # current in-progress task
    _active_task_title = ""  # display title (purpose or tool name)
    _status_dirty = False  # True when status needs reset to base on next text chunk
    _tool_gap = False

    async def _rotate_stream() -> str | None:
        """Stop the dead stream and start a fresh one. Returns new ts or None."""
        nonlocal stream_ts, use_slack_desk_stream
        if stream_ts:
            await slack_desk.stop_stream(channel, stream_ts)
        new_ts = await slack_desk.start_stream(
            channel, reply_ts, team_id=team_id or None, user_id=user_id or None
        )
        if new_ts:
            stream_ts = new_ts
            logger.info("Stream rotated: new ts=%s", new_ts)
        else:
            use_slack_desk_stream = False
            logger.warning("Stream rotation failed — falling back to chat.update")
        return new_ts

    async def _append_stream(text: str) -> bool:
        """Append text to stream, rotating on failure. Redacts before sending."""
        if not text or not stream_ts:
            return True
        if channel_activation == ACTIVATION_REVIEW:
            return True  # Suppress streaming text in review mode
        text, _ = redact_exfiltration_urls(text)
        text, _ = redact_credentials(text)
        ok = await slack_desk.append_stream(channel, stream_ts, text)
        if not ok and use_slack_desk_stream:
            if await _rotate_stream():
                assert stream_ts is not None
                return await slack_desk.append_stream(channel, stream_ts, text)
        return ok

    async def _append_task(task_id: str, title: str, status: str, details: str = "") -> bool:
        """Append task card to stream, rotating on failure."""
        if not stream_ts:
            return False
        if channel_activation == ACTIVATION_REVIEW:
            return True  # Suppress task cards in review mode
        ok = await slack_desk.append_task(channel, stream_ts, task_id, title, status, details=details)
        if not ok and use_slack_desk_stream:
            if await _rotate_stream():
                assert stream_ts is not None
                return await slack_desk.append_task(
                    channel, stream_ts, task_id, title, status, details=details
                )
        return ok

    async def _ensure_stream_started() -> None:
        """Lazy-start the stream on first event. Falls back to chat.update."""
        nonlocal stream_ts, use_slack_desk_stream
        if stream_ts is not None:
            return
        if channel_activation == ACTIVATION_REVIEW:
            # No visible message — only thread status indicator is shown
            stream_ts = _REVIEW_PLACEHOLDER_TS
            use_slack_desk_stream = False
            return
        stream_ts = await slack_desk.start_stream(
            channel, reply_ts, team_id=team_id or None, user_id=user_id or None
        )
        use_slack_desk_stream = stream_ts is not None
        if not use_slack_desk_stream:
            stream_ts = await slack_desk.post_message(channel, _THINKING, reply_ts)
        assert stream_ts is not None

    task = Task(id=msg_ts)
    _acquired = False

    # ── Bidirectional sync: check if this Slack thread is linked to a dashboard session ──
    linked_session_key = sessions.get_session_for_thread(session_key)
    if linked_session_key:
        logger.info(
            "🔗 Slack thread %s linked to dashboard session %s — routing there",
            session_key,
            linked_session_key,
        )
        session_key = linked_session_key

    try:
        task.start()
        _agent = _thread_agents.get(session_key) or channel_agent or _get_default_agent() or None
        client, is_new, resumed = await sessions.get_or_create(
            session_key, agent=_agent, channel_id=channel
        )
        _acquired = True
        if is_new:
            await sessions.set_channel(session_key, channel)
        if not linked_session_key:
            sessions.set_channel_link(session_key, session_key, channel)
        logger.info(
            "🔍 session state: key=%s is_new=%s resumed=%s",
            session_key,
            is_new,
            resumed,
        )

        # Write current session key so MCP tools can pass it to spawn API.
        # Keyed by ACP agent PID to avoid races between concurrent sessions.
        try:
            pid = sessions.get_pid(session_key)
            if isinstance(pid, int):
                (config_dir() / f"session_pid_{pid}.txt").write_text(session_key, encoding="utf-8")
        except Exception:
            pass

        # Build message with context injection
        compressed: str | None = None
        # is_new = new ACP agent/dashboard process, NOT new conversation.
        # The Slack thread persists across processes, so we compress its
        # history to bootstrap the fresh session's context window.
        if is_new and not resumed and context_builder and context_builder.conversation_log:
            from gideon.sdk.channel import compress_thread_history

            compressed = await compress_thread_history(
                context_builder.conversation_log, session_key, text, sessions
            )

        # After a soft-cancel, ACP agent drops the cancelled turn from its
        # conversation log — but the user+assistant text is persisted to our
        # local conversation_log. Re-inject just the cancelled turn as a
        # preamble so the LLM remembers what was interrupted. Flag lives on
        # the session (set by SessionManager.stop_turn), consumed one-shot.
        # Use getattr for prev_turn_cancelled so test doubles (AsyncMock)
        # don't raise AttributeError on coroutine-returning mock chains.
        _session = getattr(sessions, "_sessions", {}).get(session_key)
        if (
            _session is not None
            and getattr(_session, "prev_turn_cancelled", False)
            and context_builder
            and context_builder.conversation_log
        ):
            _session.prev_turn_cancelled = False
            from gideon.sdk.channel import build_cancelled_turn_preamble

            _preamble = build_cancelled_turn_preamble(
                context_builder.conversation_log, session_key
            )
            if _preamble:
                text = _preamble + "\n\n" + text

        # Fetch thread parent message when starting a new session in an
        # existing thread (e.g. replying to a cron thread).  Gives the LLM
        # context about what started the thread without requiring manual
        # batch_get_thread_replies.
        thread_parent_text: str | None = None
        if is_new and not resumed and thread_ts and context_builder:
            if not compressed:
                thread_parent_text = await slack_desk.fetch_message(channel, thread_ts)
            if thread_parent_text:
                from gideon.sdk.channel import redact

                thread_parent_text = redact(thread_parent_text)
                if len(thread_parent_text) > 3000:
                    thread_parent_text = (
                        thread_parent_text[:3000]
                        + "\n[truncated — use batch_get_thread_replies for full text]"
                    )

        # ── Fence untrusted non-owner content before it becomes the agent's prompt ──
        # CHANNEL-EXPANSION T1.4 / CE-6. A non-owner's chat text is untrusted DATA, never
        # instructions; wrap it in the platform's untrusted-content fence — the SAME fence
        # core's guarded door hands the sibling channels as ``verdict.fenced_text`` — before
        # it reaches the model. Trusted senders (owner, an allowlisted user, a trusted bot)
        # pass through unfenced, exactly as core exempts ``is_allowed_sender``. Only the model
        # input is fenced: display, history and the dashboard mirror below keep the raw text.
        from slack_desk_runtime.transport import fence_untrusted_inbound

        agent_text = fence_untrusted_inbound(
            text, user_id, trusted=(from_trusted_bot or is_allowed_user(user_id))
        )

        if context_builder:
            # Thread-scoped temporary mode: blocks memory reads.
            _slack_desk_blocks_reads = is_thread_temporary(session_key)
            full_message, _ = context_builder.build_message(
                agent_text,
                is_new,
                session_key,
                channel_id=channel,
                thread_ts=thread_ts or msg_ts,
                agent=_agent,
                resumed=resumed,
                user_display_name=user_display_name,
                compressed_history=compressed,
                action_context=action_context,
                thread_parent_text=thread_parent_text,
                blocks_reads=_slack_desk_blocks_reads,
            )
        else:
            full_message = agent_text

        # ── Early cancellation check: bail before expensive LLM call ──
        if sessions.is_cancelled(session_key, msg_ts):
            logger.info("Message %s cancelled before LLM call — skipping", msg_ts)
            await slack_desk.set_thread_status(channel, reply_ts, "")
            return

        async for event in client.stream(full_message):
            if event.kind == EVENT_TEXT_CHUNK:
                if _tool_gap and accumulated and accumulated[-1:] not in ("\n", " "):
                    first = event.text[:1]
                    if first and first not in ("\n", " "):
                        event.text = "\n\n" + event.text
                event.text, _ = redact_exfiltration_urls(event.text)
                event.text, _ = redact_credentials(event.text)

                if event.text:
                    _tool_gap = False
                status_ctrl.set_phase("thinking")
                status_ctrl.on_progress()
                accumulated += event.text

                if _status_dirty and use_slack_desk_stream:
                    await slack_desk.set_thread_status(channel, reply_ts, _STATUS_WORKING)
                    _status_dirty = False

                # ── Bracket hold-back: filter [OPTIONS: ...] from stream ──
                # When inside a bracket, accumulate into bracket_hold.
                # On ']', release if not OPTIONS, suppress if it is.
                if use_slack_desk_stream:
                    bracket_hold, stream_buffer = _filter_options_brackets(
                        event.text, bracket_hold, stream_buffer
                    )
                else:
                    stream_buffer += event.text

                await _ensure_stream_started()

                now = time.monotonic()
                if now - last_edit >= _EDIT_INTERVAL:
                    if use_slack_desk_stream:
                        if stream_buffer:
                            stream_buffer, _ = strip_thinking_tags(
                                stream_buffer, strip_whitespace=False
                            )
                            await _append_stream(stream_buffer)
                            stream_buffer = ""
                    else:
                        assert stream_ts is not None
                        if channel_activation != ACTIVATION_REVIEW:
                            await _safe_update(slack_desk, channel, stream_ts, accumulated + _CURSOR)
                    last_edit = now

            elif event.kind == EVENT_THINKING_CHUNK:
                status_ctrl.set_phase("thinking")
                status_ctrl.on_progress()
                thinking_accumulated += event.text

            elif event.kind == EVENT_TOOL_CALL:
                _tool_gap = True
                # Check tool hooks
                if context_builder:
                    tool_result = context_builder.hooks.on_tool_call(event.title)
                    if tool_result.action == TOOL_DENY:
                        accumulated += f"\n🚫 _Tool `{event.title}` blocked by hooks._"
                        sel().log_tool_invocation(
                            session_key=session_key,
                            source="slack",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="denied",
                            error="hook_deny",
                        )
                        continue

                sel().log_tool_invocation(
                    session_key=session_key,
                    source="slack",
                    tool_name=event.title,
                    tool_kind=event.tool_kind,
                    outcome="invoked",
                )

                tool_name = event.title.removeprefix("Running: ")
                tool_name, _ = redact_exfiltration_urls(tool_name)
                tool_name, _ = redact_credentials(tool_name)
                tool_kind = event.tool_kind or ""
                status_ctrl.set_phase(_tool_to_phase(tool_name, tool_kind))
                status_ctrl.on_progress()
                tool_detail = event.tool_purpose or tool_kind
                tool_status = f"\n🫆 `{tool_name}`\n"
                await _ensure_stream_started()
                if use_slack_desk_stream:
                    await slack_desk.set_thread_status(channel, reply_ts, f"is using {tool_name}")
                    _status_dirty = True
                if use_slack_desk_stream:
                    # Flush any buffered text before the tool status
                    if stream_buffer:
                        stream_buffer, _ = strip_thinking_tags(
                            stream_buffer, strip_whitespace=False
                        )
                        await _append_stream(stream_buffer)
                        stream_buffer = ""
                    # Mark previous task complete, start new one
                    if _active_task_id:
                        await _append_task(_active_task_id, _active_task_title, "complete")
                    _task_counter += 1
                    _active_task_id = f"tool_{_task_counter}"
                    _active_task_title = event.tool_purpose or tool_name
                    _active_task_title, _ = redact_exfiltration_urls(_active_task_title)
                    _active_task_title, _ = redact_credentials(_active_task_title)
                    await _append_task(
                        _active_task_id,
                        title=_active_task_title,
                        status="in_progress",
                        details=tool_name if tool_detail else "",
                    )
                else:
                    accumulated += tool_status
                    assert stream_ts is not None
                    if channel_activation != ACTIVATION_REVIEW:
                        await _safe_update(slack_desk, channel, stream_ts, accumulated + _CURSOR)
                last_edit = time.monotonic()

                # wait tool blocks MCP for up to 30min — finalize the
                # streaming message now so Slack doesn't show an error.
                # _ensure_stream_started() will open a new message when
                # the next text chunk arrives after wait returns.
                if tool_name == "wait" and use_slack_desk_stream and stream_ts:
                    if _active_task_id:
                        await _append_task(_active_task_id, _active_task_title, "complete")
                        _active_task_id = ""
                    await slack_desk.stop_stream(channel, stream_ts)
                    stream_ts = None
                    accumulated = ""

            elif event.kind == EVENT_PERMISSION_REQUEST:
                # Check tool hooks for auto-approve
                if context_builder:
                    tool_result = context_builder.hooks.on_tool_call(event.title)
                    if tool_result.action == TOOL_AUTO_APPROVE:
                        await client.approve_tool(event.request_id)
                        sel().log_tool_invocation(
                            session_key=session_key,
                            source="slack",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="auto_approved",
                            request_id=event.request_id,
                            metadata={"reason": "hook_auto_approve"},
                        )
                        continue
                    if tool_result.action == TOOL_DENY:
                        await client.reject_tool(event.request_id)
                        accumulated += f"\n🚫 _Tool `{event.title}` blocked by hooks._"
                        sel().log_tool_invocation(
                            session_key=session_key,
                            source="slack",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="denied",
                            request_id=event.request_id,
                            error="hook_deny",
                        )
                        continue

                # auto_approve_subagent_spawn → auto-approve subagent_run tool calls
                if _should_auto_approve_spawn(context_builder, event.title or ""):
                    await client.approve_tool(event.request_id)
                    sel().log_tool_invocation(
                        session_key=session_key,
                        source="slack",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="auto_approved",
                        request_id=event.request_id,
                        metadata={"reason": "auto_approve_subagent_spawn"},
                    )
                    continue

                if approval_mode == APPROVAL_AUTO:
                    await client.approve_tool(event.request_id)
                    sel().log_tool_invocation(
                        session_key=session_key,
                        source="slack",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="auto_approved",
                        request_id=event.request_id,
                        metadata={"reason": "approval_mode_auto"},
                    )
                    continue

                # Trust mode (per-session) or YOLO mode (owner-only global) → auto-approve
                _yolo_now = is_yolo_mode()  # delegates to trust_mode (expires on read)
                if _yolo_now or session_key in _trusted_sessions:
                    await client.approve_tool(event.request_id)
                    logger.info(
                        "Auto-approved %s (%s)",
                        event.title,
                        "yolo" if _yolo_now else "trust",
                    )
                    sel().log_tool_invocation(
                        session_key=session_key,
                        source="slack",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="auto_approved",
                        request_id=event.request_id,
                        metadata={"reason": "yolo" if _yolo_now else "trust"},
                    )
                    continue

                logger.info("Permission request: tool=%s req_id=%s", event.title, event.request_id)
                status_ctrl.pause_stall_watchdog()
                task.await_approval()
                await _ensure_stream_started()
                if use_slack_desk_stream:
                    await slack_desk.set_thread_status(channel, reply_ts, "Waiting for approval…")
                    _status_dirty = True
                    # Flush buffered text before approval pause
                    if stream_buffer:
                        stream_buffer, _ = strip_thinking_tags(
                            stream_buffer, strip_whitespace=False
                        )
                        await _append_stream(stream_buffer)
                        stream_buffer = ""

                outcome = await _request_approval(
                    slack_desk,
                    client,
                    channel,
                    reply_ts,
                    event,
                    session_key,
                    is_dm=channel.startswith("D"),
                )
                task.resume()
                status_ctrl.resume_stall_watchdog()
                sel().log_tool_invocation(
                    session_key=session_key,
                    source="slack",
                    tool_name=event.title,
                    tool_kind=event.tool_kind,
                    outcome="approved" if outcome != _OUTCOME_REJECTED else "rejected",
                    request_id=event.request_id,
                    metadata={"reason": "interactive"},
                )
                if outcome == _OUTCOME_REJECTED:
                    if use_slack_desk_stream and _active_task_id:
                        assert stream_ts is not None
                        await _append_task(_active_task_id, _active_task_title, "error")
                        _active_task_id = ""
                    if not use_slack_desk_stream:
                        accumulated += "\n🚫 _Tool use rejected._"
                    break

            elif event.kind == EVENT_COMPLETE:
                status_ctrl.on_progress()
                _stop_reason = event.stop_reason
                if (
                    _stop_reason
                    and _stop_reason != STOP_REASON_END_TURN
                    and _stop_reason != STOP_REASON_CANCELLED
                ):
                    logger.warning(
                        "Unexpected stop_reason %r for %s — treating as normal completion",
                        _stop_reason,
                        session_key,
                    )
                break

        if _stop_reason == STOP_REASON_CANCELLED:
            logger.info("Turn cancelled by user for %s", session_key)
            task.complete()
        else:
            task.complete()
            sessions.record_success(session_key)

        # Check context usage — fires background compaction at configured threshold, never blocks
        sessions.check_context_usage(session_key, client)

    except AcpTimeoutError as e:
        _had_error = True
        accumulated = e.partial_output or "⏱️ Request timed out. Please try again."
        task.fail("timeout")
        await sessions.record_failure(session_key)
    except AcpProcessDied:
        _had_error = True
        accumulated = accumulated or "💀 Agent process died. Please try again."
        task.fail("process_died")
        await sessions.record_failure(session_key)
    except AcpError as e:
        _had_error = True
        accumulated = f"❌ {e}"
        task.fail(str(e))
        await sessions.record_failure(session_key)
    except Exception:
        _had_error = True
        logger.exception("Unexpected error handling message")
        accumulated = accumulated or "🔧 Something went wrong. Please try again."
        task.fail("unexpected")
        await sessions.record_failure(session_key)
    finally:
        if _acquired:
            sessions.release(session_key)
        status_ctrl.finalize(error=_had_error)
        await asyncio.sleep(0)  # let finalize fire

    # ── Cancelled check: suppress response if message was deleted mid-flight ──
    if sessions.is_cancelled(session_key, msg_ts):
        logger.info("Message %s cancelled (deleted) — suppressing response", msg_ts)
        await slack_desk.set_thread_status(channel, reply_ts, "")
        if stream_ts:
            try:
                await slack_desk.delete_message(channel, stream_ts)
            except Exception:
                logger.debug("Failed to delete cancelled stream", exc_info=True)
        return

    # Clear assistant thread status (skip in review mode — keep indicator until button press)
    if channel_activation != ACTIVATION_REVIEW:
        await slack_desk.set_thread_status(channel, reply_ts, "")

    # Suppress error replies for trusted bot messages to prevent echo loops
    if from_trusted_bot and _had_error:
        logger.info("Suppressing error reply to trusted bot message to prevent echo loop")
        if conversation_log and not _is_slack_desk_restricted(session_key):
            save_conversation_turn(
                conversation_log,
                session_key,
                text,
                "[suppressed: trusted bot error]",
                source_thread=session_key,
                source_user=user_id,
            )
        return

    # Strip any inline <thinking> tags that leaked into the text
    if accumulated:
        accumulated, inline_thinking = strip_thinking_tags(accumulated)
        accumulated = accumulated.strip()
        if inline_thinking:
            thinking_accumulated += ("\n\n" if thinking_accumulated else "") + inline_thinking

    actually_streamed = use_slack_desk_stream and bool(stream_ts)
    final_text = to_slack_desk_mrkdwn(accumulated, keep_tables=actually_streamed) if accumulated else _NO_RESPONSE

    # Scan for URL exfiltration before posting to Slack (link previews auto-fetch)
    final_text, exfil_warnings = redact_exfiltration_urls(final_text)
    for w in exfil_warnings:
        logger.warning("Exfiltration URL redacted in response: %s", w)
    final_text, cred_warnings = redact_credentials(final_text)
    for w in cred_warnings:
        logger.warning("Credential redacted in response: %s", w)

    # Extract OPTIONS buttons from response and post as Block Kit
    from slack_desk_runtime.format import extract_options

    clean_text, options = extract_options(final_text)

    # ── Review mode: ephemeral draft instead of public post ──
    if channel_activation == ACTIVATION_REVIEW:
        from slack_desk_runtime.blocks import review_draft_blocks

        # Stop streaming, delete placeholder, set status indicator
        if stream_ts and stream_ts != _REVIEW_PLACEHOLDER_TS:
            if use_slack_desk_stream:
                try:
                    await slack_desk.stop_stream(channel, stream_ts)
                except Exception:
                    pass
            try:
                await slack_desk.delete_message(channel, stream_ts)
            except Exception:
                logger.debug("Failed to delete stream msg in review mode", exc_info=True)
        await slack_desk.set_thread_status(channel, reply_ts, "Awaiting review…")
        # Post ephemeral draft with approve/edit/cancel buttons
        draft = clean_text or _NO_RESPONSE
        draft_key = f"{channel}|{reply_ts}|{uuid.uuid4().hex[:8]}"
        blocks = review_draft_blocks(draft, draft_key)
        await slack_desk.post_ephemeral(channel, user_id, draft, blocks=blocks, thread_ts=reply_ts if thread_ts else None)
        # Store draft for button handlers (requester can act on their own draft)
        _review_drafts_set(draft_key, draft, user_id)
        logger.info("Review mode: ephemeral draft sent to %s in %s", user_id, channel)
        # Persist conversation (draft counts as a turn)
        if conversation_log:
            save_conversation_turn(
                conversation_log,
                session_key,
                text,
                accumulated,
                source_thread=session_key,
            )
        return

    if use_slack_desk_stream and stream_ts:
        # Mark last task complete
        if _active_task_id:
            await _append_task(_active_task_id, _active_task_title, "complete")
        # Flush remaining buffer (bracket_hold excluded — it's either
        # a suppressed OPTIONS tag or an unclosed bracket we drop)
        if stream_buffer:
            stream_buffer, _ = strip_thinking_tags(stream_buffer, strip_whitespace=False)
            await _append_stream(stream_buffer)
        await slack_desk.stop_stream(channel, stream_ts, clean_text or _NO_RESPONSE)

    if stream_ts:
        # Always finalize with clean accumulated text to strip streaming
        # artifacts (whitespace drops, partial flushes).
        from slack_desk_runtime.format import _convert_tables
        final_text = _convert_tables(clean_text) if clean_text else _NO_RESPONSE
        await _safe_final_update(slack_desk, channel, stream_ts, final_text or _NO_RESPONSE, reply_ts)
    else:
        # No stream was started (e.g. no text chunks) — post the final text directly
        await slack_desk.post_message(channel, clean_text or _NO_RESPONSE, reply_ts)

    # Post thinking/reasoning as a thread reply between response and timing footer
    if thinking_accumulated:
        thinking_mrkdwn = to_slack_desk_mrkdwn(thinking_accumulated)
        thinking_mrkdwn, exfil_warnings = redact_exfiltration_urls(thinking_mrkdwn)
        for w in exfil_warnings:
            logger.warning("Exfiltration URL redacted in thinking: %s", w)
        thinking_mrkdwn, cred_warnings = redact_credentials(thinking_mrkdwn)
        for w in cred_warnings:
            logger.warning("Credential redacted in thinking: %s", w)
        thinking_parts = split_message(f"💭 *Thinking*\n\n{thinking_mrkdwn}")
        for part in thinking_parts:
            try:
                await slack_desk.post_message(channel, part, reply_ts)
            except Exception:
                logger.warning("Failed to post thinking message", exc_info=True)

    # ── Timing footer ──
    elapsed = time.monotonic() - _t0
    footer_blocks, footer_text = build_timing_footer(elapsed, client)
    footer_blocks = _append_footer_actions(
        footer_blocks, options, thread_ts, linked_session_key, _dashboard_state,
    )
    await slack_desk.post_blocks(channel, footer_blocks, footer_text, reply_ts)

    # ── Voice reply (fire-and-forget, non-blocking) ──
    # Triggers when: (a) user has opted in globally or per-thread via !voice,
    # or (b) this message carried transcribed voice input and
    # auto_reply_to_voice is enabled (symmetric voice conversation).
    #
    # ``auto_reply_to_voice`` defaults to ``enabled``'s value at config load
    # (see ``set_orch_cfg``) so users with explicit ``enabled=false`` retain
    # zero-voice behavior, and globally-enabled users automatically get
    # symmetric voice-in/voice-out. Users who want voice ONLY in response to
    # voice memos can set ``auto_reply_to_voice=true`` while leaving
    # ``enabled=false``. See docs/reference/voice.md.
    voice_auto_reply = had_voice_input and _vc.auto_reply_to_voice
    if _vc.global_enabled or session_key in _vc.sessions or voice_auto_reply:
        if len(accumulated) >= 50:
            from gideon.sdk.channel import active_voice_params

            _params = active_voice_params()
            _tts_ok = _params is not None and await _params["provider"].can_synthesize(
                _params["voice"],
            )
            if not _tts_ok:
                # Voice reply requested via any opt-in path (global, per-thread,
                # or voice-auto-reply) but no usable TTS voice is configured.
                # Post a one-shot ephemeral so the user knows the response fell
                # back to text only — silent fallback is worse UX for users who
                # explicitly opted in.
                hint = "Select a TTS voice in Settings → Models (download a Piper voice in Providers first)."
                if voice_auto_reply:
                    intro = "🔇 Received your voice memo. Replying as text — "
                else:
                    intro = "🔇 Voice reply requested but "
                try:
                    await slack_desk.post_ephemeral(
                        channel,
                        user_id,
                        f"{intro}no TTS voice is configured. {hint}",
                    )
                except Exception:
                    logger.debug("Failed to post TTS-unavailable ephemeral", exc_info=True)
            else:
                asyncio.create_task(
                    _safe_voice_reply(
                        slack_desk,
                        channel,
                        reply_ts,
                        final_text,
                    )
                )

    # ── Update task banner with final state ──
    # ── Persist conversation history ──
    _skip_writes = _is_slack_desk_restricted(session_key)
    if conversation_log and not _skip_writes:
        save_conversation_turn(
            conversation_log,
            session_key,
            text,
            accumulated,
            source_thread=session_key,
            source_user=user_id,
        )
        if consolidator and _stop_reason != STOP_REASON_CANCELLED:
            consolidator.maybe_consolidate(session_key)

    # ── Bidirectional sync: mirror to dashboard if routed to a dashboard session ──
    if linked_session_key and _dashboard_state and accumulated and not _skip_writes:
        try:
            ds = _dashboard_state
            session_name = linked_session_key.removeprefix("dashboard:")
            session = getattr(ds, "_sessions", {}).get(session_name)
            if session:
                session.append("user", text, "msg msg-u")
                session.append("assistant", accumulated, "msg msg-a")
                if session._on_message:
                    session._on_message(session.key, {"role": "user", "content": text, "cls": "msg msg-u"})
                    session._on_message(session.key, {"role": "assistant", "content": accumulated, "cls": "msg msg-a"})
                ds.push_sessions_update()  # type: ignore[attr-defined]
        except Exception:
            logger.debug("Failed to mirror Slack message to dashboard", exc_info=True)
    # ── Auto-title Slack thread (fire-and-forget) ──
    # Claim-early-unclaim-on-failure pattern: mark titled immediately to prevent
    # duplicate tasks from concurrent messages. If the background task fails or
    # returns SKIP, it unclaims the key so the next message retries. A message
    # arriving between claim and unclaim is intentionally skipped (no duplicate).
    if not _had_error and session_key not in _titled_threads and not _skip_writes:
        _mark_titled(session_key)  # claim early to prevent duplicate tasks
        _t = asyncio.create_task(
            _maybe_auto_title_slack_desk(
                slack_desk, sessions, channel, session_key, conversation_log, text, accumulated
            )
        )
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)


# ── Slack thread auto-title ─────────────────────────────────────────────

_auto_title_lock: asyncio.Lock | None = None


def _get_auto_title_lock() -> asyncio.Lock:
    """Lazily create the lock inside a running event loop."""
    global _auto_title_lock
    if _auto_title_lock is None:
        _auto_title_lock = asyncio.Lock()
    return _auto_title_lock


def _build_title_prompt(user_msg: str, assistant_msg: str) -> str:
    """Build the Slack thread auto-title prompt.

    The instruction lives in the prompt system (bundled ``task-channel-title``,
    bindable in Settings → Prompts), rendered with the conversation turn."""
    from gideon.sdk.channel import render_use_case_prompt

    return render_use_case_prompt(
        "channel_title", {"user_msg": user_msg, "assistant_msg": assistant_msg}
    ) or ""


async def _maybe_auto_title_slack_desk(
    slack_desk: SlackDeskClientOps,
    sessions: SessionManager,
    channel: str,
    session_key: str,
    conversation_log: ConversationLog | None,
    user_text: str,
    assistant_text: str,
) -> None:
    """Generate and set a Slack thread title after the first response."""
    try:
        from gideon.sdk.channel import BACKGROUND_KEY

        prompt = _build_title_prompt(user_text[:200], assistant_text[:200])
        async with _get_auto_title_lock():
            client, _, _ = await sessions.get_or_create(BACKGROUND_KEY)
            title = ""
            try:

                async def _stream_title() -> str:
                    t = ""
                    async for event in client.stream(prompt):
                        if event.kind == EVENT_TEXT_CHUNK:
                            t += event.text
                        elif event.kind == EVENT_PERMISSION_REQUEST:
                            sel().log_api_access(
                                caller="system",
                                operation="auto_title.tool_rejected",
                                outcome="denied",
                                source="slack",
                                resources=str(event.request_id),
                            )
                            await client.reject_tool(event.request_id)
                        elif event.kind == EVENT_COMPLETE:
                            break
                    return t

                title = await asyncio.wait_for(_stream_title(), timeout=30)
            finally:
                sessions.release(BACKGROUND_KEY)

        title = title.split("\n")[0].strip("\"'. \t")
        title = title.replace("<", "").replace(">", "")  # neutralize Slack mrkdwn links
        if not title or title.upper() == "SKIP":
            _titled_threads.pop(session_key, None)  # allow retry on next exchange
            return
        title, _ = redact_exfiltration_urls(title)
        title, _ = redact_credentials(title)
        title = title[:80]

        if _titled_threads.get(session_key) == "manual":
            return  # manual title was set while we were streaming
        await slack_desk.set_thread_title(channel, session_key, title)
        if conversation_log:
            try:
                conversation_log.set_title(session_key, title)
            except Exception:
                logger.debug("Failed to set conversation log title for %s", session_key, exc_info=True)
        sel().log_api_access(
            caller="system",
            operation="slack.thread_auto_title",
            outcome="allowed",
            source="slack",
            resources=f"{channel}:{session_key}",
        )
        logger.info("Slack thread auto-titled: %s → %r", session_key, title)
    except Exception:
        _titled_threads.pop(session_key, None)  # allow retry on transient failure
        logger.debug("Slack thread auto-title failed for %s", session_key, exc_info=True)


async def _request_approval(
    slack_desk: SlackDeskClientOps,
    provider: ModelProvider,
    channel: str,
    thread_ts: str,
    event: LLMEvent,
    session_key: str = "",
    is_dm: bool = True,
) -> str:
    """Post approval buttons, wait for click, return 'approved' or 'rejected'."""
    blocks = _build_approval_blocks(event, is_dm=is_dm)
    approval_ts = await slack_desk.post_blocks(channel, blocks, "Manual approval required", thread_ts)

    key = f"{channel}:{approval_ts}"
    pending = _PendingApproval(provider, event.request_id, session_key)
    _pending_approvals[key] = pending

    try:
        outcome = await asyncio.wait_for(pending.future, timeout=_APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        outcome = _OUTCOME_REJECTED
        await provider.reject_tool(event.request_id)
    finally:
        _pending_approvals.pop(key, None)

    try:
        await slack_desk.delete_message(channel, approval_ts)
    except Exception:
        status = "✅ Approved" if outcome == _OUTCOME_APPROVED else "🚫 Rejected"
        title_safe, _ = redact_exfiltration_urls(event.title)
        title_safe, _ = redact_credentials(title_safe)
        await _safe_update(slack_desk, channel, approval_ts, f"🔐 *{title_safe}* — {status}")

    return outcome


async def handle_interaction(channel: str, msg_ts: str, action_id: str, user_id: str = "", thread_ts: str = "", slack_desk: SlackDeskClientOps | None = None) -> str | None:
    """Handle a Block Kit button click for tool approval.

    Supports four actions:
    - approve_tool: approve this one tool call
    - trust_tool: auto-approve all tools for this session (thread)
    - reject_tool: reject this tool call

    Security: rejects non-owner clicks. Trust requires DM channel
    (verified via conversations.info by the gateway caller).
    """

    # Deny-by-default: reject unless positively confirmed as allowed
    if not user_id or not is_allowed_user(user_id):
        logger.warning(
            "Rejecting interactive action from unauthorized user %s (action=%s)", user_id, action_id
        )
        sel().log_api_access(
            caller=user_id or "unknown",
            operation="slack.interactive.approval",
            outcome="denied",
            source="slack",
            resources=action_id,
            error="unauthorized user",
        )
        return None

    key = f"{channel}:{msg_ts}"
    pending = _pending_approvals.get(key)
    if not pending:
        # Approval already resolved (approved/rejected/timed out).
        # For trust clicks, still set trust using the thread as session key.
        # Replicate session_key derivation from handle_message: thread_ts,
        # then check for linked dashboard session override.
        if action_id == _ACTION_TRUST and thread_ts:
            if not is_allowed_user(user_id):
                logger.warning("Rejecting late trust click from non-allowed user %s", user_id)
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.interactive.trust_late",
                    outcome="denied",
                    source="slack",
                    error="unauthorized user",
                )
                return None
            # Verify clicking user owns this thread (prevents privilege escalation)
            if not slack_desk:
                logger.warning("Rejecting late trust click: cannot verify thread ownership (no slack client)")
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.interactive.trust_late",
                    outcome="denied",
                    source="slack",
                    error="no_slack_client",
                )
                return None
            try:
                msgs = await slack_desk.fetch_thread_replies(channel, thread_ts, limit=1)
                thread_owner = msgs[0].get("user", "") if msgs else ""
            except Exception:
                logger.warning("Failed to verify thread ownership for %s", thread_ts, exc_info=True)
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.interactive.trust_late",
                    outcome="denied",
                    source="slack",
                    error="thread_ownership_check_failed",
                )
                return None
            if not thread_owner or thread_owner != user_id:
                logger.warning("Rejecting late trust click: user %s is not thread owner", user_id)
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.interactive.trust_late",
                    outcome="denied",
                    source="slack",
                    error="not_thread_owner",
                )
                return None
            from gideon.sdk.channel import SessionMap
            session_key = thread_ts
            try:
                linked = SessionMap().get_session_for_thread(thread_ts)
                if linked:
                    session_key = linked
            except Exception:
                logger.warning("SessionMap lookup failed for thread %s; refusing to grant trust", thread_ts, exc_info=True)
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.interactive.trust_late",
                    outcome="denied",
                    source="slack",
                    error="session_map_lookup_failed",
                )
                return None
            _trusted_sessions.add(session_key)
            logger.info("Trust mode ON (late click) for session %s", session_key)
            sel().log_api_access(
                caller=user_id,
                operation="slack.interactive.trust_late",
                outcome="allowed",
                source="slack",
                resources=session_key,
            )
            return _ACTION_TRUST
        else:
            logger.warning("No pending approval for %s", key)
            sel().log_api_access(
                caller=user_id or "unknown",
                operation="slack.interactive.approval",
                outcome="denied",
                source="slack",
                resources=key,
                error="no_pending_approval",
            )
        return None

    if action_id in (_ACTION_APPROVE, _ACTION_TRUST):
        # Set trust state BEFORE approving (so subsequent tools auto-approve)
        if action_id == _ACTION_TRUST:
            if not is_allowed_user(user_id):
                logger.error("Rejecting trust escalation from non-allowed user %s", user_id)
                sel().log_api_access(
                    caller=user_id,
                    operation="slack.interactive.trust_denied",
                    outcome="denied",
                    source="slack",
                    resources=pending.session_key or "",
                    error="non-allowed user",
                )
                if not pending.future.done():
                    pending.future.set_result(_OUTCOME_REJECTED)
                del _pending_approvals[key]
                return _ACTION_REJECT
            elif pending.session_key:
                _trusted_sessions.add(pending.session_key)
                logger.info("Trust mode ON for session %s", pending.session_key)
            else:
                logger.warning("No session_key on pending approval %s; approving without trust", key)
        if pending.provider:
            await pending.provider.approve_tool(pending.request_id)
        if not pending.future.done():
            pending.future.set_result(_OUTCOME_APPROVED)
        sel().log_api_access(
            caller=user_id,
            operation="slack.interactive.approval",
            outcome="allowed",
            source="slack",
            resources=action_id,
        )
    else:
        if pending.provider:
            await pending.provider.reject_tool(pending.request_id)
        if not pending.future.done():
            pending.future.set_result(_OUTCOME_REJECTED)
        sel().log_api_access(
            caller=user_id,
            operation="slack.interactive.approval",
            outcome="denied",
            source="slack",
            resources=action_id,
        )

    del _pending_approvals[key]
    return action_id


#: The key the CORE stamps its approval brief onto ``event.tool_meta`` under — the value
#: of ``gideon.approval_brief.APPROVAL_BRIEF_META_KEY``. Read as a literal because
#: the brief is ADDITIVE meta the SDK does not export: a core that composes none simply
#: leaves the key absent, and this channel then prompts exactly as it did before.
_APPROVAL_BRIEF_META_KEY = "approval_brief"

#: The one facet that is NOT a consequence: ``readOnly`` claims what a call does not do,
#: so it must never be framed as something the call "can" do. Named as the EXCEPTION
#: rather than listing the consequences, so a facet core adds later is framed as a
#: consequence automatically instead of silently reading as a reassurance.
_READ_CLAIM_FACET = "readOnly"


def _approval_brief_line(event: LLMEvent) -> str:
    """One line saying what this call can TOUCH, or ``""`` to show no such line.

    Slack is the surface with no room, so the core-composed brief collapses to a single
    line next to the effective risk; the dashboard stays the rich surface (per-facet
    cards carrying each facet's ``detail`` sentence). Nothing is re-derived here — every
    word comes from ``event.tool_meta``, so a hint added to the core gate reaches Slack
    without a change in this repo, and this renderer cannot drift into a second
    vocabulary.

    Two rules, both from the ``ChannelDelivery.request_approval`` contract:

    * an ABSENT blast radius renders NO line. "Nothing was established" reads to a
      person as "nothing happens", which is the opposite of what an unrecognized tool
      means. Silence is the honest render, and the caller must not fill it.
    * only ESTABLISHED facets are ever named. ``blastRadiusLine`` already contains
      exactly the ``True`` ones, so a ``False`` can never be painted as an all-clear
      ("no network") — absence of evidence never becomes evidence of absence.

    The returned text is plain (no mrkdwn, no emoji) so the approval block and the
    notification fallback can render the same string without diverging.
    """
    meta = getattr(event, "tool_meta", None)
    if not isinstance(meta, dict):
        return ""
    brief = meta.get(_APPROVAL_BRIEF_META_KEY)
    if not isinstance(brief, dict):
        return ""
    facets = str(brief.get("blastRadiusLine") or "")
    if not facets:
        return ""
    radius = brief.get("blastRadius")
    consequence = isinstance(radius, dict) and any(
        v for k, v in radius.items() if k != _READ_CLAIM_FACET
    )
    line = f"Can: {facets}" if consequence else f"{facets[:1].upper()}{facets[1:]}"
    risk = str(brief.get("risk") or "")
    if risk:
        line += f" · Risk: {risk}"
    return line


def _build_approval_blocks(event: LLMEvent, is_dm: bool = True, source: str = "") -> list[dict]:
    """Build Block Kit blocks for tool approval prompt.

    Args:
        event: The permission-request event from the LLM provider.
        is_dm: True when posting to a DM (adds Trust button).
        source: Optional label for background agents (e.g. "subagent",
            "cron").  Prefixed to the header so users can tell main-agent
            approvals apart from background ones.

    Shows the full command text (from tool_input) in a code block so users
    can see exactly what will run before approving.  Falls back to the
    truncated title when tool_input is unavailable.

    In DMs: Approve / Trust / Reject
    In group channels: Approve / Reject only (Trust excluded
    to limit blast radius — it escalates permissions for the session).
    YOLO is owner-only via ``!yolo on`` command — no button.
    """
    buttons: list[dict] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Approve"},
            "style": "primary",
            "action_id": _ACTION_APPROVE,
            "value": event.request_id,
        },
    ]
    if is_dm:
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Trust session"},
                "action_id": _ACTION_TRUST,
                "value": event.request_id,
            },
        )
    buttons.append(
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Reject"},
            "style": "danger",
            "action_id": _ACTION_REJECT,
            "value": event.request_id,
        },
    )

    blocks: list[dict] = []

    tag = f"[{source}] " if source else ""
    title_safe, _ = redact_exfiltration_urls(event.title)
    title_safe, _ = redact_credentials(title_safe)
    footer = f":lock: {tag}*{title_safe}*"
    if event.tool_purpose:
        purpose, _ = redact_exfiltration_urls(event.tool_purpose)
        purpose, _ = redact_credentials(purpose)
        footer += f" — {purpose}"

    # When full tool_input is available, show a simple header and the
    # complete command in a code block below.
    # When tool_input is missing, fall back to the truncated title.
    if event.tool_input:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"🔐 *{tag}Tool approval requested:*"},
            },
        )
        # Security: scan for exfiltration URLs and credentials before posting
        sanitized, _ = redact_exfiltration_urls(event.tool_input)
        sanitized, _ = redact_credentials(sanitized)
        # Truncate with marker if exceeds Slack limit
        if len(sanitized) > _SLACK_SECTION_TEXT_LIMIT:
            detail = (
                sanitized[: _SLACK_SECTION_TEXT_LIMIT - len(_TRUNCATION_MARKER)]
                + _TRUNCATION_MARKER
            )
        else:
            detail = sanitized
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{detail}```"},
            },
        )

    # The blast radius goes ABOVE the buttons: it is the reason to press one, so a
    # reader must meet it before the decision, not after it.
    brief_line = _approval_brief_line(event)
    if brief_line:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": brief_line}]},
        )

    blocks.append({"type": "actions", "elements": buttons})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return blocks


def _remove_all_jobs(store: TriggerStore) -> str:
    """Remove every automation the ASSISTANT created, and return a summary.

    Scoped to `created_by="agent"` by the SDK helper. The old version removed EVERYTHING the
    scheduler held, including the automations the user built by hand — `cron remove all` from a chat
    message could wipe them with no confirmation step. The scoped delete is what the core
    `automation_delete_all` tool enforces, and this command now inherits it rather than re-deriving a
    broader blast radius.
    """
    rows = [r for r in store.load() if r.trigger.created_by == "agent"]
    if not rows:
        return "No agent-created automations to remove."
    lines = [f"- `{r.trigger.id}` — {r.trigger.name}" for r in rows]
    result = delete_all_automations(store, created_by="agent", confirm=True)
    if not result.ok:
        return f"⚠️ {result.text}"
    return f"✅ Removed {len(lines)} automation(s):\n" + "\n".join(lines)


def _handle_spawn_command(text: str, manager: SubagentManager, session_key: str = "") -> str | None:
    """Intercept spawn/bg keyword commands. Returns reply or None."""
    t = text.strip()
    low = t.lower()

    for prefix in ("spawn ", "bg "):
        if low.startswith(prefix):
            return _do_spawn(t[len(prefix) :].strip(), manager, session_key)
    return None


def _do_spawn(task: str, manager: SubagentManager, session_key: str = "") -> str | None:
    """Execute a spawn command. Returns reply string."""
    if not task:
        return None

    # "spawn list" / "spawn status"
    if task.lower() in ("list", "status"):
        running = manager.running
        if not running:
            return "No subagents running."
        lines = ["*Running subagents:*"]
        for a in running:
            elapsed = int(time.time() - a.started)
            lines.append(f"🔹 `{a.id}` | {elapsed}s | {a.task[:60]}")
        return "\n".join(lines)

    info = manager.spawn(task, parent_session_key=session_key)
    if not info:
        return f"⚠️ Subagent capacity reached ({manager.max_concurrent}). Try again later."
    return f"🚀 Spawned subagent `{info.id}`\n_{task[:100]}_"


def _relative_next_run(nxt: float | None, now: float) -> str:
    """"⏭ in 2h 15m" for a next-run timestamp, or "" when there is none."""
    if nxt is None:
        return ""
    delta = nxt - now
    if delta >= 86400:
        rel = f"in {int(delta // 86400)}d {int((delta % 86400) // 3600)}h"
    elif delta >= 3600:
        rel = f"in {int(delta // 3600)}h {int((delta % 3600) // 60)}m"
    elif delta > 0:
        minutes = int(delta // 60)
        rel = f"in {minutes}m" if minutes >= 1 else "in <1m"
    else:
        rel = "now"
    return f" | ⏭ {rel}"


def _handle_cron_command(
    text: str, store: TriggerStore, channel: str, thread_ts: str
) -> str | None:
    """Handle cron keyword commands against the unified trigger store. Returns reply or None.

    🔴 Re-pointed off `ScheduleService`, which core deleted (S112). That service read
    `crons.json` — a file nothing has written since core's S108 — so **every one of these commands
    was already broken**: `cron list` showed an empty list to a user with live automations, and
    remove/pause/resume answered "not found" for every real id. Measured against a store-only home
    before re-pointing.

    Reads the same store, projection and tool functions the Automations page and the `automation_*`
    chat tools use, so a `/cron` reply can no longer disagree with the UI.

    The surface is unchanged for the user, with two honest improvements the store makes possible:
    a BROKEN automation is listed with its parse error rather than silently omitted, and every kind
    is visible (a file watch or an event trigger used to be invisible here because the legacy
    scheduler only held clocks).
    """
    t = text.strip().lower()
    parts = t.split()

    if len(parts) < 2 or parts[0] != "cron":
        return None

    action = parts[1]

    if action == "list":
        rows = store.load()
        if not rows:
            return "No automations scheduled."
        lines = ["*Your automations:*"]
        now = time.time()
        for row in rows:
            trigger = row.trigger
            # A row that failed to parse is SHOWN with its reason. The legacy list could not
            # represent one at all, and silently omitting an automation the user created is how
            # "where did my automation go" happens.
            if not row.ok:
                reason = row.errors[0].message if row.errors else "invalid"
                lines.append(f"⚠️ `{trigger.id}` | {reason}")
                continue
            view = to_schedule_row(trigger)
            status = "✅" if trigger.enabled else "⏸️"
            last = ""
            if view.get("last_status") == "ok":
                last = " ✓"
            elif view.get("last_status") in ("error", "failure", "timeout"):
                last = " ❌"
            safe_msg, _ = redact_credentials(
                redact_exfiltration_urls(str(view.get("message") or ""))[0]
            )
            next_part = _relative_next_run(view.get("next_run_ts"), now)
            lines.append(
                f"{status} `{trigger.id}` | `{describe_cadence(trigger)}` "
                f"| {safe_msg[:50]}{last}{next_part}"
            )
        return "\n".join(lines)

    if len(parts) < 3:
        return None

    job_id = parts[2]

    if action == "remove":
        if job_id == "all":
            return _remove_all_jobs(store)
        # `confirm=True`: the flag exists so a TOOL CALL cannot delete by accident. A user who typed
        # `cron remove <id>` has already expressed the intent.
        if delete_automation(store, trigger_id=job_id, confirm=True).ok:
            return f"✅ Removed automation `{job_id}`"
        return f"❌ Automation `{job_id}` not found"

    if action == "pause":
        if set_automation_paused(store, trigger_id=job_id, paused=True).ok:
            return f"⏸️ Paused automation `{job_id}`"
        return f"❌ Automation `{job_id}` not found"

    if action == "resume":
        result = set_automation_paused(store, trigger_id=job_id, paused=False)
        if result.ok:
            return f"▶️ Resumed automation `{job_id}`"
        # `set_paused` REFUSES to resume a row with a parse error and NAMES it — strictly more useful
        # than "not found", and the row does exist, so "not found" was wrong as well as unhelpful.
        return f"❌ {result.text}"

    return None


async def _handle_sessions_command(
    cmd_text: str,
    slack_desk: SlackDeskClientOps,
    channel: str,
    reply_ts: str,
    msg_ts: str,
    session_key: str,
    conversation_log: ConversationLog | None,
) -> None:
    """Handle ``!sessions`` — list recent sessions as task_card blocks with resume buttons."""
    import json as _json
    from pathlib import Path

    sess_dir = Path.home() / ".gideon" / "sessions"
    if not sess_dir.exists():
        await slack_desk.post_message(channel, "_No recent sessions._", reply_ts)
        return

    max_msg_chars = 4000
    sessions: list[dict] = []
    for jsonl in sess_dir.glob("*.jsonl"):
        if jsonl.is_symlink():
            continue
        key = jsonl.stem
        if key.startswith("dashboard_"):
            key = "dashboard:" + key[len("dashboard_"):]
        try:
            lines = jsonl.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue

        title = key
        agent = "gideon"
        msgs: list[tuple[str, str]] = []
        mtime = jsonl.stat().st_mtime

        for line in lines:
            try:
                d = _json.loads(line.strip())
            except (ValueError, _json.JSONDecodeError):
                continue
            if d.get("_type") == "metadata":
                title = d.get("title") or title
                agent = d.get("agent") or agent
                continue
            role = d.get("role", "")
            txt = (d.get("content") or "")[:max_msg_chars]
            if role in ("user", "assistant") and txt:
                msgs.append((role, txt))

        sessions.append({
            "key": key,
            "title": title[:80],
            "agent": agent,
            "mtime": mtime,
            "msgs": msgs[-5:],
        })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    sessions = sessions[:10]

    sel().log_api_access(
        caller=session_key,
        operation="slack.sessions_data_access",
        outcome="allowed",
        source="slack",
        resources=f"{len(sessions)} sessions read",
    )

    if not sessions:
        await slack_desk.post_message(channel, "_No recent sessions._", reply_ts)
        return

    blocks: list[dict] = []
    for i, s in enumerate(sessions):
        rt_items: list[dict] = []
        for role, txt in s["msgs"]:
            txt, _ = redact_exfiltration_urls(txt)
            txt, _ = redact_credentials(txt)
            emoji_name = "bust_in_silhouette" if role == "user" else "robot_face"
            rt_items.append({
                "type": "rich_text_section",
                "elements": [
                    {"type": "emoji", "name": emoji_name},
                    {"type": "text", "text": f" {txt}"},
                ],
            })

        _title, _ = redact_exfiltration_urls(s["title"])
        _title, _ = redact_credentials(_title)
        task: dict = {
            "type": "task_card",
            "task_id": f"session_{i}",
            "title": f"{_title} — {s['agent']} agent",
            "status": "complete",
        }
        if rt_items:
            task["details"] = {
                "type": "rich_text",
                "elements": [{"type": "rich_text_list", "style": "bullet", "elements": rt_items}],
            }
        blocks.append(task)
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "\u25b6\ufe0f Resume"},
                "action_id": f"pc_session_resume_{s['key']}",
                "value": _json.dumps({"key": s["key"], "title": s["title"]}),
            }],
        })
        if i < len(sessions) - 1:
            blocks.append({"type": "divider"})

    await slack_desk.post_blocks(channel, blocks, "Recent sessions:", reply_ts)


async def _safe_update(slack_desk: SlackDeskClientOps, channel: str, ts: str, text: str) -> None:
    """Update a Slack message, truncating if too long.

    Used for progressive streaming edits — truncation is fine here since
    the final message uses _safe_final_update which splits instead.
    """
    text, _ = redact_exfiltration_urls(text)
    if len(text) > SLACK_MSG_LIMIT:
        text = text[:SLACK_MSG_LIMIT] + TRUNCATION_NOTICE
    try:
        await slack_desk.update_message(channel, ts, text)
    except Exception:
        logger.debug("Failed to update message %s", ts, exc_info=True)


async def _safe_final_update(
    slack_desk: SlackDeskClientOps, channel: str, ts: str, text: str, thread_ts: str | None = None
) -> None:
    """Final message update — splits into multiple messages if too long."""
    text, _ = redact_exfiltration_urls(text)
    parts = split_message(text)
    # First part updates the existing streaming message
    try:
        await slack_desk.update_message(channel, ts, parts[0])
    except Exception:
        logger.debug("Failed to update message %s", ts, exc_info=True)
    # Overflow parts posted as follow-up messages in the same thread
    for part in parts[1:]:
        try:
            await slack_desk.post_message(channel, part, thread_ts)
        except Exception:
            logger.debug("Failed to post continuation message", exc_info=True)
