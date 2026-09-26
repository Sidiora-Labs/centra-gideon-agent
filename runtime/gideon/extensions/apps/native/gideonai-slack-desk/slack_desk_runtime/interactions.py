"""Slack Block Kit interactive payload routing.

Handles button clicks dispatched by Socket Mode:
- Tool approval (approve / trust / reject)
- OPTIONS choice buttons (LLM-generated multiple-choice)
- Cron and subagent acknowledge buttons
- Allowlist approve / deny buttons

All handlers receive the raw ``SocketModeRequest`` payload and
delegate to the appropriate service via the module-level ``_orch``
reference (set by the gateway orchestrator at startup).
"""

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from slack_desk_runtime.settings import ACTIVATION_REVIEW
from gideon.sdk.channel import redact_credentials, redact_exfiltration_urls
from gideon.sdk.channel import sel
from slack_desk_runtime.allowlist import (
    ACTION_ALLOWLIST_APPROVE,
    ACTION_ALLOWLIST_DENY,
    ACTION_TRACK_APPROVE,
    ACTION_TRACK_DENY,
    persist_allowed_user,
    persist_tracking_channel,
)
from slack_desk_runtime.format import (
    LINK_DASHBOARD_ACTION,
    OPTIONS_ACTION_PREFIX,
    OPTIONS_CHECKBOXES_ACTION,
    OPTIONS_SUBMIT_ACTION,
    build_options_selected_blocks,
)
from slack_desk_runtime.handler import (
    APPROVAL_INTERACTIVE,
    handle_interaction,
    handle_message,
    is_allowed_user,
    is_owner,
    set_allowed_users,
    set_tracking_channels,
)

if TYPE_CHECKING:
    from gideon.sdk.channel import GatewayServices

logger = logging.getLogger(__name__)

# Module-level orchestrator reference — set by ``init()``.
_orch: "GatewayServices | None" = None


def init(orchestrator: "GatewayServices") -> None:
    """Bind the orchestrator so interactive handlers can reach services."""
    global _orch
    _orch = orchestrator


# ---------------------------------------------------------------------------
# View submission registry
# ---------------------------------------------------------------------------

# Handler signature: async def handler(payload: dict) -> None
ViewHandler = Callable[[dict], Awaitable[None]]

VIEW_REGISTRY: dict[str, ViewHandler] = {}


def register_view_handler(callback_id: str, handler: ViewHandler) -> None:  # type: ignore[type-arg]
    """Register a handler for a ``view_submission`` or ``view_closed`` callback_id."""
    VIEW_REGISTRY[callback_id] = handler


async def handle_view_submission(payload: dict) -> None:
    """Dispatch a view_submission event to the registered handler."""
    view = payload.get("view", {})
    callback_id = view.get("callback_id", "")
    handler = VIEW_REGISTRY.get(callback_id)
    if handler is None:
        logger.warning("No view handler registered for callback_id=%s", callback_id)
        return
    try:
        await handler(payload)  # type: ignore[misc]
    except Exception:
        logger.exception("View handler failed for callback_id=%s", callback_id)


async def handle_view_closed(payload: dict) -> None:
    """Dispatch a view_closed event. Uses same registry with ``_closed`` suffix fallback."""
    view = payload.get("view", {})
    callback_id = view.get("callback_id", "")
    # Try <callback_id>_closed first, then fall back to <callback_id>
    handler = VIEW_REGISTRY.get(callback_id + "_closed")
    if handler is None:
        logger.debug("No view_closed handler for callback_id=%s (ignored)", callback_id)
        return
    try:
        await handler(payload)  # type: ignore[misc]
    except Exception:
        logger.exception("View closed handler failed for callback_id=%s", callback_id)


# ---------------------------------------------------------------------------
# Config modal submission handler
# ---------------------------------------------------------------------------


async def _handle_config_submission(payload: dict) -> None:
    """Persist config modal changes to config.json and update runtime state."""
    caller = payload.get("user", {}).get("id", "")
    if not is_owner(caller):
        logger.warning("config_submission rejected: non-owner %s", caller)
        return
    from gideon.sdk.channel import ProviderSettings

    view = payload.get("view", {})
    values = view.get("state", {}).get("values", {})

    # Parse allowlist — multi-user access disabled; ignore any stale allowlist_block
    # Parse tracked channels (multi_channels_select)
    chan_vals = values.get("channels_block", {}).get("pc_config_channels", {})
    new_channels = set(chan_vals.get("selected_channels") or [])

    # Update runtime state
    if _orch:
        _orch._tracking_channels = new_channels
        set_tracking_channels(new_channels)

    # Persist to the app's OWN store (SlackDeskSettings home) — not core config.json.
    try:
        ProviderSettings.update(
            "gideonai-slack-desk",
            {"tracking_channels": [{"channel_id": cid} for cid in sorted(new_channels)]},
        )
        from slack_desk_runtime.settings import reload_settings

        reload_settings()
    except OSError:
        logger.exception("Failed to persist config from modal")

    logger.info("Config updated via modal: channels=%d", len(new_channels))
    sel().log_api_access(
        caller=payload.get("user", {}).get("id", "unknown"),
        operation="slack.config_update",
        outcome="allowed",
        source="slack",
        resources=f"channels={len(new_channels)}",
    )


register_view_handler("pc_config_panel", _handle_config_submission)


# ---------------------------------------------------------------------------
# Shared helper — replace a button message with "✅ Acknowledged"
# ---------------------------------------------------------------------------


async def ack_button(payload: dict, channel: str, msg_ts: str) -> None:
    """Replace an ack/approve button message with '✅ Acknowledged'.

    Tries ``response_url`` first (instant, no API call), then falls
    back to ``chat.update``.
    """
    response_url = payload.get("response_url", "")
    blocks = payload.get("message", {}).get("blocks", [])

    # Strip action blocks, keep content — append ack context
    acked_blocks = []
    for b in blocks:
        if b.get("type") == "actions":
            continue
        if b.get("type") == "section" and b.get("text", {}).get("text", ""):
            b = {**b, "text": {**b["text"], "text": b["text"]["text"][:2990]}}
        acked_blocks.append(b)
    acked_blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "✅ Acknowledged"}]}
    )

    updated = False
    if response_url:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as sess:
                resp = await sess.post(
                    response_url,
                    json={
                        "replace_original": True,
                        "text": "✅ Acknowledged",
                        "blocks": acked_blocks,
                    },
                )
                updated = resp.status == 200
        except Exception:
            logger.debug("response_url update failed", exc_info=True)

    if not updated and _orch and _orch.slack_desk and channel and msg_ts:
        try:
            await _orch.slack_desk.update_message(
                channel, msg_ts, text="✅ Acknowledged", blocks=acked_blocks
            )
        except Exception:
            logger.debug("chat.update fallback failed", exc_info=True)


# ---------------------------------------------------------------------------
# Main dispatcher — called from the event router
# ---------------------------------------------------------------------------


