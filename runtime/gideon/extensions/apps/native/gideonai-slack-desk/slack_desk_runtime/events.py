"""Slack Socket Mode event routing.

Sets up the Socket Mode client, dispatches incoming events to the
correct handler:

- ``interactive`` → :mod:`interactions.dispatch`
- ``slash_commands`` → registry-based sub-command routing
- ``member_joined_channel`` → tracking-channel allowlist prompt
- ``app_home_opened`` → publish Home Tab view
- ``message`` / ``app_mention`` → :func:`handler.handle_message`

Also contains the bounded dedup cache (``_SeenCache``) that prevents
processing the same Slack event twice.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import aiohttp
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.websockets import SocketModeClient as WSSocketModeClient
from slack_sdk.web.async_client import AsyncWebClient

from gideon.sdk.channel import AppConfig
from slack_desk_runtime.settings import (
    ACTIVATION_MENTION,
    ACTIVATION_OBSERVE,
    ACTIVATION_OFF,
    ACTIVATION_REVIEW,
)
from gideon.sdk.channel import TriggerStore, config_dir, describe_cadence
from gideon.sdk.channel import parse_duration
from gideon.sdk.channel import list_servers
from gideon.sdk.channel import redact_credentials, redact_exfiltration_urls
from gideon.sdk.channel import sel
from gideon.sdk.channel import ProcedureLibrary as SkillsLoader
from slack_desk_runtime.allowlist import prompt_track_channel, send_dashboard_link, sync_channel_trust
from slack_desk_runtime.enterprise import check_message_origin, validate_enterprise
from slack_desk_runtime.files import process_slack_desk_files
from slack_desk_runtime.handler import (
    APPROVAL_INTERACTIVE,
    claim_owner,
    get_owner_id,
    handle_message,
    is_allowed_user,
    is_open_channel,
    is_owner,
    is_tracked_channel,
    is_yolo_mode,
    set_allowed_users,
    set_dashboard_state,
    set_gateway_services,
    set_open_channels,
    set_orch_cfg,
    set_owner_id,
    set_tracking_channels,
    set_yolo_mode,
)
from slack_desk_runtime.interactions import dispatch as dispatch_interactive
from gideon.sdk.channel import Stats
from gideon.sdk.channel import stt_available

if TYPE_CHECKING:
    from gideon.sdk.channel import GatewayServices

logger = logging.getLogger(__name__)

_skills_loader: SkillsLoader | None = None


def _get_skills_loader() -> SkillsLoader:
    global _skills_loader  # noqa: PLW0603
    if _skills_loader is None:
        _skills_loader = SkillsLoader()
    return _skills_loader


def publish_inbound_for_trigger_source(
    *, channel: str, text: str, sender_id: str, thread_ts: str, msg_ts: str
) -> int:
    """Normalize one ADMITTED message and hand it to this bundle's inbound tap (CE-10).

    Lives here rather than in ``inbound_tap`` so the tap stays vendor-neutral: it moves a
    :class:`~gideon.sdk.channel.ChannelMessage` and knows nothing about Slack's event
    shape. Called from exactly one place — see the comment at that call site for why it is
    that place and not an earlier one.

    ``is_dm`` is derived from the channel id's ``D`` prefix, which is Slack's own DM signal
    and the same test ``handler.py``'s guarded-door call uses. STRUCTURAL on purpose: the
    trigger source picks its event name from this, so it must not be readable out of the
    message body.

    Never raises. The caller is about to start a turn, and a broken source must not cost the
    user their conversation. Returns the observer count the tap reported so a caller can log
    honestly; today nothing reads it, which is why there is no log line here rather than a
    misleading one.
    """
    try:
        from gideon.sdk.channel import ChannelMessage

        from slack_desk_runtime.inbound_tap import publish

        message = ChannelMessage(
            channel_id=channel,
            text=text,
            sender=sender_id,
            thread_id=thread_ts or msg_ts,
            message_id=msg_ts,
            # Deliberately EMPTY. The trigger source reads no metadata, and a display name
            # here would be attacker-chosen prose sitting one refactor away from an unfenced
            # `meta` field. See trigger_source.py's rule 3.
            metadata={},
        )
        return publish(message, is_dm=channel.startswith("D"))
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("slack: trigger-source publish failed", exc_info=True)
        return 0


# Suppress noisy Slack SDK WebSocket reconnect errors — these are normal
# idle connection drops that the SDK handles automatically.
# WARNING lets ERROR through (recv failures, reconnect failures) while
# suppressing INFO (session established) and DEBUG (every message/ping).
logging.getLogger("slack_sdk.socket_mode.websockets").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Dedup cache — bounded LRU to avoid processing duplicate Slack events
# ---------------------------------------------------------------------------

_MAX_SEEN = 5000

# prevent GC of fire-and-forget tasks (Python event loop holds weak refs)
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


class SeenCache:
    """Bounded set that remembers the last *maxlen* event IDs."""

    def __init__(self, maxlen: int = _MAX_SEEN):
        self._d: OrderedDict[str, None] = OrderedDict()
        self._maxlen = maxlen

    def check_and_add(self, key: str) -> bool:
        """Return ``True`` if *key* was already seen, else mark it."""
        if key in self._d:
            return True
        self._d[key] = None
        if len(self._d) > self._maxlen:
            self._d.popitem(last=False)
        return False


# ---------------------------------------------------------------------------
# Slash command registry
# ---------------------------------------------------------------------------

# Handler signature: async def handler(orch, caller_id, args, respond) -> None
SlashHandler = Callable[["GatewayServices", str, str, Callable], Coroutine[Any, Any, None]]

SLASH_REGISTRY: dict[str, tuple[SlashHandler, str]] = {}


def register_slash_command(name: str, handler: SlashHandler, description: str = "") -> None:
    """Register a sub-command for ``/gideon <name>``."""
    SLASH_REGISTRY[name] = (handler, description)


def _build_help_text(cmd_name: str = "gideon") -> str:
    """Build help message listing all registered sub-commands."""
    lines = ["*Available commands:*"]
    for name, (_, desc) in sorted(SLASH_REGISTRY.items()):
        lines.append(f"• `/{cmd_name} {name}` — {desc}" if desc else f"• `/{cmd_name} {name}`")
    lines.append(f"• `/{cmd_name} #channel` — track/untrack channel")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in slash sub-command handlers
# ---------------------------------------------------------------------------


async def _handle_dashboard(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Generate presigned dashboard link and DM to caller."""
    from gideon.sdk.channel import LINK_WINDOW_SECS, MAX_SESSION_TTL_SECS
    from slack_desk_runtime.blocks import dashboard_link_block

    ttl = 3600
    if args:
        parsed = parse_duration(args.split()[0])
        if parsed is None:
            await respond(f"Usage: `/{orch.slack_desk_command} dashboard [<N>h|<N>m]`")
            return
        ttl = parsed

    session_ttl = min(ttl, MAX_SESSION_TTL_SECS)
    assert orch.slack_desk is not None
    url = await send_dashboard_link(orch.slack_desk, caller_id, session_ttl)
    if url:
        blks = dashboard_link_block(url, LINK_WINDOW_SECS // 60, session_ttl // 60)
        await respond("🔗 Dashboard link sent to your DMs.", blocks=blks)
    else:
        await respond("❌ Failed to send dashboard link.")


async def _handle_agent(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Switch agent directly if valid name given, otherwise show selector."""
    from slack_desk_runtime.handler import (
        _get_default_agent,
        _resolve_agent_name,
        _set_default_agent,
        is_owner,
    )

    if not is_owner(caller_id):
        await respond("⛔ Only the owner can switch agents.")
        return

    # Direct switch if arg provided
    if args:
        name = args.strip().split()[0]
        if name.lower() in ("off", "default"):
            _set_default_agent("")
            await respond("🔄 Reset to default agent.")
            return
        resolved = _resolve_agent_name(name)
        if resolved:
            _set_default_agent(resolved)
            await respond(f"🔄 Switched to agent: *{resolved}*")
            return
        await respond(f"❌ Unknown agent `{name}`. Pick one below:")

    # Show selector dropdown
    from pathlib import Path

    agents_dir = Path.home() / ".gideon" / "agents"
    jsons = sorted(agents_dir.glob("*.json")) if agents_dir.is_dir() else []
    agent_names = sorted(f.stem for f in jsons)
    current = _get_default_agent() or ""

    options = [{"text": {"type": "plain_text", "text": n[:75]}, "value": n} for n in agent_names]
    options.append({"text": {"type": "plain_text", "text": "off (default)"}, "value": "off"})
    initial = next((o for o in options if o["value"] == current), options[-1])

    blks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Current agent:* {current or 'default'}"},
            "accessory": {
                "type": "static_select",
                "action_id": "pc_agent_select",
                "options": options,
                "initial_option": initial,
            },
        },
    ]
    await respond("Select an agent:", blocks=blks)


async def _handle_voice(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Open voice config modal with current TTS settings."""
    from slack_desk_runtime.blocks import voice_config_modal
    from slack_desk_runtime.handler import _vc

    trigger_id = getattr(orch, "_last_trigger_id", "")
    if not trigger_id:
        await respond("❌ Missing trigger_id — cannot open modal.")
        return

    from gideon.sdk.channel import active_voice_params

    _params = active_voice_params()
    modal = voice_config_modal(
        tts_enabled=_vc.global_enabled,
        auto_speak=getattr(_vc, "auto_speak", False),
        length_scale=_params["speed"] if _params else 1.0,
    )

    try:
        assert orch.slack_desk is not None
        await orch.slack_desk.views_open(trigger_id=trigger_id, view=modal)
    except Exception:
        logger.exception("Failed to open voice config modal")
        await respond("❌ Failed to open voice settings modal.")


register_slash_command("dashboard", _handle_dashboard, "get a dashboard access link")
register_slash_command("agent", _handle_agent, "switch the active agent")
register_slash_command("voice", _handle_voice, "configure TTS voice settings")


async def _handle_yolo(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Toggle YOLO mode on/off."""
    if not is_owner(caller_id):
        await respond("⛔ Only the owner can toggle YOLO mode.")
        return

    arg = args.strip().lower()
    if arg == "on":
        from gideon.sdk.channel import trust_mode
        from slack_desk_runtime.handler import _YOLO_TTL_SECS

        if trust_mode.yolo_from_config():
            sel().log_api_access(caller=caller_id, operation="slack.yolo_mode", outcome="noop_config_permanent", source="slack", resources="yolo_on")
            await respond("🟢 YOLO mode is already *permanently ON* from config (`agent.yolo=true`). No action needed.")
            return
        # One canonical trust state (gideon.trust_mode) — enabling it here is
        # what the dashboard reads too; just refresh the dashboard's session view.
        trust_mode.enable_yolo(ttl_secs=_YOLO_TTL_SECS)
        sel().log_api_access(caller=caller_id, operation="slack.yolo_mode", outcome="allowed", source="slack", resources="yolo_on")
        if orch.dashboard_state:
            orch.dashboard_state.push_sessions_update()
        await respond(f"🟢 YOLO mode *ON* (auto-expires in {_YOLO_TTL_SECS // 60}min) — all tools auto-approved.")
    elif arg == "off":
        from gideon.sdk.channel import trust_mode
        trust_mode.disable_yolo()
        sel().log_api_access(caller=caller_id, operation="slack.yolo_mode", outcome="allowed", source="slack", resources="yolo_off")
        if orch.dashboard_state:
            orch.dashboard_state.push_sessions_update()
        await respond("🔴 YOLO mode *OFF* — tools require approval.")
    else:
        state = "ON 🟢" if is_yolo_mode() else "OFF 🔴"
        await respond(
            f"YOLO mode is currently *{state}*.\nUsage: `/{orch.slack_desk_command} yolo on` or `/{orch.slack_desk_command} yolo off`"
        )


async def _handle_config(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Open config modal (owner-only) — users and channels."""
    if not is_owner(caller_id):
        await respond("⛔ Only the owner can change config.")
        return

    tracking_ids = list(orch._tracking_channels)

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "⚠️ Multi-user access is disabled for security. Only the owner can interact via Slack.",
            },
        },
        {
            "type": "input",
            "block_id": "channels_block",
            "label": {"type": "plain_text", "text": "Tracked Channels"},
            "element": {
                "type": "multi_channels_select",
                "action_id": "pc_config_channels",
                "placeholder": {"type": "plain_text", "text": "Select channels"},
                **({"initial_channels": tracking_ids} if tracking_ids else {}),
            },
            "optional": True,
        },
    ]

    view = {
        "type": "modal",
        "callback_id": "pc_config_panel",
        "title": {"type": "plain_text", "text": "Gideon Config"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }

    trigger_id = getattr(orch, "_last_trigger_id", "")
    if not trigger_id:
        await respond("⚠️ Cannot open modal — missing trigger_id.")
        return

    try:
        assert orch.slack_desk is not None
        await orch.slack_desk.views_open(trigger_id=trigger_id, view=view)
    except Exception:
        logger.exception("Failed to open config modal")
        await respond("❌ Failed to open config modal.")


register_slash_command("yolo", _handle_yolo, "toggle YOLO mode (auto-approve tools)")
register_slash_command("config", _handle_config, "manage users and channels (owner-only)")


async def _handle_allowlist_cmd(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Multi-user access disabled — user management is blocked."""
    await respond("⛔ Multi-user access is disabled for security. Only the owner can use Gideon via Slack.")


def _get_agent_names() -> list[str]:
    """Return sorted list of installed agent names from ~/.gideon/agents/.

    Reads each agent JSON's ``name`` field via ``hooks.safe_read_file`` so
    symlinks into sensitive paths (e.g. ``~/.aws/credentials``) are blocked
    by ``is_sensitive_path()``. Falls back to the filename stem when the
    file cannot be read safely or the JSON does not carry a usable name.

    When a read is blocked by ``is_sensitive_path()``, a SEL audit event
    (``sensitive_path_blocked``) is emitted so the attempt is observable.
    """
    import json
    from pathlib import Path

    from gideon.sdk.channel import safe_read_file

    agents_dir = Path.home() / ".gideon" / "agents"
    if not agents_dir.is_dir():
        return []
    names = []
    for f in agents_dir.glob("*.json"):
        try:
            data = json.loads(safe_read_file(str(f)))
            name = data.get("name") if isinstance(data, dict) else None
        except PermissionError as exc:
            # Symlink or resolved path landed in a sensitive location — audit it.
            try:
                sel().log_api_access(
                    caller="system",
                    operation="sensitive_path_blocked",
                    outcome="denied",
                    source="slack.events._get_agent_names",
                    resources=str(f),
                    error=str(exc),
                )
            except Exception:
                logger.debug(
                    "Failed to emit SEL audit event for blocked agent read: %s",
                    f,
                    exc_info=True,
                )
            name = None
        except (json.JSONDecodeError, OSError):
            name = None
        names.append(name or f.stem)
    return sorted(names)


async def _handle_channel_cmd(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Open modal showing tracked channels with per-channel activation mode."""
    if not is_owner(caller_id):
        await respond("⛔ Only the owner can manage tracked channels.")
        return

    from slack_desk_runtime.blocks import channels_modal

    current_ids = sorted(orch._tracking_channels)
    channels = [
        {
            "channel_id": cid,
            "activation": orch.settings.channel_config(cid).activation,
            "agent": orch.settings.channel_config(cid).agent,
        }
        for cid in current_ids
    ]
    agent_names = _get_agent_names()
    modal = channels_modal(channels, agent_names=agent_names)

    trigger_id = getattr(orch, "_last_trigger_id", "")
    if not trigger_id:
        await respond("⚠️ Cannot open modal — missing trigger_id.")
        return
    try:
        assert orch.slack_desk is not None
        await orch.slack_desk.views_open(trigger_id=trigger_id, view=modal)
    except Exception:
        logger.exception("Failed to open channels modal")
        await respond("❌ Failed to open channels modal.")


register_slash_command("users", _handle_allowlist_cmd, "manage allowed users")
register_slash_command("channels", _handle_channel_cmd, "manage tracked channels")


async def _handle_sessions(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """List last 10 sessions as task_card blocks with resume buttons."""
    import json
    from pathlib import Path

    sess_dir = Path.home() / ".gideon" / "sessions"
    if not sess_dir.exists():
        await respond("_No recent sessions._")
        return

    MAX_MSG_CHARS = 4000  # noqa: N806
    from gideon.sdk.channel import redact_credentials, redact_exfiltration_urls
    sessions: list[dict] = []
    for jsonl in sess_dir.glob("*.jsonl"):
        key = jsonl.stem
        # Restore canonical key: filenames use underscore (dashboard_chat-1-xxx)
        # but session keys use colon (dashboard:chat-1-xxx)
        if key.startswith("dashboard_"):
            key = "dashboard:" + key[len("dashboard_") :]
        try:
            lines = jsonl.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue

        title = key
        agent = "gideon"
        msgs: list[tuple[str, str]] = []  # (role, content)
        mtime = jsonl.stat().st_mtime

        for line in lines:
            try:
                d = json.loads(line.strip())
            except (ValueError, json.JSONDecodeError):
                continue
            if d.get("_type") == "metadata":
                title = d.get("title") or title
                agent = d.get("agent") or agent
                continue
            role = d.get("role", "")
            txt = redact_credentials(redact_exfiltration_urls((d.get("content") or "")[:MAX_MSG_CHARS])[0])[0]
            if role in ("user", "assistant") and txt:
                msgs.append((role, txt))

        if title == key:
            if key.startswith("dashboard_"):
                title = f"Dashboard {key.split('_', 1)[1]}"

        active = orch.sessions.has_session(key) if orch.sessions else False
        sessions.append(
            {
                "key": key,
                "title": title[:80],
                "agent": agent,
                "mtime": mtime,
                "active": active,
                "msgs": msgs[-5:],
            }
        )

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    sessions = sessions[:10]

    if not sessions:
        await respond("_No recent sessions._")
        return

    blocks: list[dict] = []
    for i, s in enumerate(sessions):
        if s["active"]:
            emoji, status = "\U0001f7e2", "in_progress"
        else:
            emoji, status = "\u26ab", "complete"

        # Build rich_text_list for messages
        rt_items: list[dict] = []
        for role, txt in s["msgs"]:
            emoji_name = "bust_in_silhouette" if role == "user" else "robot_face"
            rt_items.append(
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "emoji", "name": emoji_name},
                        {"type": "text", "text": f" {txt}"},
                    ],
                }
            )

        task: dict = {
            "type": "task_card",
            "task_id": f"session_{i}",
            "title": f"{emoji} {s['title']} — {s['agent']} agent",
            "status": status,
        }
        if rt_items:
            task["details"] = {
                "type": "rich_text",
                "elements": [{"type": "rich_text_list", "style": "bullet", "elements": rt_items}],
            }
        blocks.append(task)
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "\u25b6\ufe0f Resume"},
                        "action_id": f"pc_session_resume_{s['key']}",
                        "value": json.dumps({"key": s["key"], "title": s["title"]}),
                    }
                ],
            }
        )
        if i < len(sessions) - 1:
            blocks.append({"type": "divider"})

    await respond("\U0001f4cb Recent sessions:", blocks=blocks)


