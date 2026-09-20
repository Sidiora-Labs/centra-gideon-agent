"""Messaging handlers — spawn, notifications, send-message, channel profile."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from gideon.assurance.validation import (
    SPAWN_RUN_SCHEMA,
    ValidationError,
    validate_tool_args,
)
from gideon.core.http_request import read_json_body
from gideon.engine.subagent_persistence import _agent_dir, read_state
from gideon.interfaces.dashboard.chat_persistence import _rehydrate_session_from_history
from gideon.interfaces.dashboard.chat_utils import _remove_queued_by_id
from gideon.interfaces.dashboard.state import (
    CRON_NOTIFY_END,
    CRON_NOTIFY_PREFIX,
    ConsoleState,
    _rewrite_notifications,
)
from gideon.security.security import (
    is_sensitive_path,
    redact_credentials,
    redact_exfiltration_urls,
)
from gideon.workspace import notification_kinds

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import gideon.interfaces.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


async def api_spawn(request: web.Request) -> web.Response:
    """POST /api/spawn — spawn a subagent."""
    state: ConsoleState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    try:
        cleaned = validate_tool_args(
            {
                "task": body.get("task", ""),
                "agent": body.get("agent", ""),
                "max_turns": body.get("max_turns", 0),
                "cwd": body.get("cwd", ""),
            },
            SPAWN_RUN_SCHEMA,
        )
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    task = (cleaned.get("task") or "").strip()
    if not task:
        return web.json_response({"error": "task is required"}, status=400)
    parent_session = body.get("parent_session", "")
    approval_mode = body.get("approval_mode", "")
    if approval_mode not in ("", "auto"):
        return web.json_response(
            {"error": "approval_mode must be '' or 'auto'"}, status=400
        )
    silent = body.get("silent", False)
    if not isinstance(silent, bool):
        silent = str(silent).lower() in ("true", "1", "yes")
    agent = cleaned.get("agent") or ""
    max_turns = cleaned.get("max_turns") or 0
    cwd = cleaned.get("cwd") or ""
    info = state.subagents.spawn(
        task,
        parent_session_key=parent_session,
        agent=agent,
        max_turns=max_turns,
        cwd=cwd,
        approval_mode=approval_mode or None,
        silent=silent,
    )
    if not info:
        return web.json_response(
            {"error": f"capacity reached ({state.subagents.max_concurrent})"},
            status=429,
        )
    if info.done and info.error:
        return web.json_response({"error": info.error}, status=400)
    return web.json_response({"id": info.id, "task": task, "status": "spawned"})


def _redact(text: str) -> str:
    """Two-pass redaction for LLM-derived content on external surfaces."""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


async def api_spawn_status(request: web.Request) -> web.Response:
    """GET /api/spawn/{id} — poll subagent status."""
    state: ConsoleState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    agent_id = request.match_info["agent_id"]
    info = state.subagents.get(agent_id)
    if not info:
        try:
            disk_state = read_state(agent_id)
            if disk_state:
                disk_data: dict[str, object] = {
                    "id": agent_id,
                    "task": _redact(disk_state.get("task", "")),
                    "done": True,
                    "started": disk_state.get("started"),
                }
                result_path = _agent_dir(agent_id) / "result.txt"
                result = ""
                if result_path.exists() and not is_sensitive_path(str(result_path)):
                    try:
                        result = await asyncio.to_thread(
                            result_path.read_text, encoding="utf-8", errors="replace"
                        )
                    except OSError:
                        pass
                disk_data["result"] = _redact(result) if result else "_No result._"
                tombstone_path = _agent_dir(agent_id) / "tombstone.json"
                if tombstone_path.exists() and not is_sensitive_path(
                    str(tombstone_path)
                ):
                    try:
                        raw = await asyncio.to_thread(
                            tombstone_path.read_text, encoding="utf-8"
                        )
                        ts = json.loads(raw)
                        disk_data["error"] = _redact(
                            f"Orphaned: {ts.get('cause', 'unknown')}"
                        )
                    except (OSError, ValueError):
                        disk_data["error"] = "Orphaned (unknown cause)"
                else:
                    disk_data["error"] = ""
                return web.json_response(disk_data)
        except Exception:
            logger.debug("Persistence fallback failed for %s", agent_id, exc_info=True)
        return web.json_response({"error": "not found"}, status=404)
    data = {
        "id": info.id,
        "task": _redact(info.task),
        "done": info.done,
    }  # type: dict[str, object]
    data["started"] = info.started
    if info.done:
        result = info.result
        if info.result_path and not is_sensitive_path(info.result_path):
            try:
                result = await asyncio.to_thread(
                    Path(info.result_path).read_text,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                pass
        data["result"] = _redact(result)
        data["error"] = _redact(info.error) if info.error else ""
    else:
        data["turns"] = info.turns
        data["last_tool"] = _redact(info.last_tool)
        data["elapsed"] = round(time.time() - info.started)
    return web.json_response(data)


async def api_spawn_list(request: web.Request) -> web.Response:
    """GET /api/spawn — list all subagents."""
    state: ConsoleState = request.app["state"]
    if not state.subagents:
        return web.json_response({"agents": []})
    agents = []
    for info in state.subagents.all_agents:
        entry: dict[str, object] = {
            "id": info.id,
            "task": _redact(info.task),
            "done": info.done,
            "parent": info.parent_session_key,
            "agent": info.agent,
            "started": info.started,
        }
        if info.done:
            entry["result"] = _redact(info.result)
            entry["error"] = _redact(info.error) if info.error else ""
        else:
            entry["turns"] = info.turns
            entry["last_tool"] = _redact(info.last_tool)
            entry["elapsed"] = round(time.time() - info.started)
        agents.append(entry)
    return web.json_response({"agents": agents})


async def api_spawn_delete(request: web.Request) -> web.Response:
    """DELETE /api/spawn/{agent_id} — cancel a running subagent or remove a finished one."""
    state: ConsoleState = request.app["state"]
    agent_id = request.match_info["agent_id"]
    if not state.subagents or agent_id not in state.subagents._agents:
        return web.json_response({"error": "not found"}, status=404)
    cancelled = await state.subagents.cancel(agent_id)
    if not cancelled:
        state.subagents._agents.pop(agent_id, None)
        state.subagents._tasks.pop(agent_id, None)
    return web.json_response({"ok": True, "cancelled": cancelled})


async def api_spawn_clear(request: web.Request) -> web.Response:
    """DELETE /api/spawn — clear all completed subagents."""
    state: ConsoleState = request.app["state"]
    if not state.subagents:
        return web.json_response({"ok": True})
    done_ids = [a.id for a in state.subagents.all_agents if a.done]
    for aid in done_ids:
        state.subagents._agents.pop(aid, None)
        state.subagents._tasks.pop(aid, None)
    return web.json_response({"ok": True, "cleared": len(done_ids)})


async def api_spawn_cancel_fanout(request: web.Request) -> web.Response:
    """POST /api/spawn/cancel-fanout — kill EVERY child of one parent/run in one
    click (WF2WOR-8 C1.4). Body: ``{parent_session?: str, parent_run?: str}`` — one
    of them keys the fan-out. Unlike DELETE /api/spawn (clears COMPLETED entries
    without killing running ones), this stops the whole in-flight fan-out.
    """
    state: ConsoleState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    fanout_key = (body.get("parent_run") or "").strip()
    if not fanout_key:
        parent_session = (body.get("parent_session") or "").strip()
        if parent_session:
            fanout_key = (
                parent_session
                if parent_session.startswith("dashboard:")
                else f"dashboard:{parent_session}"
            )
    if not fanout_key:
        return web.json_response(
            {"error": "parent_session or parent_run is required"}, status=400
        )
    cancelled = await state.subagents.cancel_fanout(
        fanout_key, reason="cancelled by user"
    )
    return web.json_response({"ok": True, "cancelled": cancelled})


async def api_notifications(request: web.Request) -> web.Response:
    state: ConsoleState = request.app["state"]
    return web.json_response(
        {"notifications": state._notification_log, "unread": state.unread_count()}
    )


async def api_notification_delete(request: web.Request) -> web.Response:
    """DELETE /api/notifications — delete a single notification by timestamp."""
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.delete_notification(ts)
    return web.json_response({"ok": ok})


async def api_notifications_clear(request: web.Request) -> web.Response:
    """POST /api/notifications/clear — clear all notifications."""
    state: ConsoleState = request.app["state"]
    state.clear_notifications()
    return web.json_response({"ok": True})


async def api_notification_ack(request: web.Request) -> web.Response:
    """POST /api/notifications/ack — mark a single notification as read."""
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.ack_notification(ts)
    return web.json_response({"ok": ok})


async def api_notification_unack(request: web.Request) -> web.Response:
    """POST /api/notifications/unack — mark a single notification as unread."""
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.unack_notification(ts)
    return web.json_response({"ok": ok})


async def api_notifications_ack_all(request: web.Request) -> web.Response:
    """POST /api/notifications/ack-all — mark all notifications as read."""
    state: ConsoleState = request.app["state"]
    for n in state._notification_log:
        n["acked"] = True
    _rewrite_notifications(state._notification_log)
    state.broadcast_ws("notification_ack", {"ts": "*"})
    return web.json_response({"ok": True})


_MAX_BLOCKS = 50
_MAX_WALK_DEPTH = 10


def _sanitize_blocks(
    blocks: list[dict],
    *redactors: Any,
) -> list[dict]:
    """Walk Block Kit blocks and sanitize all strings (both keys and values).

    Block Kit structural keys (type, text, mrkdwn, etc.) pass through
    sanitizers unchanged since they don't match hostile patterns.
    """
    from copy import deepcopy  # noqa: F811

    def _redact_str(s: str) -> str:
        for fn in redactors:
            s, _ = fn(s)
        return s

    def _walk(obj: Any, depth: int = 0) -> Any:
        if depth > _MAX_WALK_DEPTH:
            if isinstance(obj, str):
                return _redact_str(obj)
            if isinstance(obj, (dict, list)):
                return {} if isinstance(obj, dict) else []
            return obj
        if isinstance(obj, str):
            return _redact_str(obj)
        if isinstance(obj, dict):
            return {_redact_str(k): _walk(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(item, depth + 1) for item in obj]
        return obj

    return _walk(deepcopy(blocks[:_MAX_BLOCKS]))


def _resolve_session_target(
    state: ConsoleState, target: str, caller_session: str
) -> tuple[str, str] | tuple[None, None]:
    """Resolve a session target to a dashboard session key and job name.

    ``target="origin"`` looks up the cron job that owns *caller_session*
    and returns ``(session_key, job_name)``.
    Returns ``(None, None)`` if the origin session can't be resolved
    (non-"origin" target, non-cron caller, unknown job, or cron with no
    originating session_key — e.g. one created from the dashboard UI).

    Note: ``target="channel"`` is NOT handled here — it is intercepted in
    ``api_send_message`` and converted to an explicit fall-through to the
    channel-delivery path, so it never reaches this resolver.
    """
    if target != "origin":
        return None, None
    if not caller_session.startswith("cron:"):
        return None, None
    cron_id = caller_session.removeprefix("cron:")
    from gideon.automation.triggers import schedule_view as _sv
    from gideon.automation.triggers.store import TriggerStore
    from gideon.core.config.loader import config_dir

    row = TriggerStore(base_dir=config_dir()).get(cron_id)
    if row is None:
        return None, None
    session_key = _sv.session_key_of(row.trigger)
    if not session_key:
        return None, None
    return session_key.removeprefix("dashboard:"), row.trigger.name


def _is_owner_user(owner_id: str, user_id: str) -> bool:
    """Owner-only channel access (multi-user disabled), with W/U prefix cross-match."""
    if not owner_id or not user_id:
        return False
    return (
        user_id == owner_id
        or user_id.replace("W", "U", 1) == owner_id
        or user_id.replace("U", "W", 1) == owner_id
    )


def _is_tracked_channel(state: "ConsoleState", channel_id: str) -> bool:
    """Whether a channel is in the ACTIVE channel app's outbound allowlist.

    The channel app owns its tracked-channel config; core
    consults it through the provider-agnostic ChannelDelivery seam. No channel
    connected → nothing is tracked (deny-by-default)."""
    if not channel_id:
        return False
    delivery = getattr(state, "channel_delivery", None)
    if delivery is None or not hasattr(delivery, "is_tracked_channel"):
        return False
    try:
        return bool(delivery.is_tracked_channel(channel_id))
    except Exception:
        logger.exception("is_tracked_channel failed")
        return False


async def api_send_message(request: web.Request) -> web.Response:
    """POST /api/send-message — deliver a message to the messaging channel and/or dashboard.

    Authorization is channel-agnostic: owner-only user access + a config-backed
    tracked-channel allowlist. No import of any channel app — delivery goes through
    the provider-agnostic ``state.channel_delivery`` (:class:`ChannelDelivery`)."""
    from gideon.assurance.validation import USER_ID_RE  # noqa: F811
    from gideon.assurance.validation import CHANNEL_ID_RE

    _owner = getattr(request.app["state"], "owner_id", "") or ""

    def is_tracked_channel(channel_id: str) -> bool:
        return _is_tracked_channel(request.app["state"], channel_id)

    def is_allowed_user(user_id: str) -> bool:
        return _is_owner_user(_owner, user_id)

    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text required"}, status=400)
    title = body.get("title", "Agent Message")
    blocks = body.get("blocks")
    if blocks and not isinstance(blocks, list):
        return web.json_response({"error": "blocks must be an array"}, status=400)

    target_channel = body.get("channel", "").strip()
    target_user = body.get("user", "").strip()
    unfurl_links = body.get("unfurl_links")
    unfurl_media = body.get("unfurl_media")
    if (unfurl_links is not None and not isinstance(unfurl_links, bool)) or (
        unfurl_media is not None and not isinstance(unfurl_media, bool)
    ):
        return web.json_response(
            {"error": "unfurl_links and unfurl_media must be booleans"}, status=400
        )

    thread_ts = body.get("thread_ts")
    if thread_ts is not None:
        if not isinstance(thread_ts, str) or not re.match(r"^\d+\.\d+$", thread_ts):
            return web.json_response(
                {
                    "error": "thread_ts must be a channel timestamp string like '1712793600.123456'"
                },
                status=400,
            )
    reply_broadcast = body.get("reply_broadcast")
    if reply_broadcast is not None and not isinstance(reply_broadcast, bool):
        return web.json_response(
            {"error": "reply_broadcast must be a boolean"}, status=400
        )
    if reply_broadcast and not thread_ts:
        return web.json_response(
            {"error": "reply_broadcast requires thread_ts"}, status=400
        )

    if target_channel and target_user:
        return web.json_response(
            {"error": "specify channel or user, not both"}, status=400
        )

    if target_channel and not CHANNEL_ID_RE.match(target_channel):
        return web.json_response({"error": "invalid channel ID format"}, status=400)
    if target_user and not USER_ID_RE.match(target_user):
        return web.json_response({"error": "invalid user ID format"}, status=400)

    if target_channel:
        target_channel, _ = redact_exfiltration_urls(target_channel)
        target_channel, _ = redact_credentials(target_channel)
    if target_user:
        target_user, _ = redact_exfiltration_urls(target_user)
        target_user, _ = redact_credentials(target_user)

    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    if blocks:
        blocks = _sanitize_blocks(blocks, redact_exfiltration_urls, redact_credentials)

    if target_channel and not is_tracked_channel(target_channel):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="send_message",
            outcome="denied",
            downstream_service="channel",
            resources=f"target_channel={target_channel}",
        )
        return web.json_response(
            {
                "error": f"channel {target_channel} is not in the channel app's tracked "
                "channels. Add it in the channel app's settings (tracking channels "
                "via /gideon #channel or the app config)."
            },
            status=403,
        )

    if target_user and not is_allowed_user(target_user):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="send_message",
            outcome="denied",
            downstream_service="channel",
            resources=f"target_user={target_user}",
        )
        return web.json_response(
            {"error": "user not in allowlist — add them in the channel app's settings"},
            status=403,
        )

    sent_channel = False
    channel_ts: str | None = None
    sent_session = False
    target_session = body.get("session")
    job_name = None
    channel_attempted = False
    channel_error = ""
    try:
        if target_session == "channel":
            target_session = None
        if target_session:
            session_name, job_name = _resolve_session_target(
                state, target_session, body.get("caller_session", "")
            )
            if session_name:
                session = state.get_session(session_name)
                was_loaded = session is not None
                if session is None:
                    session = _rehydrate_session_from_history(state, session_name)
                logger.info(
                    "send_message session=origin resolved session_name=%s job=%s was_loaded=%s rehydrated=%s",  # noqa: E501
                    session_name,
                    job_name,
                    was_loaded,
                    (session is not None and not was_loaded),
                )
                if session:
                    label = job_name or "cron"
                    label, _ = redact_exfiltration_urls(label)
                    label, _ = redact_credentials(label)
                    wrapped = (
                        f'{CRON_NOTIFY_PREFIX}"{label}"]\n{text}\n{CRON_NOTIFY_END}'
                    )
                    inject_cls = json.dumps({"cronLabel": label})
                    if session.running:
                        if len(session._queue) >= 50:
                            evicted = session.queue_pop(0)
                            logger.warning(
                                "Queue full for session %s — evicting oldest message",
                                session_name,
                            )
                            _remove_queued_by_id(session.messages, evicted["id"])
                        qid = session.queue_append(wrapped)
                        _cls = json.loads(inject_cls)
                        _cls["queue_id"] = qid
                        session.append("queued", wrapped, json.dumps(_cls))
                        state.push_sessions_update()
                    else:
                        from gideon.interfaces.dashboard.chat_runner import run_chat

                        session.append("inject", wrapped, inject_cls)
                        task = asyncio.create_task(run_chat(state, session, wrapped))
                        session.task = task
                        state._background_tasks.add(task)
                        task.add_done_callback(state._background_tasks.discard)
                        state.push_sessions_update()
                    sent_session = True
        if not sent_session:
            if target_session and job_name:
                safe_name, _ = redact_exfiltration_urls(job_name)
                safe_name, _ = redact_credentials(safe_name)
                title = f"⏰ {safe_name}"
                text += "\n\n_(session closed — delivered as notification)_"
            state.notify(notification_kinds.AGENT, title, text)
            if state.channel_delivery:
                try:
                    if target_channel:
                        channel = target_channel
                    elif target_user:
                        channel = await state.channel_delivery.open_dm(target_user)
                    elif state.owner_id:
                        channel = await state.channel_delivery.open_dm(state.owner_id)
                    else:
                        channel = ""

                    if channel:
                        channel_attempted = True
                        if blocks:
                            channel_ts = await state.channel_delivery.deliver_rich(
                                channel,
                                blocks,
                                text,
                                thread_ts=thread_ts,
                                unfurl_links=unfurl_links,
                                unfurl_media=unfurl_media,
                                reply_broadcast=reply_broadcast,
                            )
                        else:
                            channel_ts = await state.channel_delivery.deliver_text(
                                channel,
                                text,
                                thread_ts=thread_ts,
                                unfurl_links=unfurl_links,
                                unfurl_media=unfurl_media,
                                reply_broadcast=reply_broadcast,
                            )
                        sent_channel = True
                except Exception as exc:
                    channel_attempted = True
                    channel_error = str(exc)
                    logger.exception("send_message: channel delivery failed")
    finally:
        try:
            thread_hint = " threaded=1" if thread_ts else ""
            if reply_broadcast:
                thread_hint += " broadcast=1"
            base_res = (
                f"target_channel={target_channel} target_user={target_user}"
                if (target_channel or target_user)
                else ("session=origin" if sent_session else "fallback=owner_dm")
            )
            _sel().log_tool_invocation(
                session_key="dashboard",
                tool_name="send_message",
                outcome=(
                    "completed"
                    if sent_channel or sent_session or not channel_attempted
                    else "error"
                ),
                downstream_service=(
                    "session"
                    if sent_session
                    else ("channel" if sent_channel else "dashboard")
                ),
                resources=base_res + thread_hint,
            )
        except Exception:
            logger.warning("SEL logging failed for send_message", exc_info=True)
    if channel_attempted and not sent_channel:
        safe_error, _ = redact_credentials(channel_error)
        safe_error, _ = redact_exfiltration_urls(safe_error)
        return web.json_response(
            {
                "ok": False,
                "error": f"Channel delivery failed: {safe_error}",
                "channel": False,
            },
            status=502,
        )
    resp_body: dict[str, Any] = {
        "ok": True,
        "channel": sent_channel,
        "session": sent_session,
    }
    if channel_ts:
        resp_body["ts"] = channel_ts
    return web.json_response(resp_body)


async def api_channel_profile(request: web.Request) -> web.Response:
    """POST /api/channel/profile — read a channel user's profile."""
    import time  # noqa: F811

    from gideon.assurance.validation import USER_ID_RE  # noqa: F811

    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    raw_user = body.get("user", "")
    if not isinstance(raw_user, str):
        return web.json_response({"error": "user must be a string"}, status=400)
    user_id = raw_user.strip()
    if not user_id:
        return web.json_response({"error": "user required"}, status=400)
    if not USER_ID_RE.match(user_id):
        return web.json_response({"error": "invalid user ID format"}, status=400)
    user_id, _ = redact_exfiltration_urls(user_id)
    user_id, _ = redact_credentials(user_id)

    if not _is_owner_user(getattr(state, "owner_id", "") or "", user_id):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="denied",
            downstream_service="channel",
            resources=f"user={user_id}",
        )
        return web.json_response({"error": "user not in allowlist"}, status=403)

    if not state.channel_delivery:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="error",
            downstream_service="channel",
            resources=f"user={user_id} reason=channel_not_connected",
        )
        return web.json_response({"error": "Channel not connected"}, status=503)

    now = time.monotonic()
    history: list[float] = getattr(state, "_profile_lookup_times", [])
    history = [t for t in history if now - t < 60]
    if len(history) >= 5:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="denied",
            downstream_service="channel",
            resources=f"user={user_id} reason=rate_limit",
        )
        return web.json_response(
            {"error": "rate limit exceeded — max 5 profile lookups per minute"},
            status=429,
        )
    history.append(now)
    state._profile_lookup_times = history  # type: ignore[attr-defined]

    try:
        profile = await state.channel_delivery.resolve_user_profile(user_id)
    except Exception:
        logger.exception("channel-profile: failed for %s", user_id)
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="error",
            downstream_service="channel",
            resources=f"user={user_id}",
        )
        return web.json_response({"error": "Channel API error"}, status=502)

    for key in list(profile):
        val = profile[key]
        if isinstance(val, str) and key not in ("id",):
            val, _ = redact_exfiltration_urls(val)
            val, _ = redact_credentials(val)
            profile[key] = val

    _sel().log_tool_invocation(
        session_key="dashboard",
        tool_name="read_channel_profile",
        outcome="completed",
        downstream_service="channel",
        resources=f"user={user_id}",
    )
    return web.json_response({"profile": profile})