async def dispatch(payload: dict) -> None:
    """Route a Block Kit interactive payload to the correct handler."""
    # ── View submissions and closures (modals) ──
    payload_type = payload.get("type", "")
    if payload_type == "view_submission":
        await handle_view_submission(payload)
        return
    if payload_type == "view_closed":
        await handle_view_closed(payload)
        return

    actions = payload.get("actions", [])
    if not actions:
        return

    action = actions[0]
    action_id = action.get("action_id", "")
    channel = payload.get("channel", {}).get("id", "")
    msg_ts = payload.get("message", {}).get("ts", "")
    user_id = payload.get("user", {}).get("id", "")

    # ── Access check — deny-by-default ──
    if not is_allowed_user(user_id):
        logger.warning(
            "Rejecting interactive payload from unauthorized user %s (action=%s)",
            user_id or "unknown",
            action_id,
        )
        sel().log_api_access(
            caller=user_id or "unknown",
            operation="slack.interactive",
            outcome="denied",
            source="slack",
            resources=action_id,
            error="unauthorized user",
        )
        if _orch and _orch.slack_desk and channel and user_id:
            try:
                await _orch.slack_desk.post_ephemeral(
                    channel, user_id, "⛔ You are not authorized to use these buttons."
                )
            except Exception:
                logger.debug("Failed to send ephemeral rejection", exc_info=True)
        return

    # ── OPTIONS checkboxes toggle — no-op, wait for Send ──
    if action_id == OPTIONS_CHECKBOXES_ACTION:
        return

    # ── OPTIONS Send button ──
    if action_id == OPTIONS_SUBMIT_ACTION:
        await _handle_options_submit(payload, channel, msg_ts)
        return

    # ── OPTIONS choice buttons + action:: element routing ──
    if action_id.startswith(OPTIONS_ACTION_PREFIX):
        if "_done_" in action_id:
            return
        await _handle_options(payload, action, channel, msg_ts)
        return

    # ── Cron acknowledge ──
    from slack_desk_runtime.format import CRON_ACK_ACTION_PREFIX

    if action_id.startswith(CRON_ACK_ACTION_PREFIX):
        await _handle_cron_ack(payload, action, channel, msg_ts)
        return

    # ── Subagent acknowledge ──
    from slack_desk_runtime.format import SUBAGENT_ACK_ACTION_PREFIX

    if action_id.startswith(SUBAGENT_ACK_ACTION_PREFIX):
        await _handle_subagent_ack(payload, action, channel, msg_ts)
        return

    # ── Allowlist approve / deny (owner-only) ──
    if action_id in (ACTION_ALLOWLIST_APPROVE, ACTION_ALLOWLIST_DENY):
        if not is_owner(user_id):
            logger.warning("Rejecting allowlist action from non-owner %s", user_id)
            sel().log_api_access(
                caller=user_id,
                operation="slack.allowlist.button",
                outcome="denied",
                source="slack",
                resources=action_id,
                error="non-owner",
            )
            return
        await _handle_allowlist(payload, action, action_id, channel, msg_ts, user_id)
        return

    # ── Track channel approve / deny (owner-only) ──
    if action_id in (ACTION_TRACK_APPROVE, ACTION_TRACK_DENY):
        if not is_owner(user_id):
            logger.warning("Rejecting track-channel action from non-owner %s", user_id)
            sel().log_api_access(
                caller=user_id,
                operation="slack.track_channel.button",
                outcome="denied",
                source="slack",
                resources=action_id,
                error="non-owner",
            )
            return
        await _handle_track_channel(payload, action, action_id, channel, msg_ts, user_id)
        return

    # ── Stop confirm / cancel ──
    if action_id == "pc_stop_confirm":
        await _handle_stop_confirm(payload, channel, msg_ts, user_id)
        return
    if action_id == "pc_stop_cancel":
        await _handle_stop_cancel(payload, channel, msg_ts)
        return

    # ── Kill Now (ephemeral stop escalation) ──
    if action_id == "stop_kill_now":
        await _handle_stop_kill_now(payload, action, channel, msg_ts, user_id)
        return

    # ── Dashboard copy link ──
    if action_id == "pc_dashboard_copy":
        url = action.get("value", "")
        response_url = payload.get("response_url", "")
        if response_url and url:
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    response_url,
                    json={
                        "replace_original": False,
                        "response_type": "ephemeral",
                        "text": f"📋 Copy this link:\n```{url}```",
                    },
                )
        return

    # ── Link to Dashboard button ──
    if action_id == LINK_DASHBOARD_ACTION:
        user_id = payload.get("user", {}).get("id", "")
        if not is_allowed_user(user_id):
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="pc_link_dashboard", tool_kind="interaction",
                outcome="denied",
                metadata={"user_id": user_id, "reason": "not_allowed_user"},
            )
            return
        thread_ts = payload.get("message", {}).get("thread_ts") or payload.get("container", {}).get("thread_ts", "")
        if not thread_ts:
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="pc_link_dashboard", tool_kind="interaction",
                outcome="failure",
                metadata={"user_id": user_id, "reason": "no_thread_ts"},
            )
            return
        ds = _orch.dashboard_state if _orch else None
        if not ds or not hasattr(ds, "get_or_create_session"):
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="pc_link_dashboard", tool_kind="interaction",
                outcome="failure",
                metadata={"user_id": user_id, "reason": "no_dashboard"},
            )
            return
        if not _orch or not _orch.slack_desk:
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="pc_link_dashboard", tool_kind="interaction",
                outcome="failure",
                metadata={"user_id": user_id, "reason": "no_slack_client"},
            )
            return
        session = await _import_thread_to_session(_orch.slack_desk, ds, channel, thread_ts)
        if session:
            from slack_desk_runtime.handler import _track_linked_channel

            _track_linked_channel(channel)
        if not session:
            sel().log_tool_invocation(
                session_key="", agent="gideon", source="slack",
                tool_name="pc_link_dashboard", tool_kind="interaction",
                outcome="failure",
                metadata={"channel": channel, "thread_ts": thread_ts, "reason": "empty_thread"},
            )
            response_url = payload.get("response_url", "")
            if response_url and response_url.startswith("https://hooks.slack.com/"):
                import aiohttp

                async with aiohttp.ClientSession() as sess:
                    await sess.post(
                        response_url,
                        json={
                            "replace_original": False,
                            "response_type": "ephemeral",
                            "text": "⚠️ Could not import thread history.",
                        },
                    )
            return
        sel().log_tool_invocation(
            session_key=session.key, agent="gideon", source="slack",
            tool_name="pc_link_dashboard", tool_kind="interaction",
            outcome="success",
            metadata={"session": session.key, "channel": channel, "thread_ts": thread_ts},
        )
        # Replace the button with confirmation
        response_url = payload.get("response_url", "")
        if response_url and response_url.startswith("https://hooks.slack.com/"):
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    response_url,
                    json={
                        "replace_original": False,
                        "response_type": "ephemeral",
                        "text": f"Linked to dashboard session *{session.key}* -- messages sync both ways.",
                    },
                )
        return

    # ── Agent select dropdown ──
    if action_id == "pc_agent_select":
        await _handle_agent_select(payload, action, channel, msg_ts, user_id)
        return

    # ── Session resume choice buttons ──
    if action_id.startswith("pc_resume_thread_"):
        await _handle_resume_choice(payload, action, channel, msg_ts, user_id, mode="thread")
        return
    if action_id.startswith("pc_resume_dm_"):
        await _handle_resume_choice(payload, action, channel, msg_ts, user_id, mode="dm")
        return

    # ── Session resume/end/new buttons ──
    if action_id.startswith("pc_session_resume_"):
        await _handle_session_resume(payload, action, channel, msg_ts, user_id)
        return
    if action_id.startswith("pc_session_end_"):
        await _handle_session_end(payload, action, channel, msg_ts, user_id)
        return
    if action_id == "pc_session_new":
        await _handle_session_new(payload, action, channel, msg_ts, user_id)
        return

    # ── Channel modal: activation change ──
    if action_id.startswith("pc_ch_activation_"):
        await _handle_ch_activation(payload, action)
        return

    # ── Channel modal: agent change ──
    if action_id.startswith("pc_ch_agent_"):
        await _handle_ch_agent(payload, action)
        return

    # ── Channel modal: remove channel ──
    if action_id.startswith("pc_ch_remove_"):
        await _handle_ch_remove(payload, action)
        return

    # ── Channel modal: add channel ──
    if action_id == "pc_ch_add":
        await _handle_ch_add(payload, action)
        return

    # ── Review mode: approve / edit / cancel ──
    if action_id == "pc_review_approve":
        await _handle_review_approve(payload, action)
        return
    if action_id == "pc_review_edit":
        await _handle_review_edit(payload, action)
        return
    if action_id == "pc_review_revise":
        await _handle_review_revise(payload, action)
        return
    if action_id == "pc_review_cancel":
        await _handle_review_cancel(payload, action)
        return

    # ── Allowlist / channel list remove buttons ──
    if action_id.startswith("pc_allowlist_remove_"):
        await _handle_allowlist_remove(payload, action, channel, msg_ts, user_id)
        return
    if action_id.startswith("pc_channel_remove_"):
        await _handle_channel_remove(payload, action, channel, msg_ts, user_id)
        return

    # ── Tool approval buttons (approve / trust / reject) ──
    if channel and msg_ts:
        await _handle_tool_approval(payload, action_id, channel, msg_ts, user_id)


# ---------------------------------------------------------------------------
# Channel modal helpers
# ---------------------------------------------------------------------------


async def _refresh_channels_modal(view_id: str) -> None:
    """Rebuild and push the channels modal with current state."""
    if not _orch or not _orch.slack_desk:
        return
    from slack_desk_runtime.blocks import channels_modal

    from slack_desk_runtime.settings import get_settings

    current_ids = sorted(_orch._tracking_channels)
    channels = [
        {
            "channel_id": cid,
            "activation": get_settings().channel_config(cid).activation,
            "agent": get_settings().channel_config(cid).agent,
        }
        for cid in current_ids
    ]
    from slack_desk_runtime.events import _get_agent_names

    modal = channels_modal(channels, agent_names=_get_agent_names())
    try:
        await _orch.slack_desk.views_update(view_id=view_id, view=modal)
    except Exception:
        logger.exception("Failed to refresh channels modal")


async def _handle_ch_activation(payload: dict, action: dict) -> None:
    """Change activation mode for a channel from the modal dropdown."""
    caller = payload.get("user", {}).get("id", "")
    if not is_owner(caller):
        return
    action_id = action.get("action_id", "")
    cid = action_id.removeprefix("pc_ch_activation_")
    new_mode = (action.get("selected_option") or {}).get("value", "mention")

    from slack_desk_runtime.handler import _persist_channel_config

    _persist_channel_config(cid, activation=new_mode)
    from slack_desk_runtime.settings import reload_settings

    reload_settings()
    sel().log_api_access(caller=caller, operation="slack.channel_activation_change", outcome="allowed", source="slack", resources=f"{cid}={new_mode}")
    logger.info("Channel %s activation changed to %s", cid, new_mode)


async def _handle_ch_agent(payload: dict, action: dict) -> None:
    """Change agent override for a channel from the modal dropdown."""
    caller = payload.get("user", {}).get("id", "")
    if not is_owner(caller):
        return
    action_id = action.get("action_id", "")
    cid = action_id.removeprefix("pc_ch_agent_")
    new_agent = (action.get("selected_option") or {}).get("value", "")
    if new_agent == "__default__":
        new_agent = ""

    from slack_desk_runtime.handler import _persist_channel_config

    _persist_channel_config(cid, agent=new_agent)
    from slack_desk_runtime.settings import reload_settings

    reload_settings()
    logger.info("Channel %s agent changed to %s", cid, new_agent or "default")
    sel().log_api_access(caller=caller, operation="slack.channel_agent_change", outcome="allowed", source="slack", resources=f"{cid}={new_agent or 'default'}")


async def _handle_ch_remove(payload: dict, action: dict) -> None:
    """Remove a channel from tracking via the modal button."""
    cid = action.get("value", "")
    if not cid or not _orch:
        return
    caller = payload.get("user", {}).get("id", "")
    if not is_owner(caller):
        return

    from slack_desk_runtime.allowlist import persist_tracking_channel

    _orch._tracking_channels.discard(cid)
    set_tracking_channels(_orch._tracking_channels)
    persist_tracking_channel(cid, remove=True)
    logger.info("Channel %s removed from tracking", cid)
    sel().log_api_access(caller=caller, operation="slack.channel_remove", outcome="allowed", source="slack", resources=cid)

    view_id = payload.get("view", {}).get("id", "")
    if view_id:
        await _refresh_channels_modal(view_id)


