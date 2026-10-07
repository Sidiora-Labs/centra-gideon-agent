"""Channel integration — link sessions, handoff, channel listing."""

import logging

from aiohttp import web

from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.integrations.sync_bridge import handoff_to_channel
from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
from gideon.interfaces.dashboard.chat_utils import (
    _history_key_for,
    persisted_history_key,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.security import redact_and_truncate
from gideon.security.sel import sel

logger = logging.getLogger(__name__)


async def api_chat_session_channel_link(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/channel-link — link a dashboard session to a channel."""

    state: ConsoleState = request.app["state"]
    name = request.match_info.get("session", "")
    session = state.get_session(name) or state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    session_key = _history_key_for(name)

    try:
        body = await read_json_body(request)
    except RequestValidationError:
        return web.json_response({"error": "invalid handoff destination"}, status=400)
    provider = body.get("provider", "")
    raw_channel = body.get("channel", "dm")
    if (
        not isinstance(provider, str)
        or not isinstance(raw_channel, str)
        or not provider.strip()
    ):
        return web.json_response(
            {"error": "provider required for channel destination"}, status=400
        )
    provider = provider.strip()
    delivery = state.delivery_for(provider)
    if delivery is None:
        return web.json_response({"error": "channel unavailable"}, status=503)
    if raw_channel and raw_channel != "dm":
        if not delivery.is_tracked_channel(raw_channel):
            return web.json_response(
                {"error": "channel destination is not authorized"}, status=403
            )
        target_channel = raw_channel
    else:
        from gideon.core.config.credentials import owner_id_for

        owner = owner_id_for(provider)
        target_channel = str(await delivery.open_dm(owner) or "") if owner else ""
        if not target_channel:
            return web.json_response(
                {"error": "owner destination unavailable"}, status=503
            )
    existing_ts, existing_chan = state.sessions.get_channel_link(session_key)
    if (
        existing_ts
        and existing_chan == target_channel
        and state.channel_provider_for(session_key) == provider
    ):
        return web.json_response(
            {
                "ok": True,
                "already_linked": True,
                "provider": provider,
                "thread_ts": existing_ts,
                "channel": target_channel,
            }
        )
    title = redact_and_truncate(session.title or name, max_chars=200)
    opening = f"\U0001f9f5 *{title}*\nSession linked from dashboard."
    thread_ts = await delivery.deliver_text(target_channel, opening)
    if not thread_ts:
        return web.json_response({"error": "failed to create thread"}, status=500)

    try:
        save_session_to_history(state, session)
    except Exception:
        logger.warning("Could not persist linked chat provider identity", exc_info=True)
        return web.json_response(
            {"error": "failed to persist channel link"}, status=500
        )
    state.link_channel(name, thread_ts, target_channel, provider)

    for m in session.messages[-5:]:
        role = m.get("role", "")
        txt = redact_and_truncate(m.get("content") or "", max_chars=2000)
        if role in ("user", "assistant") and txt:
            icon = "\U0001f9d1" if role == "user" else "\U0001f916"
            try:
                await delivery.deliver_text(target_channel, f"{icon} {txt}", thread_ts)
            except Exception:
                pass

    sel().log_api_access(
        caller="dashboard",
        operation="chat.channel_link",
        outcome="success",
        source="dashboard",
        resources=session.key,
    )
    state.push_sessions_update()
    return web.json_response(
        {
            "ok": True,
            "provider": provider,
            "thread_ts": thread_ts,
            "channel": target_channel,
        }
    )


async def api_channel_reply_targets(request: web.Request) -> web.Response:
    """GET /api/channels/reply-targets — list channels the bot can reply in.

    The channel APP owns which channels are reply-eligible (its own tracked/active
    config), surfaced through the provider-agnostic ChannelDelivery seam — core
    holds no channel config."""
    state: ConsoleState = request.app["state"]
    delivery = state.channel_delivery
    if delivery is None or not hasattr(delivery, "list_reply_channels"):
        return web.json_response([{"id": "dm", "name": "Direct Message"}])
    try:
        return web.json_response(delivery.list_reply_channels())
    except Exception:
        logger.exception("list_reply_channels failed")
        return web.json_response([{"id": "dm", "name": "Direct Message"}])


async def api_chat_session_handoff(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/handoff — hand off session to channel DM thread."""

    state: ConsoleState = request.app["state"]
    name = request.match_info.get("session", "")
    session = state.get_session(name) or state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    if not state.conversation_log:
        return web.json_response({"error": "no conversation log"}, status=500)

    body = await read_json_body(request)
    provider = body.get("provider", "")
    channel = None
    if "channel" in body:
        channel = body.get("channel")
    if not isinstance(provider, str) or (
        channel is not None and not isinstance(channel, str)
    ):
        return web.json_response({"error": "invalid handoff destination"}, status=400)
    provider = provider.strip()
    channel = channel.strip() if isinstance(channel, str) else None

    if channel and not provider:
        return web.json_response(
            {"error": "provider required for channel destination"}, status=400
        )

    if provider:
        delivery = state.delivery_for(provider)
        if delivery is None:
            return web.json_response({"error": "channel unavailable"}, status=404)
        if channel:
            if not delivery.is_tracked_channel(channel):
                return web.json_response(
                    {"error": "channel destination is not authorized"}, status=403
                )
        else:
            from gideon.core.config.credentials import owner_id_for

            if not owner_id_for(provider):
                return web.json_response(
                    {"error": "owner destination unavailable"}, status=503
                )
    else:
        delivery = state.channel_delivery
        if not delivery:
            return web.json_response({"error": "Channel not connected"}, status=503)
        from gideon.integrations.channel_delivery import provider_for_delivery

        provider = provider_for_delivery(delivery)
        if not provider:
            return web.json_response({"error": "channel unavailable"}, status=503)
        from gideon.core.config.credentials import owner_id_for

        if not owner_id_for(provider):
            return web.json_response(
                {"error": "owner destination unavailable"}, status=503
            )

    try:
        save_session_to_history(state, session)
    except Exception:
        return web.json_response({"error": "handoff failed"}, status=500)

    history_key = persisted_history_key(state.conversation_log, session.key)
    thread_ts = await handoff_to_channel(
        delivery,
        "",
        state.conversation_log,
        history_key,
        title=session.title if session._titled else "",
        channel=channel,
        sessions=state.sessions,
        provider=provider,
    )
    if not thread_ts:
        return web.json_response({"error": "handoff failed"}, status=500)

    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_handoff",
        outcome="allowed",
        source="dashboard",
        resources=session.key,
    )
    return web.json_response({"ok": True, "thread_ts": thread_ts})