register_slash_command("sessions", _handle_sessions, "list recent sessions")


async def _handle_status(
    orch: "GatewayServices", caller_id: str, args: str, respond: Callable
) -> None:
    """Show runtime stats summary."""
    from gideon.sdk.channel import Stats

    await respond(Stats().summary())


register_slash_command("status", _handle_status, "show runtime stats")


# ---------------------------------------------------------------------------
# Socket Mode setup
# ---------------------------------------------------------------------------


def init_socket_mode(orch: "GatewayServices", seen: SeenCache) -> None:
    """Wire up the Socket Mode client and attach the event listener.

    Does nothing when Slack is disabled (either token missing). An EMPTY allowlist does
    not stop the socket — it starts so a first DM can arrive and claim ownership, and
    every message is then refused by ``is_allowed_user`` until someone is authorized.
    Mutates ``orch._socket_client`` in place.
    """
    if not orch._slack_desk_enabled:
        return

    # No preset owner → run in trust-on-first-use bootstrap: the socket still
    # starts (so an inbound DM can arrive), and the first human to DM the bot is
    # auto-claimed as owner (see _route_message → claim_owner). Multi-user is still
    # disabled — exactly one owner ever exists; this only bootstraps who it is.
    if not orch._owner_id:
        logger.warning(
            "No Slack owner set — starting in owner-claim mode: the first user to "
            "DM the bot becomes its owner."
        )

    # Share owner-only allowlist and tracking channels with handler modules
    set_allowed_users(orch._allowed_users)
    set_tracking_channels(orch._tracking_channels)
    set_open_channels(orch._open_channels)
    set_owner_id(orch._owner_id)
    set_gateway_services(orch._services)
    # EA-7: mirror the owner-only posture into core's channel_trust store — the
    # guarded inbound door the linked-thread intercept routes through consults
    # core, not SlackDeskSettings. Idempotent: writes only what is missing.
    sync_channel_trust(orch._owner_id, orch._tracking_channels)
    if orch._cfg.agent.yolo:
        set_yolo_mode(True)
    set_orch_cfg(orch._cfg)
    if orch.dashboard_state:
        set_dashboard_state(orch.dashboard_state)

    # ── Enterprise Grid workspace validation ──
    # Blocks data exfiltration via personal/external Slack workspaces.
    extra_ids = orch.settings.enterprise_ids()
    if not validate_enterprise(orch._bot_token, extra_ids=extra_ids):
        logger.error("Slack workspace validation failed (auth.test) — Slack disabled")
        orch._slack_desk_enabled = False
        orch.slack_desk = None
        return

    web_client = AsyncWebClient(token=orch._bot_token)
    orch._socket_client = WSSocketModeClient(
        app_token=orch._app_token,
        web_client=web_client,
    )

    async def _on_event(client: WSSocketModeClient, req: SocketModeRequest) -> None:
        # Always ack immediately so Slack doesn't retry
        try:
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        except Exception:
            logger.debug("Failed to ack event (WebSocket not ready), skipping")
            return

        if req.type == "interactive":
            t = asyncio.create_task(dispatch_interactive(req.payload or {}))
            orch._handler_tasks.add(t)
            t.add_done_callback(orch._handler_tasks.discard)
            return

        if req.type == "slash_commands":
            payload = req.payload or {}
            t = asyncio.create_task(_handle_slash(orch, payload))
            orch._handler_tasks.add(t)
            t.add_done_callback(orch._handler_tasks.discard)
            return

        if req.type != "events_api":
            return

        event = (req.payload or {}).get("event", {})
        event_type = event.get("type")

        # ── Tracking-channel join → allowlist prompt ──
        if event_type == "member_joined_channel":
            _maybe_prompt_owner(orch, event)
            return

        # ── Home Tab ──
        if event_type == "app_home_opened":
            user = event.get("user")
            if event.get("tab") == "home" and user:
                if is_allowed_user(user):
                    sel().log_api_access(
                        caller=user,
                        operation="slack.home_tab",
                        outcome="allowed",
                        source="slack",
                    )
                    task = asyncio.ensure_future(_publish_home_tab(orch, user))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
                else:
                    sel().log_api_access(
                        caller=user,
                        operation="slack.home_tab",
                        outcome="denied",
                        source="slack",
                        error="unauthorized sender",
                    )
            return

        # ── Messages and mentions ──
        if event_type not in ("message", "app_mention"):
            return
        _bot_id = event.get("bot_id")
        _trusted = _bot_id and _bot_id in orch.settings.trusted_bot_ids
        _self_id = getattr(orch, "_self_bot_id", None)
        # Sentinel cooldown: reset stale sentinel after 60s so auth_test is retried
        if _self_id == "" and time.monotonic() - getattr(orch, "_self_bot_id_ts", 0) > 60:
            del orch._self_bot_id  # type: ignore[attr-defined]
            orch._auth_test_failures = 0  # type: ignore[attr-defined]
            _self_id = None
        if _trusted and (_bot_id == _self_id or _self_id == ""):
            sel().log_api_access(
                caller=_bot_id,
                operation="slack.message",
                outcome="denied",
                source="slack",
                error="self_echo_guard" if _bot_id == _self_id else "unknown_self_id",
            )
            return  # fail-closed: reject when own identity unknown or matched
        _subtype = event.get("subtype")
        # ── message_deleted: cancel queued or in-flight messages ──
        if _subtype == "message_deleted":
            await _handle_message_deleted(orch, event)
            return
        if _subtype and _subtype not in ("file_share", "bot_message" if _trusted else ""):
            return
        if _bot_id and not _trusted:
            sel().log_api_access(
                caller=_bot_id,
                operation="slack.message",
                outcome="denied",
                source="slack",
                error="untrusted_bot",
            )
            return
        if _trusted and not hasattr(orch, "_self_bot_id"):
            # Lazily cache own bot_id — double-checked locking for concurrency
            _lock: asyncio.Lock = getattr(orch, "_auth_test_lock", None) or asyncio.Lock()
            orch._auth_test_lock = _lock  # type: ignore[attr-defined]
            async with _lock:
                if not hasattr(orch, "_self_bot_id"):  # re-check after lock
                    try:
                        resp = await client.web_client.auth_test()
                        own_id = resp.get("bot_id")
                        if not own_id:
                            logger.warning("auth_test() returned no bot_id — caching sentinel")
                            orch._self_bot_id = ""  # type: ignore[attr-defined]
                            orch._self_bot_id_ts = time.monotonic()  # type: ignore[attr-defined]
                            sel().log_api_access(
                                caller=_bot_id,
                                operation="slack.message",
                                outcome="denied",
                                source="slack",
                                error="auth_test_no_bot_id",
                            )
                            return
                        orch._self_bot_id = own_id  # type: ignore[attr-defined]
                        if _bot_id == orch._self_bot_id:  # type: ignore[attr-defined]
                            sel().log_api_access(
                                caller=_bot_id,
                                operation="slack.message",
                                outcome="denied",
                                source="slack",
                                error="self_echo_guard",
                            )
                            return
                    except Exception:
                        _fails = getattr(orch, "_auth_test_failures", 0) + 1
                        orch._auth_test_failures = _fails  # type: ignore[attr-defined]
                        if _fails >= 3:
                            logger.warning("auth_test() failed %d times — caching sentinel", _fails)
                            orch._self_bot_id = ""  # type: ignore[attr-defined]
                            orch._self_bot_id_ts = time.monotonic()  # type: ignore[attr-defined]
                        else:
                            logger.warning("auth_test() failed (%d/3) — will retry", _fails)
                        sel().log_api_access(
                            caller=_bot_id,
                            operation="slack.message",
                            outcome="denied",
                            source="slack",
                            error="auth_test_failed",
                        )
                        return

        # Post-init self-echo re-check: coroutines that skipped the init block
        # (another coroutine completed it) still need to compare against the
        # now-cached value, since their pre-lock _self_id may be stale (None).
        if _trusted and _bot_id == getattr(orch, "_self_bot_id", None):
            sel().log_api_access(
                caller=_bot_id,
                operation="slack.message",
                outcome="denied",
                source="slack",
                error="self_echo_guard",
            )
            return

        # Enterprise Grid: envelope team_id is the *bot's* workspace;
        # event["team"] may be the *sender's* workspace in shared channels.
        # Always prefer envelope to prevent cross-workspace bypass.
        outer_team = (req.payload or {}).get("team_id", "")
        if outer_team:
            event["team"] = outer_team
        elif not event.get("team"):
            logger.warning(
                "Enterprise Grid: no team_id from event or envelope "
                "(sender=%s) — rejecting",
                event.get("user", "unknown"),
            )
            sel().log_api_access(
                caller=event.get("user", "unknown"),
                operation="slack.message",
                outcome="denied",
                source="slack",
                error="missing_team_id",
            )
            return

        await _route_message(
            orch,
            event,
            seen,
            is_mention=(event_type == "app_mention"),
            from_trusted_bot=bool(_trusted),
        )

    orch._socket_client.socket_mode_request_listeners.append(_on_event)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Home Tab