async def _handle_ch_add(payload: dict, action: dict) -> None:
    """Add a channel to tracking via the modal picker."""
    cid = action.get("selected_conversation") or action.get("selected_channel", "")
    if not cid or not _orch:
        return
    caller = payload.get("user", {}).get("id", "")
    if not is_owner(caller):
        return

    from slack_desk_runtime.allowlist import persist_tracking_channel

    _orch._tracking_channels.add(cid)
    set_tracking_channels(_orch._tracking_channels)
    persist_tracking_channel(cid)
    logger.info("Channel %s added to tracking", cid)
    sel().log_api_access(caller=caller, operation="slack.channel_add", outcome="allowed", source="slack", resources=cid)

    view_id = payload.get("view", {}).get("id", "")
    if view_id:
        await _refresh_channels_modal(view_id)


# ---------------------------------------------------------------------------
# Voice config view submission handler
# ---------------------------------------------------------------------------


async def _handle_voice_config_submission(payload: dict) -> None:
    """Save voice *behavior* from the pc_voice_config modal submission.

    Toggles + speaking speed persist to ``use_case_settings/tts.json`` (the
    unified store). The voice model itself is chosen in Settings → Models.
    """
    caller = payload.get("user", {}).get("id", "")
    if not is_owner(caller):
        return

    from gideon.sdk.channel import load_use_case_settings, save_use_case_settings
    from slack_desk_runtime.handler import _vc

    values = payload.get("view", {}).get("state", {}).get("values", {})

    def _txt(block_id: str, action_id: str) -> str:
        return (values.get(block_id, {}).get(action_id, {}).get("value") or "").strip()

    # Checkboxes
    tts_block = values.get("tts_enabled_block", {}).get("pc_voice_tts_enabled", {})
    selected = {o.get("value") for o in tts_block.get("selected_options", [])}
    _vc.global_enabled = "enabled" in selected
    auto_speak = "auto_speak" in selected
    _vc.auto_speak = auto_speak

    _length_scale_raw = _txt("piper_length_scale_block", "pc_voice_piper_length_scale")
    try:
        speed = float(_length_scale_raw) if _length_scale_raw else 1.0
    except ValueError:
        speed = 1.0

    settings = load_use_case_settings("tts")
    settings["enabled"] = _vc.global_enabled
    settings["auto_speak"] = auto_speak
    settings["speed"] = speed
    try:
        save_use_case_settings("tts", settings)
    except Exception:
        logger.exception("Failed to persist voice settings from modal")

    logger.info(
        "Voice settings updated: enabled=%s auto_speak=%s speed=%s",
        _vc.global_enabled, auto_speak, speed,
    )


register_view_handler("pc_voice_config", _handle_voice_config_submission)


# ---------------------------------------------------------------------------
# Individual action handlers
# ---------------------------------------------------------------------------


def _mark_button_clicked(blocks: list[dict], clicked_action_id: str, label: str) -> list[dict]:
    """Replace a clicked button with a ✓ context block in the Block Kit message.

    Walks *blocks* looking for an ``actions`` block containing *clicked_action_id*.
    Removes that button element and inserts a ``context`` block with
    ``✓ {label}`` immediately before the actions block.  If no elements
    remain, the empty actions block is dropped entirely.
    """
    result: list[dict] = []
    for block in blocks:
        if block.get("type") != "actions":
            result.append(block)
            continue
        elements = block.get("elements", [])
        remaining = [e for e in elements if e.get("action_id") != clicked_action_id]
        if len(remaining) == len(elements):
            # Clicked button not in this actions block — keep as-is
            result.append(block)
            continue
        # Insert ✓ context block before the (possibly empty) actions block
        result.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"✓ {label}"}]}
        )
        if remaining:
            result.append({**block, "elements": remaining})
    return result


_ACTION_PREFIX = "action::"


def _replace_options_blocks(
    blocks: list[dict], selected_blocks: list[dict]
) -> list[dict]:
    """Replace OPTIONS actions block(s) with *selected_blocks* in place.

    Walks *blocks* looking for any ``actions`` block whose elements include
    ``OPTIONS_CHECKBOXES_ACTION``, ``OPTIONS_SUBMIT_ACTION``, or an action_id
    starting with ``OPTIONS_ACTION_PREFIX``. The first such block is replaced
    by *selected_blocks* (inserted in order); subsequent OPTIONS actions blocks
    are dropped. All other blocks are preserved unchanged.
    """
    result: list[dict] = []
    inserted = False
    for block in blocks:
        if block.get("type") != "actions":
            result.append(block)
            continue
        elements = block.get("elements", [])
        is_options_block = any(
            el.get("action_id") in (OPTIONS_CHECKBOXES_ACTION, OPTIONS_SUBMIT_ACTION)
            or el.get("action_id", "").startswith(OPTIONS_ACTION_PREFIX)
            for el in elements
        )
        if not is_options_block:
            result.append(block)
            continue
        if not inserted:
            result.extend(selected_blocks)
            inserted = True
        # Drop the OPTIONS actions block itself
    if not inserted:
        # No OPTIONS actions block found — append selected_blocks at end so
        # the user still sees their selection (defensive fallback).
        logger.warning(
            "OPTIONS actions block not found in parent message blocks; "
            "appending selection at end"
        )
        result.extend(selected_blocks)
    return result


def _extract_selected_value(action: dict) -> tuple[str, str]:
    """Return ``(raw_value, display_text)`` from an extended element payload."""
    opt = action.get("selected_option")
    if opt:
        return opt.get("value", ""), opt.get("text", {}).get("text", "")
    for field in ("selected_date", "selected_time"):
        val = action.get(field)
        if val:
            return val, val
    dt = action.get("selected_date_time")
    if dt is not None:
        return str(dt), str(dt)
    return "", ""


_ACTION_PAYLOAD_CAP = 4000


async def _route_action_to_session(
    channel: str,
    msg_ts: str,
    thread_ts: str,
    user_id: str,
    team_id: str,
    label: str,
    payload_str: str,
    context_tag: str,
    action_id_value: str,
    blocks: list[dict],
) -> None:
    """Shared logic for routing an action:: interaction to the agent session."""
    assert _orch and _orch.slack_desk  # caller already checked  # noqa: S101

    # Redact label before any Slack surface
    label, _ = redact_exfiltration_urls(label)
    label = redact_credentials(label)[0]

    # Update the message: replace clicked element with ✓ label
    updated_blocks = _mark_button_clicked(blocks, action_id_value, label)
    try:
        await _orch.slack_desk.update_message(
            channel, msg_ts, text=label, blocks=updated_blocks
        )
    except Exception:
        logger.debug("Failed to update action message", exc_info=True)

    # Post display text as visible user message
    new_ts = await _orch.slack_desk.post_message(channel, label, thread_ts)
    if not new_ts:
        logger.warning("Failed to post action label — aborting action routing")
        return

    # Redact and cap payload before embedding in context
    payload_str, _ = redact_exfiltration_urls(payload_str)
    payload_str = redact_credentials(payload_str)[0]
    if len(payload_str) > _ACTION_PAYLOAD_CAP:
        payload_str = payload_str[:_ACTION_PAYLOAD_CAP] + "… [truncated]"

    # SEL audit trail
    sel().log_api_access(
        caller=user_id,
        operation=f"slack.{context_tag.split()[0].lower()}",
        outcome="allowed",
        source="slack",
        resources=action_id_value,
    )

    # Build context entry for the agent
    action_context = (
        "--- CONTEXT ENTRY BEGIN ---\n"
        f"[{context_tag}: {payload_str}]\n"
        "--- CONTEXT ENTRY END ---"
    )

    t = asyncio.create_task(
        handle_message(
            _orch.slack_desk,
            _orch.sessions,  # type: ignore[arg-type]
            channel,
            label,
            thread_ts,
            new_ts,
            user_id,
            team_id=team_id,
            approval_mode=APPROVAL_INTERACTIVE,
            context_builder=_orch.ctx_builder,
            conversation_log=_orch.conv_log,
            consolidator=_orch.consolidator,
            subagent_manager=_orch.subagent_mgr,
            action_context=action_context,
        )
    )
    _orch._handler_tasks.add(t)
    t.add_done_callback(_orch._handler_tasks.discard)


async def _import_thread_to_session(slack_desk: Any, ds: Any, channel: str, thread_ts: str) -> Any:
    """Fetch a Slack thread, redact messages, and import into a new dashboard session."""
    from gideon.sdk.channel import save_session_to_history

    # Idempotency: return existing session if already linked
    existing = ds.get_linked_session(thread_ts)
    if existing:
        return existing

    msgs = await slack_desk.fetch_thread_replies(channel, thread_ts)
    if not msgs:
        return None
    # Pre-filter: drop empty text and !link-to-dashboard messages
    msgs = [m for m in msgs if m.get("text", "").strip() and not m.get("text", "").startswith("!link-to-dashboard")]
    if not msgs:
        return None
    # Cap to last 50 messages to avoid bloating the session
    truncated = len(msgs) > 50
    if truncated:
        msgs = msgs[-50:]
    session = ds.get_or_create_session()
    session.title = f"Slack thread {thread_ts[:10]}" + (" (truncated)" if truncated else "")
    bot_id = getattr(ds, "_self_bot_id", None) or ""
    for m in msgs:
        is_bot = bool(m.get("bot_id")) or m.get("user") == bot_id
        role = "assistant" if is_bot else "user"
        text_content = m.get("text", "")
        text_content, _ = redact_exfiltration_urls(text_content)
        text_content, _ = redact_credentials(text_content)
        session.append(role, text_content, f"msg msg-{'a' if is_bot else 'u'}")
    ds.link_channel(session.key, thread_ts, channel)
    save_session_to_history(ds, session)
    ds.push_sessions_update()
    return session


