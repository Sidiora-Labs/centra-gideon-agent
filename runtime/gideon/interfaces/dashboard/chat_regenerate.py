"""Regenerate, variant switch, and edit-resend endpoints."""

import asyncio
import logging
from datetime import datetime, timezone

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
from gideon.interfaces.dashboard.chat_runner import run_chat
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.sel import sel

logger = logging.getLogger(__name__)

_MAX_VARIANTS = 20
_MAX_REWIND_SNAPSHOTS = 5


async def _persist_history_off_thread(
    state: ConsoleState, session: _ChatSession, label: str
) -> None:
    """Rewrite the session's persisted history off the event loop.

    Snapshots the current message list and writes it on a worker thread so a
    slow disk write never blocks the event loop. Failures are logged but not
    raised — the in-memory session stays authoritative until the next save.
    """
    try:
        msgs_snapshot = list(session.messages)
        await asyncio.to_thread(
            save_session_to_history, state, session, msgs_snapshot, force=True
        )
    except Exception:
        logger.warning("%s: failed to persist session history", label, exc_info=True)


async def api_chat_session_regenerate(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/regenerate — regenerate the last assistant reply."""
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    async with session._lock:
        if session.running:
            return web.json_response({"error": "session is running"}, status=409)

        msgs = session.messages
        anchor_idx = -1
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") in ("assistant", "error"):
                anchor_idx = i
                break
        if anchor_idx < 0:
            return web.json_response(
                {"error": "no assistant message to regenerate"}, status=400
            )
        retrying_failed_turn = msgs[anchor_idx].get("role") == "error"
        u_idx = -1
        for i in range(anchor_idx - 1, -1, -1):
            if msgs[i].get("role") == "user":
                u_idx = i
                break
        if u_idx < 0:
            return web.json_response({"error": "no preceding user message"}, status=400)

        user_msg = msgs[u_idx].get("content", "")
        if not user_msg:
            return web.json_response({"error": "empty user message"}, status=400)

        variants: list[dict] = []
        if not retrying_failed_turn:
            ai_msg = msgs[anchor_idx]
            _rv = ai_msg.get("variants")
            variants = list(_rv) if isinstance(_rv, list) else []  # type: ignore[arg-type]
            current_entry = {
                "content": ai_msg.get("content", ""),
                "ts": ai_msg.get("ts", ""),
            }
            if not any(v.get("content") == current_entry["content"] for v in variants):
                variants.append(current_entry)
            if len(variants) > _MAX_VARIANTS:
                variants = variants[-_MAX_VARIANTS:]

        del session.messages[u_idx + 1 :]
        session._dirty = True
        session._pending_variants = variants

        await _persist_history_off_thread(state, session, "regenerate")

        sel().log_api_access(
            caller="dashboard",
            operation=(
                "chat.retry_failed_turn" if retrying_failed_turn else "chat.regenerate"
            ),
            outcome="allowed",
            source="dashboard",
            resources=session.key,
        )

        if retrying_failed_turn:
            hint = ""
        else:
            hint = (
                "The user regenerated the previous response. Produce a fresh answer — "
                "vary phrasing, structure, or angle. Do not say you already answered or "
                "reference the prior reply."
            )
        task = asyncio.create_task(
            run_chat(state, session, user_msg, regenerate_hint=hint)
        )
        session.task = task
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

        def _clear_pending_on_done(t: asyncio.Task) -> None:
            if session._pending_variants:
                if not t.cancelled() and t.exception() is None:
                    logger.warning(
                        "Regenerate: pending variants not consumed by flush, discarding"
                    )
                session._pending_variants = []

        task.add_done_callback(_clear_pending_on_done)
    state.push_sessions_update()
    return web.json_response({"ok": True})


async def api_chat_session_switch_variant(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/switch-variant — switch which regenerated variant is active."""  # noqa: E501

    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)
    try:
        idx = int(body.get("index"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid index"}, status=400)

    async with session._lock:
        if session.running:
            return web.json_response({"error": "session is running"}, status=409)

        target = None
        for m in reversed(session.messages):
            if m.get("role") == "assistant" and m.get("variants"):
                target = m
                break
        if target is None:
            return web.json_response({"error": "no variants"}, status=400)
        raw_target_variants = target.get("variants")
        variants: list[dict] = (
            list(raw_target_variants)  # type: ignore[arg-type]
            if isinstance(raw_target_variants, list)
            else []
        )
        if idx < 0 or idx >= len(variants):
            return web.json_response({"error": "index out of range"}, status=400)

        chosen = variants[idx]
        if not isinstance(chosen, dict):
            return web.json_response({"error": "corrupt variant entry"}, status=400)
        target_dict: dict = target
        target_dict["content"] = chosen.get("content", "")
        target_dict["ts"] = chosen.get("ts", target_dict.get("ts", ""))
        target_dict["variant_idx"] = idx
        session._dirty = True
        await _persist_history_off_thread(state, session, "switch-variant")
        sel().log_api_access(
            caller="dashboard",
            operation="chat.switch_variant",
            outcome="allowed",
            source="dashboard",
            resources=session.key,
        )
        _bc, _ = redact_exfiltration_urls(target_dict["content"])
        _bc, _ = redact_credentials(_bc)
        state.broadcast_ws(
            "chat_variant_switch",
            {
                "session": session.key,
                "index": idx,
                "count": len(variants),
                "content": _bc,
            },
        )
        return web.json_response({"ok": True, "index": idx})


async def api_chat_session_edit_resend(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/edit-resend — edit a user message and resend."""
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    index = body.get("index")
    ts = body.get("ts")
    client_ts = body.get("client_ts")
    rewind = bool(body.get("rewind"))
    content = (body.get("content") or "").strip()
    if not content:
        return web.json_response({"error": "content is required"}, status=400)

    async with session._lock:
        if session.running:
            return web.json_response({"error": "session is running"}, status=409)

        msgs = session.messages

        target = -1
        if ts:
            target = next(
                (
                    i
                    for i, m in enumerate(msgs)
                    if m.get("ts") == ts and m.get("role") == "user"
                ),
                -1,
            )
        if (
            target < 0
            and isinstance(index, int)
            and 0 <= index < len(msgs)
            and msgs[index].get("role") == "user"
        ):
            target = index
        if target < 0:
            target = next(
                (
                    i
                    for i in range(len(msgs) - 1, -1, -1)
                    if msgs[i].get("role") == "user"
                ),
                -1,
            )
        if target < 0:
            return web.json_response({"error": "no user message to edit"}, status=400)
        index = target

        retained = 0
        carried_rewound: list[dict] = []
        if rewind:
            edited_old = msgs[index]
            _rw = edited_old.get("rewound")
            carried_rewound = list(_rw) if isinstance(_rw, list) else []
            tail = [
                dict(m) for m in msgs[index:] if m.get("role") in ("user", "assistant")
            ]
            retained = max(0, len(tail) - 1)
            if tail:
                carried_rewound.append(
                    {"messages": tail, "ts": datetime.now(tz=timezone.utc).isoformat()}
                )
                if len(carried_rewound) > _MAX_REWIND_SNAPSHOTS:
                    carried_rewound = carried_rewound[-_MAX_REWIND_SNAPSHOTS:]

        del session.messages[index:]
        session._dirty = True

        _bc, _ = redact_exfiltration_urls(content)
        _bc, _ = redact_credentials(_bc)
        _resend_ts = ""
        if isinstance(client_ts, str) and client_ts:
            try:
                datetime.fromisoformat(client_ts)
                _resend_ts = client_ts
            except (ValueError, TypeError):
                _resend_ts = ""
        session.append("user", _bc, "msg msg-u", ts=_resend_ts)
        if rewind and carried_rewound:
            session.messages[-1]["rewound"] = carried_rewound

        await _persist_history_off_thread(state, session, "edit-resend")

        sel().log_api_access(
            caller="dashboard",
            operation="chat.rewind" if rewind else "chat.edit_resend",
            outcome="allowed",
            source="dashboard",
            resources=session.key,
        )

        if rewind:
            from gideon.interfaces.dashboard.chat_utils import _history_key_for

            await state.sessions.reset(_history_key_for(session.key))
            state.broadcast_ws(
                "chat_rewound",
                {"session": session.key, "index": index, "retained": retained},
            )

        task = asyncio.create_task(run_chat(state, session, _bc))
        session.task = task
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

        def _on_done(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception() is not None:
                logger.error(
                    "edit-resend run_chat failed for %s",
                    session.key,
                    exc_info=t.exception(),
                )

        task.add_done_callback(_on_done)

    state.push_sessions_update()
    return web.json_response({"ok": True, "rewound": retained})