# ---------------------------------------------------------------------------


async def _publish_home_tab(orch: "GatewayServices", user_id: str) -> None:
    """Build and publish the Block Kit Home Tab view."""
    try:
        blocks: list[dict] = []

        # ── Data handling reminder ──
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":warning: *Review your organization's AI usage policies"
                        " before sharing sensitive data with Gideon.*"
                    ),
                },
            }
        )
        blocks.append({"type": "divider"})

        # ── Status ──
        yolo = is_yolo_mode()
        blocks.append(
            {"type": "header", "text": {"type": "plain_text", "text": "Gideon Status"}}
        )
        status_lines = [
            "*Gateway:* ✅ Online",
            f"*YOLO mode:* {'🟢 ON' if yolo else '🔴 OFF'}",
        ]
        if orch.sessions is not None:
            status_lines.append(f"*Active sessions:* {orch.sessions.count}")
        status_lines.append(f"*Uptime:* {Stats().uptime_str()}")
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(status_lines)}}
        )
        blocks.append({"type": "divider"})

        # ── Capabilities ──
        blocks.append(
            {"type": "header", "text": {"type": "plain_text", "text": "🔌 Capabilities"}}
        )
        try:
            servers = list_servers()
            skills = _get_skills_loader().list_skills()
            cap_lines: list[str] = []
            if servers:
                names = ", ".join(s.name for s in servers)
                raw = f"*MCP Integrations ({len(servers)}):* {names}"
                cap_lines.append(
                    redact_credentials(redact_exfiltration_urls(raw)[0])[0]
                )
            if skills:
                names = ", ".join(s["name"] for s in skills)
                raw = f"*Skills ({len(skills)}):* {names}"
                cap_lines.append(
                    redact_credentials(redact_exfiltration_urls(raw)[0])[0]
                )
            if not cap_lines:
                cap_lines.append("_No MCP servers or skills configured._")
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(cap_lines)}}
            )
        except Exception:
            logger.error("Failed to load capabilities for home tab", exc_info=True)
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "_Capabilities unavailable._"}}
            )
        blocks.append({"type": "divider"})

        # ── Automations ──
        #
        # 🔴 Read the unified TRIGGER STORE (S112). This asked `orch.cron_svc`, which core deleted —
        # and that service described `crons.json`, a file nothing has written since core's S108. So
        # the status card showed "_No cron jobs._" to a user with live automations, and every
        # file-watch or event trigger was invisible here because the legacy scheduler only held
        # clocks. There is no "service unavailable" branch any more: a store is a file, so the honest
        # empty state is "no automations".
        blocks.append({"type": "header", "text": {"type": "plain_text", "text": "⏰ Automations"}})
        rows = TriggerStore(base_dir=config_dir()).load()
        if rows:
            lines = []
            for row in rows[:15]:
                trigger = row.trigger
                if not row.ok:
                    reason = row.errors[0].message if row.errors else "invalid"
                    lines.append(f"⚠️ *{trigger.name}* — {reason}")
                    continue
                status = "✅" if trigger.enabled else "⏸️"
                raw = f"{status} *{trigger.name}* — `{describe_cadence(trigger)}`"
                lines.append(redact_credentials(redact_exfiltration_urls(raw)[0])[0])
            if len(rows) > 15:
                lines.append(f"_…and {len(rows) - 15} more_")
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}
            )
        else:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "_No automations._"}}
            )
        blocks.append({"type": "divider"})

        # ── Recent Lessons ──
        blocks.append(
            {"type": "header", "text": {"type": "plain_text", "text": "📚 Recent Lessons"}}
        )
        lesson_lines: list[str] = []
        total_lessons = 0
        vs_ok = False
        # Primary: read lessons through the memory service (the record store where
        # memory_remember writes).
        vs = getattr(orch, "vector_memory", None)
        if vs is not None:
            from gideon.sdk.channel import MemoryService

            try:
                all_vs = MemoryService.over_vector_store(vs).get_lessons()
            except Exception:
                all_vs = None
                logger.debug("record-store lesson read failed, trying JSONL", exc_info=True)
            if isinstance(all_vs, list):
                total_lessons = len(all_vs)
                # get_lessons() returns ORDER BY updated_at DESC (most recent first).
                for entry in all_vs[:5]:
                    try:
                        parsed = json.loads(entry["value_json"])
                        rule = parsed.get("rule", str(parsed)) if isinstance(parsed, dict) else str(parsed)
                        lesson_lines.append(
                            f"• {redact_credentials(redact_exfiltration_urls(rule)[0])[0][:100]}"
                        )
                    except Exception:
                        logger.debug("Skipping malformed lesson entry", exc_info=True)
                vs_ok = True
        # Fallback: JSONL lesson store, when no vector store is configured.
        if not vs_ok and orch.ctx_builder is not None:
            all_lessons = orch.ctx_builder.lessons.load_all()
            total_lessons = len(all_lessons)
            for le in all_lessons[-5:]:
                lesson_lines.append(
                    f"• {redact_credentials(redact_exfiltration_urls(le.rule)[0])[0][:100]}"
                )
        if lesson_lines:
            if total_lessons > 5:
                lesson_lines.append(f"_…and {total_lessons - 5} more_")
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lesson_lines)}}
            )
        elif not vs_ok and orch.ctx_builder is None:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "_Lessons unavailable._"}}
            )
        else:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "_No lessons yet._"}}
            )
        blocks.append({"type": "divider"})

        # ── Commands ──
        from slack_desk_runtime.blocks import command_hint_block

        blocks.append({"type": "header", "text": {"type": "plain_text", "text": "⌨️ Commands"}})
        _sc = f"/{orch.slack_desk_command}"
        for name, (_, desc) in sorted(SLASH_REGISTRY.items()):
            blocks.append(command_hint_block(f"{_sc} {name}", desc))
        blocks.append(command_hint_block(f"{_sc} #channel", "track/untrack channel"))

        # ── Version ──
        from gideon.sdk.channel import __version__
        from gideon.sdk.channel import get_update_info

        version_text = f"📦 Gideon v{__version__}"
        update_info = get_update_info()
        remote_ver = update_info.get("remote_version")
        if update_info.get("available") and remote_ver is not None:
            version_text += f"  •  🆕 v{remote_ver} available — open Dashboard to update"
        version_text = redact_credentials(redact_exfiltration_urls(version_text)[0])[0]
        blocks.append({"type": "divider"})
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": version_text}]}
        )

        view = {"type": "home", "blocks": blocks}

        if orch.slack_desk is not None:
            await orch.slack_desk.views_publish(user_id=user_id, view=view)
        else:
            logger.warning("Cannot publish home tab — Slack client is None")

    except Exception:
        logger.error("Failed to publish home tab for %s", user_id, exc_info=True)
        # Attempt fallback error view
        try:
            if orch.slack_desk is not None:
                fallback = {
                    "type": "home",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "⚠️ Failed to load Home Tab. Try again later.",
                            },
                        }
                    ],
                }
                await orch.slack_desk.views_publish(user_id=user_id, view=fallback)
        except Exception:
            logger.debug("Fallback home tab also failed", exc_info=True)