async def _handle_options_submit(payload: dict, channel: str, msg_ts: str) -> None:
    """User clicked Send on multi-select OPTIONS checkboxes."""
    if not (_orch and _orch.slack_desk):
        return

    thread_ts = payload.get("message", {}).get("thread_ts") or msg_ts
    user_id = payload.get("user", {}).get("id", "")
    team_id = (payload.get("team") or {}).get("id", "")

    if not is_allowed_user(user_id):
        sel().log_tool_invocation(
            session_key=thread_ts, agent="gideon", source="slack",
            tool_name="options_submit", tool_kind="interaction",
            outcome="denied",
            metadata={"user_id": user_id, "reason": "not_allowed_user"},
        )
        return

    # Read checkbox state from the payload's state.values
    state_values = payload.get("state", {}).get("values", {})
    selected: list[str] = []
    for block_vals in state_values.values():
        cb_state = block_vals.get(OPTIONS_CHECKBOXES_ACTION)
        if cb_state:
            selected = [o["value"] for o in cb_state.get("selected_options", [])]
            break

    if not selected:
        sel().log_tool_invocation(
            session_key=thread_ts, agent="gideon", source="slack",
            tool_name="options_submit", tool_kind="interaction",
            outcome="skipped", metadata={"reason": "empty_selection"},
        )
        return  # nothing checked, ignore

    # Extract all choices for the styled summary
    blocks = payload.get("message", {}).get("blocks", [])
    all_choices: list[str] = []
    for b in blocks:
        if b.get("type") != "actions":
            continue
        for el in b.get("elements", []):
            if el.get("action_id") == OPTIONS_CHECKBOXES_ACTION:
                all_choices = [o["value"] for o in el.get("options", [])]
                break

    # Compute indices BEFORE redaction — deduplicate to handle identical choices
    selected_set = set(selected)
    selected_indices: list[int] = []
    seen: set[str] = set()
    for i, c in enumerate(all_choices):
        if c in selected_set and c not in seen:
            selected_indices.append(i)
            seen.add(c)

    # Redact
    selected = [redact_credentials(redact_exfiltration_urls(s)[0])[0] for s in selected]
    all_choices = [redact_credentials(redact_exfiltration_urls(c)[0])[0] for c in all_choices]

    combined = ", ".join(selected)

    # Edit-in-place: replace only the OPTIONS actions block(s) with the
    # styled selection, preserving every other surrounding block. Falls back
    # to post-and-delete if update_message raises (resilience).
    selected_blocks = build_options_selected_blocks(all_choices, selected_indices)
    parent_blocks = payload.get("message", {}).get("blocks", [])
    new_blocks = _replace_options_blocks(parent_blocks, selected_blocks)
    new_ts = msg_ts
    edited = False
    try:
        await _orch.slack_desk.update_message(
            channel, msg_ts, text=combined, blocks=new_blocks
        )
        edited = True
    except Exception:
        logger.debug(
            "update_message failed for options_submit, falling back to post+delete",
            exc_info=True,
        )

    if not edited:
        posted_ts = await _orch.slack_desk.post_blocks(
            channel, selected_blocks, combined, thread_ts
        )
        if not posted_ts:
            logger.warning("Failed to post options choice — aborting")
            sel().log_tool_invocation(
                session_key=thread_ts, agent="gideon", source="slack",
                tool_name="options_submit", tool_kind="interaction",
                outcome="failure", metadata={"reason": "post_blocks_failed"},
            )
            return
        new_ts = posted_ts
        try:
            await _orch.slack_desk.delete_message(channel, msg_ts)
        except Exception:
            logger.warning(
                "Failed to delete original OPTIONS message after fallback "
                "post_blocks succeeded; user may see both the original "
                "and the new selection message",
                exc_info=True,
            )

    action_context = (
        "--- CONTEXT ENTRY BEGIN ---\n"
        f"[OPTIONS multi-select: {combined}]\n"
        "--- CONTEXT ENTRY END ---"
    )

    t = asyncio.create_task(
        handle_message(
            _orch.slack_desk,
            _orch.sessions,  # type: ignore[arg-type]
            channel,
            combined,
            thread_ts,
            new_ts,
            user_id,
            team_id=team_id,
            approval_mode=APPROVAL_INTERACTIVE,
            context_builder=_orch.ctx_builder,
            conversation_log=_orch.conv_log,
            consolidator=_orch.consolidator,
            subagent_manager=_orch.subagent_mgr,
            action_context=action_context,
        )
    )
    _orch._handler_tasks.add(t)
    t.add_done_callback(_orch._handler_tasks.discard)
    sel().log_tool_invocation(
        session_key=thread_ts, agent="gideon", source="slack",
        tool_name="options_submit", tool_kind="interaction",
        outcome="success", metadata={"selected": combined, "channel": channel},
    )


async def _handle_options(payload: dict, action: dict, channel: str, msg_ts: str) -> None:
    """User picked an OPTIONS choice — delete footer, post styled selection."""
    choice = action.get("value", "")
    # Overflow menus nest the value under selected_option
    if not choice:
        choice = (action.get("selected_option") or {}).get("value", "")
    action_id = action.get("action_id", "")
    if not ((choice or action_id.startswith(_ACTION_PREFIX)) and channel and _orch and _orch.slack_desk):
        return

    thread_ts = payload.get("message", {}).get("thread_ts") or msg_ts
    user_id = payload.get("user", {}).get("id", "")
    team_id = (payload.get("team") or {}).get("id", "")
    blocks = payload.get("message", {}).get("blocks", [])

    # ── Action button: route payload to existing session as context ──
    if choice.startswith(_ACTION_PREFIX):
        action_payload = choice[len(_ACTION_PREFIX):]
        label = action.get("text", {}).get("text", "")
        # Overflow menus: label is on the selected_option
        if not label:
            label = (action.get("selected_option") or {}).get("text", {}).get("text", "")
        action_id_value = action.get("action_id", "")
        await _route_action_to_session(
            channel, msg_ts, thread_ts, user_id, team_id,
            label, action_payload, "Action button clicked",
            action_id_value, blocks,
        )
        return

    # ── Extended element: action_id carries the action:: prefix ──
    action_id_value = action.get("action_id", "")
    if action_id_value.startswith(_ACTION_PREFIX):
        base_json = action_id_value[len(_ACTION_PREFIX):]
        raw_value, display_text = _extract_selected_value(action)

        # Merge selected_value into base payload
        try:
            merged = json.loads(base_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid JSON in action_id: %s", base_json[:200])
            return
        if not isinstance(merged, dict):
            logger.warning("Expected dict from action_id JSON, got %s", type(merged).__name__)
            return
        merged["selected_value"] = raw_value
        merged_json = json.dumps(merged)

        # Derive display label: placeholder + selected text
        placeholder = action.get("placeholder", {}).get("text", "")
        label = f"{placeholder}: {display_text}" if placeholder else display_text

        await _route_action_to_session(
            channel, msg_ts, thread_ts, user_id, team_id,
            label, merged_json, "Action element selected",
            action_id_value, blocks,
        )
        return

    # ── Standard OPTIONS choice: delete message, post value, new session ──

    # Determine which button was clicked
    try:
        selected_index = int(action_id.replace(OPTIONS_ACTION_PREFIX, ""))
    except (ValueError, TypeError):
        selected_index = 0

    # Extract all choices from the original message
    blocks = payload.get("message", {}).get("blocks", [])
    all_choices = [
        el.get("value", "")
        for b in blocks
        if b.get("type") == "actions"
        for el in b.get("elements", [])
        if el.get("action_id", "").startswith(OPTIONS_ACTION_PREFIX)
    ]

    # Redact LLM-generated content before any external use
    choice, _ = redact_exfiltration_urls(choice)
    choice, _ = redact_credentials(choice)
    all_choices = [redact_credentials(redact_exfiltration_urls(c)[0])[0] for c in all_choices]

    # Edit-in-place: replace only the OPTIONS actions block with the styled
    # selection, preserving every other surrounding block. Falls back to
    # post-and-delete if update_message raises.
    selected_blocks = build_options_selected_blocks(all_choices, selected_index)
    new_blocks = _replace_options_blocks(blocks, selected_blocks)
    new_ts = msg_ts
    edited = False
    try:
        await _orch.slack_desk.update_message(
            channel, msg_ts, text=choice, blocks=new_blocks
        )
        edited = True
    except Exception:
        logger.debug(
            "update_message failed for options choice, falling back to post+delete",
            exc_info=True,
        )

    if not edited:
        posted_ts = await _orch.slack_desk.post_blocks(
            channel, selected_blocks, choice, thread_ts
        )
        if not posted_ts:
            logger.warning("Failed to post options choice — aborting")
            sel().log_tool_invocation(
                session_key=thread_ts, agent="gideon", source="slack",
                tool_name="options", tool_kind="interaction",
                outcome="failure", metadata={"reason": "post_blocks_failed"},
            )
            return
        new_ts = posted_ts
        try:
            await _orch.slack_desk.delete_message(channel, msg_ts)
        except Exception:
            logger.warning(
                "Failed to delete original OPTIONS message after fallback "
                "post_blocks succeeded; user may see both the original "
                "and the new selection message",
                exc_info=True,
            )

    t = asyncio.create_task(
        handle_message(
            _orch.slack_desk,
            _orch.sessions,  # type: ignore[arg-type]
            channel,
            choice,
            thread_ts,
            new_ts,
            user_id,
            team_id=team_id,
            approval_mode=APPROVAL_INTERACTIVE,
            context_builder=_orch.ctx_builder,
            conversation_log=_orch.conv_log,
            consolidator=_orch.consolidator,
            subagent_manager=_orch.subagent_mgr,
        )
    )
    _orch._handler_tasks.add(t)
    t.add_done_callback(_orch._handler_tasks.discard)


async def _handle_cron_ack(payload: dict, action: dict, channel: str, msg_ts: str) -> None:
    """Acknowledge a cron notification from its Slack button.

    🔴 The `cron_svc.ack_job(...)` call is GONE (S112). Core deleted `ScheduleService`, and that write
    was already dead weight: its `acked_items` field maps to `None` in `LEGACY_FIELD_MAP` (core S98
    verified the whole ack surface had zero consumers — no frontend client method, no MCP tool, and an
    empty field on the owner's real store) and the route was removed in S110.

    The load-bearing half stays, and always was: acknowledging the DASHBOARD notification, exactly as
    the sibling `_handle_subagent_ack` does. The gate now needs no scheduler either.
    """
    job_id = action.get("value", "")
    if not (job_id and _orch):
        return
    await ack_button(payload, channel, msg_ts)
    if _orch.dashboard_state:
        for n in _orch.dashboard_state._notification_log:
            if n.get("job_id") == job_id and not n.get("acked"):
                _orch.dashboard_state.ack_notification(n["ts"])
                _orch.dashboard_state.broadcast_ws("notification_ack", {"ts": n["ts"]})


async def _handle_subagent_ack(payload: dict, action: dict, channel: str, msg_ts: str) -> None:
    subagent_id = action.get("value", "")
    await ack_button(payload, channel, msg_ts)
    if not (subagent_id and _orch and _orch.dashboard_state):
        return
    for n in _orch.dashboard_state._notification_log:
        if n.get("kind") == "subagent" and subagent_id in n.get("title", "") and not n.get("acked"):
            _orch.dashboard_state.ack_notification(n["ts"])
            _orch.dashboard_state.broadcast_ws("notification_ack", {"ts": n["ts"]})


async def _handle_allowlist(
    payload: dict,
    action: dict,
    action_id: str,
    channel: str,
    msg_ts: str,
    approver_id: str,
) -> None:
    """Process an allowlist approve or deny button click."""
    raw_value = action.get("value", "")
    new_user_id, _, display_name = raw_value.partition(":")
    if not new_user_id:
        logger.warning("Allowlist button missing user_id in value=%r", raw_value)
        return

    label = ""
    if action_id == ACTION_ALLOWLIST_APPROVE:
        if not _orch:
            logger.error("Allowlist approve: orchestrator not initialized")
            return
        _orch._allowed_users.add(new_user_id)
        set_allowed_users(_orch._allowed_users)
        persist_allowed_user(new_user_id, name=display_name)
        sel().log_api_access(
            caller=approver_id,
            operation="slack.allowlist.approve",
            outcome="allowed",
            source="slack",
            resources=new_user_id,
        )
        label = f"✅ `{display_name or new_user_id}` added to allowlist"
        # Notify the approved user
        if _orch.slack_desk:
            try:
                dm = await _orch.slack_desk.open_dm(new_user_id)
                await _orch.slack_desk.post_message(
                    dm,
                    "✅ You've been added to the allowlist. You can now message me!\n\n"
                    "⚠️ *Review your organization's AI usage policies before sharing"
                    " sensitive data.*",
                )
            except Exception:
                logger.debug("Failed to DM approved user %s", new_user_id, exc_info=True)

    elif action_id == ACTION_ALLOWLIST_DENY:
        if not _orch:
            logger.error("Allowlist deny: orchestrator not initialized")
            return
        # Remove from in-memory set and persisted config
        _orch._allowed_users.discard(new_user_id)
        set_allowed_users(_orch._allowed_users)
        persist_allowed_user(new_user_id, remove=True)
        sel().log_api_access(
            caller=approver_id,
            operation="slack.allowlist.deny",
            outcome="denied",
            source="slack",
            resources=new_user_id,
        )
        label = f"🚫 `{display_name or new_user_id}` removed from allowlist"
        if _orch.slack_desk and new_user_id:
            try:
                dm = await _orch.slack_desk.open_dm(new_user_id)
                await _orch.slack_desk.post_message(
                    dm, "🚫 Your access request was denied by the owner."
                )
            except Exception:
                logger.debug("Failed to DM denied user %s", new_user_id, exc_info=True)

    # Replace the buttons message with the outcome
    if label and _orch and _orch.slack_desk and channel and msg_ts:
        try:
            await _orch.slack_desk.update_message(channel, msg_ts, text=label)
        except Exception:
            pass


async def _handle_track_channel(
    payload: dict,
    action: dict,
    action_id: str,
    channel: str,
    msg_ts: str,
    approver_id: str,
) -> None:
    """Process a tracking-channel approve or deny button click."""
    raw_value = action.get("value", "")
    target_channel_id, _, channel_name = raw_value.partition(":")
    if not target_channel_id:
        logger.warning("Track channel button missing channel_id in value=%r", raw_value)
        return

    label = ""
    if action_id == ACTION_TRACK_APPROVE:
        if not _orch:
            logger.error("Track channel approve: orchestrator not initialized")
            return
        _orch._tracking_channels.add(target_channel_id)
        set_tracking_channels(_orch._tracking_channels)
        persist_tracking_channel(target_channel_id, name=channel_name)
        sel().log_api_access(
            caller=approver_id,
            operation="slack.track_channel.approve",
            outcome="allowed",
            source="slack",
            resources=target_channel_id,
        )
        label = f"✅ Now tracking `#{channel_name or target_channel_id}`"

    elif action_id == ACTION_TRACK_DENY:
        if not _orch:
            logger.error("Track channel deny: orchestrator not initialized")
            return
        # Remove from in-memory set and persisted config
        _orch._tracking_channels.discard(target_channel_id)
        set_tracking_channels(_orch._tracking_channels)
        persist_tracking_channel(target_channel_id, remove=True)
        sel().log_api_access(
            caller=approver_id,
            operation="slack.track_channel.deny",
            outcome="denied",
            source="slack",
            resources=target_channel_id,
        )
        label = f"🚫 Removed `#{channel_name or target_channel_id}` from tracking"

    # Replace the buttons message with the outcome
    if label and _orch and _orch.slack_desk and channel and msg_ts:
        try:
            await _orch.slack_desk.update_message(channel, msg_ts, text=label)
        except Exception:
            pass


async def _handle_agent_select(
    payload: dict, action: dict, channel: str, msg_ts: str, user_id: str
) -> None:
    """Handle agent static_select — switch agent and collapse message."""
    from slack_desk_runtime.handler import (
        _resolve_agent_name,
        _set_default_agent,
        is_owner,
    )

    if not is_owner(user_id):
        return

    selected = action.get("selected_option", {})
    agent_name = selected.get("value", "")
    if not agent_name:
        return

    if agent_name.lower() in ("off", "default"):
        try:
            _set_default_agent("")
        except ValueError:
            return
        label = "🔄 Reset to default agent."
    else:
        resolved = _resolve_agent_name(agent_name)
        if not resolved:
            return
        try:
            _set_default_agent(resolved)
        except ValueError:
            return
        label = f"🔄 Switched to agent: *{resolved}*"

    blks = [{"type": "section", "text": {"type": "mrkdwn", "text": label}}]

    response_url = payload.get("response_url", "")
    if response_url:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    response_url,
                    json={"replace_original": True, "text": label, "blocks": blks},
                )
                return
        except Exception:
            pass

    if _orch and _orch.slack_desk and channel and msg_ts:
        try:
            await _orch.slack_desk.update_message(channel, msg_ts, text=label, blocks=blks)
        except Exception:
            pass


async def _handle_stop_confirm(payload: dict, channel: str, msg_ts: str, user_id: str) -> None:
    """Stop the current session when user confirms.

    Defense-in-depth: re-checks the allowlist even though dispatch()
    also enforces it, matching the deny-by-default pattern used by
    other privileged handlers. stop_turn() can escalate to a hard kill,
    so handler-level authorization is required.
    """
    if not _orch or not _orch.sessions:
        await ack_button(payload, channel, msg_ts)
        return
    if not is_allowed_user(user_id):
        logger.warning("stop_confirm denied for unauthorized user %s", user_id or "unknown")
        sel().log_api_access(
            caller=user_id or "unknown",
            operation="slack.stop_confirm",
            outcome="denied",
            source="slack",
            resources=channel,
            error="unauthorized user",
        )
        await ack_button(payload, channel, msg_ts)
        return

    # Find the active session in this channel/thread
    thread_ts = payload.get("message", {}).get("thread_ts") or msg_ts
    has_session = _orch.sessions.has_session(thread_ts)
    active_task = _orch._session_tasks.pop(thread_ts, None)

    if has_session or active_task:
        response_url = payload.get("response_url", "")

        async def _update_ephemeral(blocks: list[dict], text: str) -> None:
            if response_url:
                import aiohttp

                try:
                    async with aiohttp.ClientSession() as sess:
                        await sess.post(
                            response_url,
                            json={"replace_original": True, "text": text, "blocks": blocks},
                        )
                except Exception:
                    pass

        async def _on_soft() -> None:
            from slack_desk_runtime.blocks import build_stopped_blocks

            await _update_ephemeral(build_stopped_blocks(), "⏹ [Stopped]")
            if _orch and _orch.slack_desk:
                await _orch.slack_desk.post_message(
                    channel, "⏹ Execution stopped.", thread_ts
                )

        async def _on_hard() -> None:
            from slack_desk_runtime.blocks import build_stop_failed_blocks

            await _update_ephemeral(
                build_stop_failed_blocks(), "⛔ [Stop Failed, Session Reset]"
            )
            if _orch and _orch.slack_desk:
                await _orch.slack_desk.post_message(
                    channel, "⛔ Execution stopped — session reset.", thread_ts
                )

        outcome = await _orch.sessions.stop_turn(
            thread_ts, on_soft=_on_soft, on_hard=_on_hard
        )
        if active_task and not active_task.done():
            active_task.cancel()
        # If stop_turn returned "idle" (no active turn), neither callback
        # fired — dismiss the stale ephemeral with a "Nothing running" message.
        if outcome == "idle":
            await _update_ephemeral([], "Nothing running.")
        sel().log_tool_invocation(
            session_key=thread_ts,
            source="slack",
            tool_name="/gideon stop",
            tool_kind="command",
            outcome=outcome,
            metadata={"user": user_id, "channel": channel},
        )
    else:
        # Replace buttons with confirmation
        response_url = payload.get("response_url", "")
        label = "Nothing running."
        if response_url:
            import aiohttp

            try:
                async with aiohttp.ClientSession() as sess:
                    await sess.post(
                        response_url,
                        json={"replace_original": True, "text": label},
                    )
            except Exception:
                pass
        elif _orch.slack_desk:
            try:
                await _orch.slack_desk.update_message(channel, msg_ts, text=label)
            except Exception:
                pass