# Slash command handler
# ---------------------------------------------------------------------------


async def _handle_slash(orch: "GatewayServices", payload: dict) -> None:
    """Route ``/gideon <sub-command>`` via :data:`SLASH_REGISTRY`.

    Falls back to @user / #channel mention handling, then help text.
    """
    cmd = payload.get("command", "")
    cmd_text = payload.get("text", "").strip()
    caller_id = payload.get("user_id", "")
    response_url = payload.get("response_url", "")
    logger.info("Slash command: %s %s (caller=%s)", cmd, cmd_text, caller_id)

    async def _respond(text: str, blocks: list[dict] | None = None) -> None:
        if not response_url:
            return
        try:
            body: dict = {"text": text, "response_type": "ephemeral"}
            if blocks:
                body["blocks"] = blocks
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json=body)
        except Exception:
            logger.debug("slash response_url failed", exc_info=True)

    slash_command = f"/{orch.slack_desk_command}"
    if cmd != slash_command:
        return

    # Deny-by-default — only allowed users can invoke slash commands
    if not is_allowed_user(caller_id):
        sel().log_api_access(
            caller=caller_id,
            operation="slack.slash_command",
            outcome="denied",
            source="slack",
            resources=cmd_text,
            error="unauthorized sender",
        )
        asyncio.create_task(_respond("⛔ You are not authorized to use this command."))
        return

    sel().log_api_access(
        caller=caller_id,
        operation="slack.slash_command",
        outcome="allowed",
        source="slack",
        resources=cmd_text,
    )

    if not (orch.slack_desk and orch._owner_id):
        asyncio.create_task(_respond("⚠️ Owner not configured."))
        return

    # Parse sub-command and args
    parts = cmd_text.split(maxsplit=1)
    sub_cmd = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    # Registry lookup
    entry = SLASH_REGISTRY.get(sub_cmd)
    if entry is not None:
        handler, _ = entry
        # Stash trigger_id so modal-opening handlers can use it
        orch._last_trigger_id = payload.get("trigger_id", "")  # type: ignore[attr-defined]
        asyncio.create_task(handler(orch, caller_id, args, _respond))
        return

    # Fallback: @user mention — multi-user access disabled for security
    user_match = re.search(r"<@([A-Z0-9]+)(?:\|([^>]+))?>", cmd_text)
    if user_match:
        asyncio.create_task(_respond("⛔ Multi-user access is disabled. Only the owner can use Gideon via Slack."))
        return

    # Fallback: #channel mention — Slack sends <#C1234|name> or <#C1234>
    channel_match = re.search(r"<#([A-Z0-9]+)(?:\|([^>]*))?>", cmd_text)
    if channel_match:
        channel_id = channel_match.group(1)
        channel_name = channel_match.group(2) or "Secret"
        asyncio.create_task(
            prompt_track_channel(orch.slack_desk, orch._owner_id, channel_id, channel_name)
        )
        asyncio.create_task(_respond(f"📨 Track request sent for #{channel_name or channel_id}."))
        return

    # Unknown sub-command → help
    asyncio.create_task(_respond(_build_help_text(orch.slack_desk_command)))