async def _handle_stop_cancel(payload: dict, channel: str, msg_ts: str) -> None:
    """Delete the ephemeral stop confirmation message on cancel."""
    response_url = payload.get("response_url", "")
    if response_url:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    response_url,
                    json={
                        "delete_original": True,
                    },
                )
        except Exception:
            pass
    elif _orch and _orch.slack_desk:
        try:
            await _orch.slack_desk.delete_message(channel, msg_ts)
        except Exception:
            pass


async def _handle_stop_kill_now(
    payload: dict, action: dict, channel: str, msg_ts: str, user_id: str
) -> None:
    """Force-kill via the ephemeral Kill Now button.

    Defense-in-depth: re-checks the allowlist even though dispatch()
    also enforces it, matching the deny-by-default pattern used by
    other privileged handlers (e.g. ``_handle_allowlist_remove``).
    """
    if not _orch or not _orch.sessions:
        return
    if not is_allowed_user(user_id):
        logger.warning("stop_kill_now denied for unauthorized user %s", user_id or "unknown")
        sel().log_api_access(
            caller=user_id or "unknown",
            operation="slack.stop_kill_now",
            outcome="denied",
            source="slack",
            resources=action.get("value", ""),
            error="unauthorized user",
        )
        return
    session_key = action.get("value", "")
    if not session_key:
        return

    response_url = payload.get("response_url", "")

    async def _on_hard() -> None:
        from slack_desk_runtime.blocks import build_stop_failed_blocks

        if response_url:
            import aiohttp

            try:
                async with aiohttp.ClientSession() as sess:
                    await sess.post(
                        response_url,
                        json={
                            "replace_original": True,
                            "text": "⛔ [Stop Failed, Session Reset]",
                            "blocks": build_stop_failed_blocks(),
                        },
                    )
            except Exception:
                pass
        if _orch and _orch.slack_desk:
            # Use the ephemeral's thread_ts (falling back to its own ts)
            # rather than session_key: for linked dashboard sessions these
            # differ, and session_key would not be a valid Slack thread.
            thread_ts = payload.get("message", {}).get("thread_ts") or msg_ts
            await _orch.slack_desk.post_message(
                channel, "⛔ Execution stopped — session reset.", thread_ts
            )

    outcome = await _orch.sessions.stop_turn(session_key, force=True, on_hard=_on_hard)
    sel().log_tool_invocation(
        session_key=session_key,
        source="slack",
        tool_name="stop_kill_now",
        tool_kind="command",
        outcome=outcome,
        metadata={"user": user_id, "channel": channel},
    )


async def _handle_allowlist_remove(
    payload: dict, action: dict, channel: str, msg_ts: str, user_id: str
) -> None:
    """Remove a user from the allowlist via the remove button."""
    if not is_owner(user_id) or not _orch:
        return
    target_id = action.get("value", "")
    if not target_id:
        return

    _orch._allowed_users.discard(target_id)
    set_allowed_users(_orch._allowed_users)
    persist_allowed_user(target_id, remove=True)

    from slack_desk_runtime.blocks import allowlist_list_block

    blks = allowlist_list_block(sorted(_orch._allowed_users))
    blks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"🚫 Removed <@{target_id}>"}]}
    )

    response_url = payload.get("response_url", "")
    if response_url:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    response_url,
                    json={"replace_original": True, "text": "Allowlist updated", "blocks": blks},
                )
                return
        except Exception:
            pass
    if _orch.slack_desk and channel and msg_ts:
        try:
            await _orch.slack_desk.update_message(channel, msg_ts, text="Allowlist updated", blocks=blks)
        except Exception:
            pass


async def _handle_channel_remove(
    payload: dict, action: dict, channel: str, msg_ts: str, user_id: str
) -> None:
    """Remove a channel from tracking via the remove button."""
    if not is_owner(user_id) or not _orch:
        return
    target_id = action.get("value", "")
    if not target_id:
        return

    _orch._tracking_channels.discard(target_id)
    set_tracking_channels(_orch._tracking_channels)
    persist_tracking_channel(target_id, remove=True)

    from slack_desk_runtime.blocks import channel_list_block

    blks = channel_list_block(sorted(_orch._tracking_channels))
    blks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"🚫 Removed <#{target_id}>"}]}
    )

    response_url = payload.get("response_url", "")
    if response_url:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    response_url,
                    json={
                        "replace_original": True,
                        "text": "Tracked channels updated",
                        "blocks": blks,
                    },
                )
                return
        except Exception:
            pass
    if _orch.slack_desk and channel and msg_ts:
        try:
            await _orch.slack_desk.update_message(
                channel, msg_ts, text="Tracked channels updated", blocks=blks
            )
        except Exception:
            pass


async def _handle_session_resume(
    payload: dict, action: dict, channel: str, msg_ts: str, user_id: str
) -> None:
    """Show choice buttons for how to resume a session."""
    import json

    if not is_owner(user_id):
        logger.warning("session_resume rejected: non-owner %s", user_id)
        sel().log_api_access(caller=user_id, operation="slack.session_resume", outcome="denied", source="slack")
        return
    if not (_orch and _orch.sessions and _orch.slack_desk):
        return

    try:
        val = json.loads(action.get("value", "{}"))
    except (ValueError, json.JSONDecodeError):
        val = {"key": action.get("value", "")}

    session_key = val.get("key", "")
    from gideon.sdk.channel import redact_and_truncate
    title = redact_and_truncate(val.get("title", session_key[:20]), max_chars=200)

    if not session_key:
        return

    # Check if session already has a linked thread/channel
    existing_thread, existing_channel = _orch.sessions.get_channel_link(session_key)

    if existing_thread and existing_channel:
        link = f"https://slack.com/archives/{existing_channel}/p{existing_thread.replace('.', '')}"
        label = f"\U0001f9f5 This session is already active: <{link}|Go to conversation>"
        response_url = payload.get("response_url", "")
        if response_url:
            import aiohttp
            try:
                async with aiohttp.ClientSession() as sess:
                    await sess.post(response_url, json={"replace_original": False, "text": label})
            except Exception:
                pass
        return

    # Show choice buttons
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    choice_value = json.dumps({"key": session_key, "title": title, "src_channel": channel})
    short_id = hashlib.sha256(session_key.encode()).hexdigest()[:12]
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"\U0001f504 Resume *{title}*\nWhere would you like to continue?",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "\U0001f4ce Thread"},
                    "action_id": f"pc_resume_thread_{short_id}",
                    "value": choice_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "\U0001f4ac DM"},
                    "action_id": f"pc_resume_dm_{short_id}",
                    "value": choice_value,
                },
            ],
        },
    ]
    response_url = payload.get("response_url", "")
    if response_url:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json={
                    "replace_original": False,
                    "text": f"Resume {title} \u2014 choose Thread or DM",
                    "blocks": blocks,
                })
        except Exception:
            pass
    else:
        try:
            await _orch.slack_desk.post_blocks(
                channel, blocks, f"Resume {title} \u2014 choose Thread or DM"  # type: ignore[arg-type]
            )
        except Exception:
            pass


_resume_locks: dict[str, asyncio.Lock] = {}