# ---------------------------------------------------------------------------
# Tracking-channel join
# ---------------------------------------------------------------------------


def _maybe_prompt_owner(orch: "GatewayServices", event: dict) -> None:
    """Multi-user access disabled — channel-join allowlist prompts are blocked."""
    return


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Audio transcription helper
# ---------------------------------------------------------------------------

_AUDIO_MIMETYPES = {"audio/", "video/webm"}


async def _transcribe_files(orch: "GatewayServices", files: list[dict]) -> list[str]:
    """Download and transcribe audio files, return list of transcription strings."""
    import tempfile

    from gideon.sdk.channel import sel
    from gideon.sdk.channel import transcribe_audio

    results: list[str] = []
    for f in files:
        mimetype = f.get("mimetype", "")
        if not any(mimetype.startswith(prefix) for prefix in _AUDIO_MIMETYPES):
            continue
        url = f.get("url_private_download") or f.get("url_private", "")
        if not url:
            continue
        dest: str | None = None
        try:
            raw_ft = re.sub(r"[^a-zA-Z0-9]", "", f.get("filetype", "webm"))
            suffix = "." + (raw_ft or "webm")
            fd, dest = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            assert orch.slack_desk is not None
            assert dest is not None
            await orch.slack_desk.download_file(url, dest)
            sel().log_api_access(
                caller="stt",
                operation="slack.download_file",
                outcome="success",
                source="transcribe",
                resources=f.get("name", "?"),
            )
            transcript = await transcribe_audio(dest)
            sel().log_api_access(
                caller="stt",
                operation="whisper.transcribe",
                outcome="success" if transcript else "empty",
                source="transcribe",
                resources=f.get("name", "?"),
            )
            if transcript:
                results.append(transcript)
                logger.info("Transcribed voice memo: %d chars", len(transcript))
            else:
                logger.warning("Transcription returned empty for %s", f.get("name", "?"))
        except Exception:
            logger.exception("Failed to transcribe file %s", f.get("name", "?"))
            sel().log_api_access(
                caller="stt",
                operation="whisper.transcribe",
                outcome="error",
                source="transcribe",
                resources=f.get("name", "?"),
                error="transcription_failed",
            )
        finally:
            if dest:
                try:
                    os.unlink(dest)
                except OSError:
                    pass
    return results


# ---------------------------------------------------------------------------
# Message routing
# ---------------------------------------------------------------------------


async def _handle_message_deleted(orch: "GatewayServices", event: dict) -> None:
    """Handle message_deleted subtype — cancel queued or in-flight messages."""
    deleted_ts = event.get("deleted_ts")
    _del_thread_ts = event.get("previous_message", {}).get("thread_ts")
    _del_channel = event.get("channel", "")
    _del_user = event.get("previous_message", {}).get("user", "")
    if deleted_ts and _del_channel and is_allowed_user(_del_user):
        _del_session_key = _del_thread_ts or deleted_ts
        was_queued = False
        if orch.sessions:
            was_queued = orch.sessions.cancel_queued(_del_session_key, deleted_ts)
        if not was_queued:
            _pq = orch._pending_queue.get(_del_session_key, [])
            _filtered = [item for item in _pq if item[0] != deleted_ts]
            if len(_filtered) < len(_pq):
                was_queued = True
                if _filtered:
                    orch._pending_queue[_del_session_key] = _filtered
                else:
                    orch._pending_queue.pop(_del_session_key, None)
        if was_queued:
            logger.info(
                "message_deleted: ts=%s session=%s queued=%s",
                deleted_ts, _del_session_key, was_queued,
            )
        sel().log_api_access(
            caller=event.get("previous_message", {}).get("user", "unknown"),
            operation="slack.message_deleted",
            outcome="allowed",
            source="slack",
            resources=f"ts={deleted_ts} session={_del_session_key} queued={was_queued}",
        )


async def _dispatch_queued(
    orch: "GatewayServices",
    session_key: str,
    msg_ts: str,
    text: str,
    kwargs: dict,
) -> None:
    """Dispatch a queued message — remove ⏳ reaction and call handle_message."""
    channel = kwargs.get("channel", "")
    thread_ts = kwargs.get("thread_ts")
    if orch.slack_desk:
        try:
            await orch.slack_desk.remove_reaction(channel, msg_ts, "hourglass_flowing_sand")
        except Exception:
            pass
    await handle_message(
        orch.slack_desk,  # type: ignore[arg-type]
        orch.sessions,  # type: ignore[arg-type]
        channel,
        text,
        thread_ts,
        msg_ts,
        kwargs.get("sender_id", ""),
        team_id=kwargs.get("team_id", ""),
        approval_mode=APPROVAL_INTERACTIVE,
        context_builder=orch.ctx_builder,
        conversation_log=orch.conv_log,
        consolidator=orch.consolidator,
        subagent_manager=orch.subagent_mgr,
        channel_agent=kwargs.get("agent_override"),
        user_display_name=kwargs.get("user_display_name"),
    )