async def _handle_resume_choice(
    payload: dict,
    action: dict,
    channel: str,
    msg_ts: str,
    user_id: str,
    mode: str,
) -> None:
    """Dispatch session resume to thread or DM based on user choice."""
    import json

    if not is_owner(user_id):
        logger.warning("resume_choice rejected: non-owner %s", user_id)
        sel().log_api_access(caller=user_id, operation="slack.session_resume_choice", outcome="denied", source="slack")
        return
    if not (_orch and _orch.sessions and _orch.slack_desk):
        return

    try:
        val = json.loads(action.get("value", "{}"))
    except (ValueError, json.JSONDecodeError):
        return

    session_key = val.get("key", "")
    from gideon.sdk.channel import redact_and_truncate
    title = redact_and_truncate(val.get("title", session_key[:20]), max_chars=200)
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    src_channel = val.get("src_channel", channel)

    if not session_key:
        return

    # Bounded eviction to prevent unbounded memory growth
    if len(_resume_locks) > 1000:
        evicted = 0
        for k in list(_resume_locks):
            if evicted >= 200:
                break
            if not _resume_locks[k].locked():
                _resume_locks.pop(k, None)
                evicted += 1

    lock = _resume_locks.setdefault(session_key, asyncio.Lock())
    async with lock:
        # Re-check: session may have been linked while user was choosing
        existing_thread, existing_channel = _orch.sessions.get_channel_link(session_key)
        if existing_thread and existing_channel:
            link = f"https://slack.com/archives/{existing_channel}/p{existing_thread.replace('.', '')}"
            label = f"\U0001f9f5 Already active: <{link}|Go to conversation>"
            response_url = payload.get("response_url", "")
            if response_url:
                import aiohttp
                try:
                    async with aiohttp.ClientSession() as sess:
                        await sess.post(response_url, json={"replace_original": True, "text": label})
                except Exception:
                    pass
            return

        if mode == "thread":
            target_channel = src_channel
            thread_msg = (
                f"\U0001f9f5 *{title}*\n"
                "Session resumed. Continue the conversation in this thread."
            )
            try:
                thread_ts = await _orch.slack_desk.post_message(target_channel, thread_msg)
            except Exception:
                logger.debug("Failed to create session thread", exc_info=True)
                return
            if not thread_ts:
                return
            link_ts, link_channel = thread_ts, target_channel
            label = f"\u25b6\ufe0f Resumed *{title}* in thread."
        elif mode == "dm":
            try:
                dm_channel = await _orch.slack_desk.open_dm(user_id)
            except Exception:
                logger.debug("Failed to open DM for session resume", exc_info=True)
                return
            if not dm_channel:
                return
            header = (
                "\u2500" * 15 + "\n"
                f"\U0001f504 Resumed: *{title}*\n"
                + "\u2500" * 15
            )
            try:
                header_ts = await _orch.slack_desk.post_message(dm_channel, header)
            except Exception:
                logger.debug("Failed to post DM resume header", exc_info=True)
                return
            if not header_ts:
                return
            link_ts, link_channel = header_ts, dm_channel
            thread_ts = header_ts
            target_channel = dm_channel
            label = f"\u25b6\ufe0f Resumed *{title}* in DM."
        else:
            return

        # Link session
        _orch.sessions.set_channel_link(session_key, link_ts, link_channel)
        sel().log_api_access(
            caller=user_id,
            operation="slack.session_resume",
            outcome="allowed",
            source="slack",
            resources=session_key,
        )
        if _orch.dashboard_state:
            session_name = (
                session_key.split(":", 1)[-1] if ":" in session_key else session_key
            )
            _orch.dashboard_state.link_channel(session_name, link_ts, link_channel)

        # Post last 5 messages as context
        try:
            from pathlib import Path

            sess_dir = Path.home() / ".gideon" / "sessions"
            stem = session_key.split(":", 1)[-1] if ":" in session_key else session_key
            jsonl = sess_dir / f"{stem}.jsonl"
            if not jsonl.exists() and not stem.startswith("dashboard_"):
                jsonl = sess_dir / f"dashboard_{stem}.jsonl"
            if jsonl.exists():
                lines = jsonl.read_text(encoding="utf-8").splitlines()
                msgs: list[tuple[str, str]] = []
                for ln in lines:
                    try:
                        d = json.loads(ln.strip())
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if d.get("_type"):
                        continue
                    role = d.get("role", "")
                    txt = (d.get("content") or "")[:2000]
                    if role in ("user", "assistant") and txt:
                        msgs.append((role, txt))
                for role, txt in msgs[-5:]:
                    txt, _ = redact_exfiltration_urls(txt)
                    txt, _ = redact_credentials(txt)
                    icon = "\U0001f9d1" if role == "user" else "\U0001f916"
                    try:
                        await _orch.slack_desk.post_message(
                            target_channel, f"{icon} {txt}", thread_ts,
                        )
                    except Exception:
                        logger.debug("Failed to post context message", exc_info=True)
        except Exception:
            logger.debug("Failed to post session context", exc_info=True)

        # Update the choice message
        response_url = payload.get("response_url", "")
        if response_url:
            import aiohttp
            try:
                async with aiohttp.ClientSession() as sess:
                    await sess.post(
                        response_url, json={"replace_original": True, "text": label},
                    )
            except Exception:
                pass


async def _handle_session_end(
    payload: dict, action: dict, channel: str, msg_ts: str, user_id: str
) -> None:
    """End a session by removing it from SessionMap and resetting if active."""
    if not is_owner(user_id):
        logger.warning("session_end rejected: non-owner %s", user_id)
        return
    session_id = action.get("value", "")
    if not (session_id and _orch and _orch.sessions):
        return

    sel().log_api_access(caller=user_id, operation="slack.session_end", outcome="allowed", source="slack", resources=session_id)

    key_to_remove = _orch.sessions.find_key_by_sid(session_id)
    if key_to_remove:
        # E11: ending a session is an explicit close — extract skills from the
        # full transcript one last time before the process goes away.
        if _orch.consolidator is not None:
            try:
                await _orch.consolidator.consolidate_session(key_to_remove)
            except Exception:
                logger.debug("session end consolidate failed for %s", key_to_remove, exc_info=True)
        # Soft-remove: kill process but preserve session_map for future resume
        try:
            await _orch.sessions.remove(key_to_remove)
        except Exception:
            logger.debug("session end remove failed for %s", key_to_remove, exc_info=True)

    response_url = payload.get("response_url", "")
    label = f"🛑 Session `{session_id[:12]}…` ended."
    if response_url:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json={"replace_original": True, "text": label})
                return
        except Exception:
            pass
    if _orch.slack_desk and channel and msg_ts:
        try:
            await _orch.slack_desk.update_message(channel, msg_ts, text=label)
        except Exception:
            pass


async def _handle_session_new(
    payload: dict, action: dict, channel: str, msg_ts: str, user_id: str
) -> None:
    """Create a fresh session by posting a prompt in a new thread."""
    if not is_owner(user_id):
        logger.warning("session_new rejected: non-owner %s", user_id)
        return
    if not (_orch and _orch.slack_desk):
        return
    sel().log_api_access(caller=user_id, operation="slack.session_new", outcome="allowed", source="slack", resources=channel)

    # Post a new message that starts a fresh thread
    try:
        await _orch.slack_desk.post_message(
            channel, "✨ New session started. Send your first message here."
        )
    except Exception:
        logger.debug("Failed to create new session message", exc_info=True)
        return

    # Ack the button
    response_url = payload.get("response_url", "")
    label = "✨ New session created."
    if response_url:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json={"replace_original": False, "text": label})
        except Exception:
            pass


async def _handle_tool_approval(
    payload: dict, action_id: str, channel: str, msg_ts: str, user_id: str
) -> None:
    """Route approve / trust / reject to the handler."""
    # Trust is restricted to DMs — fail-closed if orchestrator not ready
    if action_id == "trust_tool":
        if not _orch or not _orch.slack_desk:
            logger.warning("trust_tool: orchestrator not ready — rejecting")
            return
        is_dm = await _orch.slack_desk.is_dm(channel)
        if not is_dm:
            logger.warning("Rejecting trust_tool in non-DM channel %s (user=%s)", channel, user_id)
            return

    thread_ts = payload.get("message", {}).get("thread_ts", "")
    slack_desk_ops = _orch.slack_desk if _orch else None
    effective_action = await handle_interaction(channel, msg_ts, action_id, user_id=user_id, thread_ts=thread_ts, slack_desk=slack_desk_ops)

    # Replace buttons with outcome label — only when an action was processed.
    # When effective_action is None (unauthorized user or already resolved),
    # preserve buttons so the authorized owner can still click.
    if _orch and _orch.slack_desk and effective_action:
        label = {
            "approve_tool": "✅ Approved",
            "trust_tool": "🤝 Trusted",
            "reject_tool": "🚫 Rejected",
        }.get(effective_action, "")
        if label:
            try:
                await _orch.slack_desk.update_message(channel, msg_ts, text=label)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Review mode handlers
# ---------------------------------------------------------------------------

# Shown when a non-authorized user clicks a review-mode button.
_REVIEW_AUTH_DENIED_MSG = (
    "⚠️ Only the bot owner or the user who requested this draft can act on it."
)


async def _delete_review_placeholder(channel: str, thread_ts: str) -> None:
    """Clear the 'Awaiting review…' thread status indicator."""
    if not _orch or not _orch.slack_desk:
        return
    try:
        await _orch.slack_desk.set_thread_status(channel, thread_ts, "")
    except Exception:
        logger.debug("Failed to clear review thread status", exc_info=True)


async def _post_review_auth_error(response_url: str) -> None:
    """Reply with an ephemeral error via response_url (replaces the draft)."""
    if not response_url:
        return
    try:
        import aiohttp
        async with aiohttp.ClientSession() as sess:
            await sess.post(
                response_url,
                json={
                    "replace_original": True,
                    "response_type": "ephemeral",
                    "text": _REVIEW_AUTH_DENIED_MSG,
                },
            )
    except Exception:
        logger.debug("Failed to post review auth-denied ephemeral", exc_info=True)


def _parse_draft_key(meta: str) -> tuple[str, str, str] | None:
    """Parse draft key 'channel|thread_ts|uuid' → (channel, thread_ts, draft_key) or None."""
    parts = meta.split("|")
    if len(parts) < 2:
        return None
    channel, thread_ts = parts[0], parts[1]
    return channel, thread_ts, meta


def _can_act_on_review_draft(caller: str, requester: str) -> bool:
    """Authorize a review-mode action: bot owner OR the requester who triggered the draft."""
    return bool(caller) and (caller == requester or is_owner(caller))