async def _route_message(
    orch: "GatewayServices",
    event: dict,
    seen: SeenCache,
    is_mention: bool = False,
    from_trusted_bot: bool = False,
) -> None:
    """Validate, dedup, check activation mode, and dispatch an incoming Slack message."""
    sender_id = event.get("user", "") or (event.get("bot_id", "") if from_trusted_bot else "")
    channel = event.get("channel", "")
    text = event.get("text", "")
    thread_ts = event.get("thread_ts")
    msg_ts = event.get("ts", "")
    team_id = event.get("team", "")
    files = event.get("files", [])

    logger.debug("Stream debug: team_id=%s user_id=%s channel=%s", team_id, sender_id, channel)

    if not sender_id or not channel or (not text and not files):
        return

    # ── Enterprise origin check: reject messages from swapped tokens ──
    if not check_message_origin(team_id):
        logger.error("Message rejected: team_id=%s does not match validated workspace", team_id)
        sel().log_api_access(
            caller=sender_id,
            operation="slack.message",
            outcome="denied",
            source="slack",
            resources=f"team_id={team_id} channel={channel}",
            error="enterprise_origin_mismatch",
        )
        return

    # ── Owner auto-claim (trust-on-first-use bootstrap) ──
    # On a fresh install with no preset owner, the FIRST human to reach the bot
    # becomes its sole owner (persisted for restart). Accepted from a direct
    # message OR an @mention in a tracked channel (both are deliberate first
    # contact); a plain untracked-channel post can't claim. Never from a bot, and
    # a no-op once an owner exists — ownership never transfers.
    if (
        not from_trusted_bot
        and not get_owner_id()
        and sender_id
        and not event.get("bot_id")
        and (event.get("channel_type") == "im" or is_mention or is_tracked_channel(channel))
    ):
        if claim_owner(sender_id):
            set_allowed_users({sender_id})

    # ── Access control: record authorization decision early for SEL audit ──
    # The ephemeral rejection is deferred until after activation checks so
    # users in observe/mention channels aren't spammed, but the SEL event
    # is always emitted to preserve the audit trail.
    _user_authorized = from_trusted_bot or is_allowed_user(sender_id) or is_open_channel(channel)
    if from_trusted_bot:
        sel().log_api_access(
            caller=sender_id,
            operation="slack.message",
            outcome="allowed",
            source="slack",
        )
    elif is_open_channel(channel) and not is_allowed_user(sender_id):
        sel().log_api_access(
            caller=sender_id,
            operation="slack.message",
            outcome="allowed",
            source="slack",
            resources=f"open_channel={channel}",
        )
    if not _user_authorized:
        logger.warning("Ignoring message from unauthorized user %s", sender_id)
        sel().log_api_access(
            caller=sender_id,
            operation="slack.message",
            outcome="denied",
            source="slack",
            error="unauthorized sender",
        )

    # ── Channel activation mode (checked BEFORE ephemeral & dedup) ──
    # When activation=mention, Slack sends both a `message` and an
    # `app_mention` event for the same msg_ts.  We must skip the plain
    # `message` event *without* marking it as seen so the subsequent
    # `app_mention` event is still processed.
    from slack_desk_runtime.settings import get_settings

    ch_cfg = get_settings().channel_config(channel)
    activation = ch_cfg.activation

    if activation == ACTIVATION_OFF:
        # Allow !channel commands through so the owner can re-enable the channel.
        # Text may start with "<@BOTID> " when @mentioned, so strip that first.
        _stripped = text.lstrip()
        if _stripped.startswith("<@"):
            end = _stripped.find(">")
            if end != -1:
                _stripped = _stripped[end + 1 :].lstrip()
        if not _stripped.startswith("!channel"):
            logger.debug("Channel %s activation=off — ignoring message", channel)
            sel().log_api_access(
                caller=sender_id,
                operation="slack.message",
                outcome="denied",
                source="slack",
                resources=channel,
                error="activation=off",
            )
            return

    # Resolve sender's Slack display name so the LLM uses the actual
    # profile name instead of guessing from memory. Cached on channel
    # history (for history context) and passed to handle_message.
    _sender_display: str | None = None
    if orch.channel_history:
        _sender_display = orch.channel_history._user_names.get(sender_id)
    if not _sender_display and orch.slack_desk and hasattr(orch.slack_desk, "get_user_info"):
        try:
            info = await orch.slack_desk.get_user_info(sender_id)
            _sender_display = info.get("real_name") or sender_id
            if orch.channel_history:
                orch.channel_history.set_user_name(sender_id, _sender_display)
        except Exception:
            logger.debug("Failed to resolve display name for %s", sender_id, exc_info=True)

    # Fallback: if display name is still the raw Slack ID, resolve from
    # allowed_users config (works even without Slack users:read scope).
    if (not _sender_display or _sender_display == sender_id) and hasattr(orch, "settings"):
        for u in getattr(orch.settings, "allowed_users", []):
            if u.get("slack_id") == sender_id and u.get("name"):
                _sender_display = u["name"]
                if orch.channel_history:
                    orch.channel_history.set_user_name(sender_id, u["name"])
                break

    # Observe mode: record history from authorized users only, so non-owner
    # messages can't influence LLM context.
    if activation == ACTIVATION_OBSERVE:
        from gideon.sdk.channel import should_record_observe_history

        if should_record_observe_history(orch.channel_history, _user_authorized):
            assert orch.channel_history is not None  # narrowed by helper
            orch.channel_history.push(channel, sender_id, text, thread_ts=thread_ts)
        if not is_mention:
            in_active_thread = (
                thread_ts
                and orch.sessions
                and (
                    orch.sessions.has_session(thread_ts)
                    or orch.sessions.get_session_for_thread(thread_ts)
                    or (orch.conv_log and orch.conv_log.has_log(thread_ts))
                )
            )
            if not in_active_thread:
                sel().log_api_access(
                    caller=sender_id,
                    operation="slack.message",
                    outcome="denied",
                    source="slack",
                    resources=channel,
                    error="activation=observe, no mention or active thread",
                )
                return

    if activation in (ACTIVATION_MENTION, ACTIVATION_REVIEW) and not is_mention:
        # In mention/review mode: ignore messages without @mention UNLESS the
        # message is a reply in a thread where the bot already has an active
        # session (i.e., the bot was previously @mentioned in that thread).
        in_active_thread = (
            thread_ts
            and orch.sessions
            and (
                orch.sessions.has_session(thread_ts)
                or orch.sessions.get_session_for_thread(thread_ts)
                or (orch.conv_log and orch.conv_log.has_log(thread_ts))
            )
        )
        if not in_active_thread:
            sel().log_api_access(
                caller=sender_id,
                operation="slack.message",
                outcome="denied",
                source="slack",
                resources=channel,
                error=f"activation={activation}, no mention or active thread",
            )
            return

    # ── Access control: send ephemeral rejection ──
    # Only reached for messages the bot would actually respond to,
    # preventing notification spam in observe/mention channels.
    if not _user_authorized:
        if orch.slack_desk:
            try:
                await orch.slack_desk.post_ephemeral(
                    channel,
                    sender_id,
                    "⛔ You are not authorized to use this bot. "
                    "Ask the owner to add you to the allowlist.",
                )
            except Exception:
                # WARNING, not DEBUG (#953): a refusal the sender never sees is
                # indistinguishable from a crash, from both sides of the conversation. The
                # refusal itself is already recorded above (SEL + WARNING); this line is
                # specifically "we refused them AND could not tell them".
                logger.warning(
                    "Refused %s in %s, but the ephemeral notice failed to send — the "
                    "sender sees silence", sender_id, channel, exc_info=True,
                )
        return

    # Dedup AFTER activation check — prevents the plain `message` event
    # from poisoning the cache before the `app_mention` event arrives.
    if seen.check_and_add(msg_ts):
        return

    # ── Transcribe audio files (voice memos) ──
    # Placed after dedup + auth to avoid expensive work on duplicate events
    # or unauthorized users.
    _image_temp_paths: list[str] = []
    _had_voice_input = False
    if files and orch.slack_desk and _user_authorized:
        if await stt_available():
            transcripts = await _transcribe_files(orch, files)
            if transcripts:
                raw = "\n".join(transcripts)
                raw, _ = redact_exfiltration_urls(raw)
                raw, _ = redact_credentials(raw)
                prefix = f"[Voice memo transcription]\n{raw}\n[End of transcription]"
                text = f"{prefix}\n\n{text}" if text else prefix
                _had_voice_input = True

        # ── Process non-audio files (images, text, etc.) ──
        image_paths, text_blocks = await process_slack_desk_files(orch, files)
        _image_temp_paths = image_paths

        # Inject image paths so AcpClient._send_prompt() inlines them as base64
        if image_paths:
            paths_text = "\n".join(image_paths)
            text = f"{text}\n{paths_text}" if text else paths_text

        # Inject text file contents
        if text_blocks:
            blocks_text = "\n\n".join(text_blocks)
            text = f"{text}\n\n{blocks_text}" if text else blocks_text

    # Bail out if we still have no text after attempting transcription
    if not text:
        # Clean up any downloaded image temp files
        for p in _image_temp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass
        return

    def _cleanup_image_temps() -> None:
        for p in _image_temp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    # Record messages in channel history buffer (observe channels already
    # pushed above, so skip them here to avoid duplicates).
    if activation != ACTIVATION_OBSERVE:
        if orch.channel_history is None:
            logger.error("channel_history not initialised — skipping history push")
        else:
            orch.channel_history.push(channel, sender_id, text, thread_ts=thread_ts)

    # Strip the leading bot @mention so the LLM sees clean text.
    # app_mention events always start with "<@BOTID> ..." — just slice past the first ">".
    clean_text = text
    if is_mention and text.startswith("<@"):
        end = text.find(">")
        if end != -1:
            clean_text = text[end + 1 :].lstrip()
    if not clean_text:
        _cleanup_image_temps()
        return

    # ── !stop: intercept BEFORE handle_message to bypass session semaphore ──
    if clean_text.strip().lower() == "!stop":
        if not (is_owner(sender_id) or is_allowed_user(sender_id)):
            sel().log_api_access(
                caller=sender_id,
                operation="slack.stop_command",
                outcome="denied",
                source="slack",
                resources="!stop",
                error="unauthorized sender",
            )
            if orch.slack_desk:
                await orch.slack_desk.post_message(channel, "⛔ Not authorized.", thread_ts or msg_ts)
            return
        if not orch.sessions:
            sel().log_tool_invocation(
                session_key=thread_ts or msg_ts,
                source="slack",
                tool_name="!stop",
                tool_kind="command",
                outcome="no_session",
                metadata={"user": sender_id, "channel": channel},
            )
            if orch.slack_desk:
                await orch.slack_desk.post_message(channel, "Nothing running.", thread_ts or msg_ts)
            return
        session_key = thread_ts or msg_ts
        has_session = orch.sessions.has_session(session_key)
        active_task = orch._session_tasks.pop(session_key, None)
        if has_session or active_task:
            orch.sessions.clear_queue(session_key)
            orch._pending_queue.pop(session_key, None)

            # Post ephemeral "Stopping…" block with Kill Now button
            if orch.slack_desk:
                from slack_desk_runtime.blocks import build_stopping_blocks

                await orch.slack_desk.post_ephemeral(
                    channel,
                    sender_id,
                    "Stopping…",
                    blocks=build_stopping_blocks(session_key),
                    thread_ts=session_key,
                )

            async def _on_soft() -> None:
                if orch.slack_desk:
                    await orch.slack_desk.post_message(
                        channel, "⏹ Execution stopped.", session_key
                    )

            async def _on_hard() -> None:
                if orch.slack_desk:
                    await orch.slack_desk.post_message(
                        channel, "⛔ Execution stopped — session reset.", session_key
                    )

            outcome = await orch.sessions.stop_turn(
                session_key, on_soft=_on_soft, on_hard=_on_hard
            )
            if active_task and not active_task.done():
                active_task.cancel()
            # If stop_turn returned "idle" (no active turn), neither callback
            # fired — dismiss the stale "Stopping…" ephemeral explicitly.
            if outcome == "idle" and orch.slack_desk:
                await orch.slack_desk.post_message(
                    channel, "Nothing running.", session_key
                )
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!stop",
                tool_kind="command",
                outcome=outcome,
                metadata={"user": sender_id, "channel": channel},
            )
        else:
            sel().log_tool_invocation(
                session_key=session_key,
                source="slack",
                tool_name="!stop",
                tool_kind="command",
                outcome="no_session",
                metadata={"user": sender_id, "channel": channel},
            )
            if orch.slack_desk:
                await orch.slack_desk.post_message(channel, "Nothing running.", thread_ts or msg_ts)
        return

    # Per-channel agent override
    agent_override = ch_cfg.agent or None

    logger.info(
        "Message from %s in %s (activation=%s): %s", sender_id, channel, activation, text[:80]
    )

    # CE-10: hand the message to this bundle's own trigger source, if one is attached.
    # HERE and not earlier: everything above this line is the gate — the allowlist /
    # open-channel / tracked-channel decision, the channel activation mode, and the dedup
    # cache. A message that reaches this point is one this app has admitted and is about to
    # answer, so it is exactly the traffic a user's automation should see; anything refused
    # above arms nothing. Published BEFORE the busy-session queue check on purpose: a queued
    # message is still an admitted message, and whether a session happened to be busy is not
    # something the user's trigger should depend on.
    publish_inbound_for_trigger_source(
        channel=channel,
        text=clean_text,
        sender_id=sender_id,
        thread_ts=thread_ts,
        msg_ts=msg_ts,
    )

    # ── Queue check: if session is busy, enqueue instead of blocking ──
    session_key = thread_ts or msg_ts
    _task_busy = session_key in orch._session_tasks
    if _task_busy:
        # A task is already running for this session key.  Try the session-level
        # queue first (semaphore-based); fall back to an orchestrator-level
        # pre-session queue when the session object doesn't exist yet.
        _queued = orch.sessions and orch.sessions.enqueue(
            session_key, msg_ts, clean_text, force=True,
            channel=channel, thread_ts=thread_ts, sender_id=sender_id,
            team_id=team_id, agent_override=agent_override,
            user_display_name=_sender_display,
        )
        if not _queued:
            # Session object not created yet — stash on orch._pending_queue
            orch._pending_queue.setdefault(session_key, []).append(
                (msg_ts, clean_text, dict(
                    channel=channel, thread_ts=thread_ts, sender_id=sender_id,
                    team_id=team_id, agent_override=agent_override,
                    user_display_name=_sender_display,
                ))
            )
        logger.info("Message %s queued for busy session %s (session_obj=%s)", msg_ts, session_key, _queued)
        if orch.slack_desk:
            try:
                await orch.slack_desk.add_reaction(channel, msg_ts, "hourglass_flowing_sand")
            except Exception:
                logger.debug("Failed to add queue reaction", exc_info=True)
        _cleanup_image_temps()
        return
    elif orch.sessions and orch.sessions.enqueue(
        session_key,
        msg_ts,
        clean_text,
        channel=channel,
        thread_ts=thread_ts,
        sender_id=sender_id,
        team_id=team_id,
        agent_override=agent_override,
        user_display_name=_sender_display,
    ):
        logger.info("Message %s queued for busy session %s", msg_ts, session_key)
        if orch.slack_desk:
            try:
                await orch.slack_desk.add_reaction(channel, msg_ts, "hourglass_flowing_sand")
            except Exception:
                logger.debug("Failed to add queue reaction", exc_info=True)
        _cleanup_image_temps()
        return

    try:
        t = asyncio.create_task(
            handle_message(
                orch.slack_desk,  # type: ignore[arg-type]
                orch.sessions,  # type: ignore[arg-type]
                channel,
                clean_text,
                thread_ts,
                msg_ts,
                sender_id,
                team_id=team_id,
                approval_mode=APPROVAL_INTERACTIVE,
                context_builder=orch.ctx_builder,
                conversation_log=orch.conv_log,
                consolidator=orch.consolidator,
                subagent_manager=orch.subagent_mgr,
                channel_agent=agent_override,
                user_display_name=_sender_display,
                from_trusted_bot=from_trusted_bot,
                channel_activation=activation,
                had_voice_input=_had_voice_input,
            )
        )
    except Exception:
        logger.exception("Failed to create handle_message task")
        _cleanup_image_temps()
        return

    orch._session_tasks[session_key] = t

    def _on_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
        orch._handler_tasks.discard(task)
        if orch._session_tasks.get(session_key) is task:
            del orch._session_tasks[session_key]
        _cleanup_image_temps()
        # Drain queue: only if no other task took over this session
        try:
            if session_key not in orch._session_tasks and orch.sessions:
                _next = orch.sessions.dequeue(session_key)
                # Fall back to orchestrator-level pending queue (pre-session messages)
                if not _next:
                    _pq = orch._pending_queue.get(session_key)
                    if _pq:
                        _next = _pq.pop(0)
                        if not _pq:
                            del orch._pending_queue[session_key]
                if _next:
                    _q_ts, _q_text, _q_kw = _next
                    _q_t = asyncio.ensure_future(
                        _dispatch_queued(orch, session_key, _q_ts, _q_text, _q_kw)
                    )
                    orch._session_tasks[session_key] = _q_t
                    orch._handler_tasks.add(_q_t)
                    _q_t.add_done_callback(_on_done)
        except Exception:
            logger.exception("_on_done drain failed for %s", session_key)

    orch._handler_tasks.add(t)
    t.add_done_callback(_on_done)