async def _handle_review_approve(payload: dict, action: dict) -> None:
    """Post the approved draft to the channel."""
    if not _orch or not _orch.slack_desk:
        return
    caller = payload.get("user", {}).get("id", "")
    parsed = _parse_draft_key(action.get("value", ""))
    if not parsed:
        return
    channel, thread_ts, draft_key = parsed

    from slack_desk_runtime.handler import _review_drafts_get, _review_drafts_pop

    _, requester = _review_drafts_get(draft_key)
    if not _can_act_on_review_draft(caller, requester):
        sel().log_api_access(
            caller=caller,
            operation="slack.review_approve",
            outcome="denied",
            source="slack",
            error="not owner or requester",
        )
        await _post_review_auth_error(payload.get("response_url", ""))
        return

    draft, _requester = _review_drafts_pop(draft_key)
    if not draft:
        logger.warning("Review approve: no draft found for %s", draft_key)
        return
    draft, _ = redact_exfiltration_urls(draft)
    draft, _ = redact_credentials(draft)
    await _orch.slack_desk.post_message(channel, draft, thread_ts)
    await _delete_review_placeholder(channel, thread_ts)
    # Delete the ephemeral draft message
    response_url = payload.get("response_url", "")
    if response_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json={"delete_original": True})
        except Exception:
            logger.debug("Failed to delete review ephemeral", exc_info=True)
    sel().log_api_access(
        caller=caller,
        operation="slack.review_approve",
        outcome="allowed",
        source="slack",
        resources=channel,
    )
    logger.info("Review approved by %s in %s", caller, channel)


async def _handle_review_edit(payload: dict, action: dict) -> None:
    """Open a modal pre-filled with the draft for editing."""
    if not _orch or not _orch.slack_desk:
        return
    caller = payload.get("user", {}).get("id", "")
    trigger_id = payload.get("trigger_id", "")
    if not trigger_id:
        logger.warning("Review edit: no trigger_id in payload")
        return
    parsed = _parse_draft_key(action.get("value", ""))
    if not parsed:
        return
    channel, thread_ts, draft_key = parsed

    from slack_desk_runtime.blocks import review_edit_modal
    from slack_desk_runtime.handler import _review_drafts_get

    draft, requester = _review_drafts_get(draft_key)
    if not _can_act_on_review_draft(caller, requester):
        sel().log_api_access(
            caller=caller,
            operation="slack.review_edit",
            outcome="denied",
            source="slack",
            error="not owner or requester",
        )
        await _post_review_auth_error(payload.get("response_url", ""))
        return
    if not draft:
        logger.warning("Review edit: no draft found for %s", draft_key)
        return
    modal = review_edit_modal(draft, draft_key)
    await _orch.slack_desk.views_open(trigger_id, modal)
    # Delete the ephemeral draft message
    response_url = payload.get("response_url", "")
    if response_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json={"delete_original": True})
        except Exception:
            logger.debug("Failed to delete review ephemeral", exc_info=True)
    sel().log_api_access(
        caller=caller,
        operation="slack.review_edit",
        outcome="allowed",
        source="slack",
        resources=channel,
    )


async def _handle_review_cancel(payload: dict, action: dict) -> None:
    """Discard the draft and delete the ephemeral message."""
    if not _orch or not _orch.slack_desk:
        return
    caller = payload.get("user", {}).get("id", "")
    parsed = _parse_draft_key(action.get("value", ""))
    if not parsed:
        return
    channel, thread_ts, draft_key = parsed

    from slack_desk_runtime.handler import _review_drafts_get, _review_drafts_pop

    _, requester = _review_drafts_get(draft_key)
    if not _can_act_on_review_draft(caller, requester):
        sel().log_api_access(
            caller=caller,
            operation="slack.review_cancel",
            outcome="denied",
            source="slack",
            error="not owner or requester",
        )
        await _post_review_auth_error(payload.get("response_url", ""))
        return

    _review_drafts_pop(draft_key)
    await _delete_review_placeholder(channel, thread_ts)
    # Delete the ephemeral draft message
    response_url = payload.get("response_url", "")
    if response_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json={"delete_original": True})
        except Exception:
            logger.debug("Failed to delete review ephemeral", exc_info=True)
    sel().log_api_access(
        caller=caller,
        operation="slack.review_cancel",
        outcome="allowed",
        source="slack",
        resources=channel,
    )
    logger.info("Review cancelled by %s in %s", caller, channel)


async def _handle_review_edit_submit(payload: dict) -> None:
    """Post the edited text from the review edit modal."""
    if not _orch or not _orch.slack_desk:
        return
    caller = payload.get("user", {}).get("id", "")
    view = payload.get("view", {})
    meta = view.get("private_metadata", "")
    parsed = _parse_draft_key(meta)
    if not parsed:
        return
    channel, thread_ts, draft_key = parsed

    from slack_desk_runtime.handler import _review_drafts_get, _review_drafts_pop

    _, requester = _review_drafts_get(draft_key)
    if not _can_act_on_review_draft(caller, requester):
        sel().log_api_access(
            caller=caller,
            operation="slack.review_edit_submit",
            outcome="denied",
            source="slack",
            error="not owner or requester",
        )
        return
    values = view.get("state", {}).get("values", {})
    edited = (
        values.get("pc_review_edit_block", {})
        .get("pc_review_edit_input", {})
        .get("value", "")
    )
    if not edited:
        return

    _review_drafts_pop(draft_key)
    edited, _ = redact_exfiltration_urls(edited)
    edited, _ = redact_credentials(edited)
    await _orch.slack_desk.post_message(channel, edited, thread_ts)
    await _delete_review_placeholder(channel, thread_ts)
    sel().log_api_access(
        caller=caller,
        operation="slack.review_edit_submit",
        outcome="allowed",
        source="slack",
        resources=channel,
    )
    logger.info("Review edited and posted by %s in %s", caller, channel)


# Register the edit modal submission handler
register_view_handler("pc_review_edit_submit", _handle_review_edit_submit)


async def _handle_review_revise(payload: dict, action: dict) -> None:
    """Open a modal for the user to provide revision feedback."""
    if not _orch or not _orch.slack_desk:
        return
    caller = payload.get("user", {}).get("id", "")
    trigger_id = payload.get("trigger_id", "")
    if not trigger_id:
        logger.warning("Review revise: no trigger_id in payload")
        return
    parsed = _parse_draft_key(action.get("value", ""))
    if not parsed:
        return
    channel, thread_ts, draft_key = parsed

    from slack_desk_runtime.blocks import review_revise_modal
    from slack_desk_runtime.handler import _review_drafts_get

    _, requester = _review_drafts_get(draft_key)
    if not _can_act_on_review_draft(caller, requester):
        sel().log_api_access(
            caller=caller,
            operation="slack.review_revise",
            outcome="denied",
            source="slack",
            error="not owner or requester",
        )
        await _post_review_auth_error(payload.get("response_url", ""))
        return

    modal = review_revise_modal(draft_key)
    await _orch.slack_desk.views_open(trigger_id, modal)
    # Delete the ephemeral draft message (new one will appear after revision)
    response_url = payload.get("response_url", "")
    if response_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                await sess.post(response_url, json={"delete_original": True})
        except Exception:
            logger.debug("Failed to delete review ephemeral", exc_info=True)
    sel().log_api_access(
        caller=caller,
        operation="slack.review_revise",
        outcome="allowed",
        source="slack",
        resources=channel,
    )


async def _handle_review_revise_submit(payload: dict) -> None:
    """Take revision feedback, send to LLM with draft context, post new ephemeral draft."""
    if not _orch or not _orch.slack_desk:
        return
    caller = payload.get("user", {}).get("id", "")
    view = payload.get("view", {})
    meta = view.get("private_metadata", "")
    parsed = _parse_draft_key(meta)
    if not parsed:
        return
    channel, thread_ts, draft_key = parsed

    from slack_desk_runtime.handler import _review_drafts_get, _review_drafts_pop

    _, requester = _review_drafts_get(draft_key)
    if not _can_act_on_review_draft(caller, requester):
        sel().log_api_access(
            caller=caller,
            operation="slack.review_revise_submit",
            outcome="denied",
            source="slack",
            error="not owner or requester",
        )
        return
    values = view.get("state", {}).get("values", {})
    feedback = (
        values.get("pc_review_revise_block", {})
        .get("pc_review_revise_input", {})
        .get("value", "")
    )
    if not feedback:
        return

    draft, _requester = _review_drafts_pop(draft_key)
    if not draft:
        logger.warning("Review revise: no draft found for %s", draft_key)
        return

    # Send revision request through handle_message with context
    revision_prompt = (
        f"I asked you a question and you drafted this response:\n\n"
        f"---\n{draft}\n---\n\n"
        f"Please revise it based on this feedback: {feedback}\n\n"
        f"Respond ONLY with the revised response text, nothing else."
    )
    # Use handle_message so the revision goes through the full pipeline
    # (including review mode interception → new ephemeral draft)
    # Fire-and-forget: Slack requires view_submission response within ~3s
    # Audit the permission decision before spawning the task so it's always recorded.
    sel().log_api_access(
        caller=caller,
        operation="slack.review_revise_submit",
        outcome="allowed",
        source="slack",
        resources=channel,
    )

    async def _do_revise() -> None:
        try:
            await handle_message(
                _orch.slack_desk,  # type: ignore[arg-type]
                _orch.sessions,  # type: ignore[arg-type]
                channel,
                revision_prompt,
                thread_ts,
                thread_ts,  # msg_ts = thread_ts for revision
                caller,
                approval_mode=APPROVAL_INTERACTIVE,
                context_builder=_orch.ctx_builder,
                conversation_log=_orch.conv_log,
                consolidator=_orch.consolidator,
                subagent_manager=_orch.subagent_mgr,
                channel_activation=ACTIVATION_REVIEW,
            )
            logger.info("Review revision requested by %s in %s", caller, channel)
        except Exception:
            sel().log_api_access(
                caller=caller,
                operation="slack.review_revise_submit",
                outcome="error",
                source="slack",
                resources=channel,
                error="handle_message failed",
            )
            logger.exception("Review revision failed for %s in %s", caller, channel)

    asyncio.create_task(_do_revise())


register_view_handler("pc_review_revise_submit", _handle_review_revise_submit)
