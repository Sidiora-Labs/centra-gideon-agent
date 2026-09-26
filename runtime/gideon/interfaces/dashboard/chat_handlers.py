"""HTTP API handlers for dashboard chat endpoints."""

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from gideon.assurance.validation import _AGENT_NAME_RE
from gideon.automation.loop import files as loop_files
from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.core.config.loader import (
    AppConfig,
    default_workspace_dir,
    resolve_session_workspace,
)
from gideon.core.http_request import read_json_body
from gideon.http_errors import json_error
from gideon.interfaces.dashboard.chat_persistence import (
    _attach_rewound,
    _attach_variants,
    _redact_meta,
    _rehydrate_session_from_history,
    _validate_reasoning_effort,
    purge_session_workspace,
    resolve_session,
    resolve_tool_result_path,
    save_session_to_history,
    session_key_exists,
)
from gideon.interfaces.dashboard.chat_runner import run_chat
from gideon.interfaces.dashboard.chat_utils import (
    _build_stream_chunk,
    _emit_agent_assignment,
    _history_key_for,
    _normalize_model,
    _prepare_messages,
    _redact_for_display,
    _remove_queued_by_id,
    _sync_dashboard_sessions,
    apply_task_mode,
    persisted_history_key,
)
from gideon.interfaces.dashboard.state import (
    ConsoleState,
    _ChatSession,
    _mark_permission_resolved,
)
from gideon.security.security import (
    is_sensitive_path,
    redact_credentials,
    redact_exfiltration_urls,
)
from gideon.security.sel import sel


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)


async def _run_chat_scoped(
    state: ConsoleState,
    session: _ChatSession,
    message: str,
    *,
    persona_snippet: str = "",
) -> None:
    """Run one turn with an inbound turn's SPEND SCOPE bound (EXTERNAL-ACCESS §9.5).

    §9.5 asks that headless CLI turns "ride SpendMeter scope_key=cli". Nothing on the
    chat path bound a run scope at all — ``set_current_run_key`` had exactly one
    production caller (the trigger-fire seam), so every chat turn charged with an empty
    run key and ``run_totals`` for any chat scope was 0.0 by construction.

    Binding happens HERE rather than inside ``run_chat`` because this is a fresh task:
    a ContextVar set in a task dies with it, so the scope cannot leak into the caller's
    context and there is no reset to get wrong in a 2900-line function's teardown. The
    two direct-await callers of ``run_chat`` (the gateway's nudge loop, tests) are
    therefore untouched — they bind no scope, exactly as before.

    An ``inbound:cli:`` session scopes to ``cli``; another ``inbound:`` surface scopes to
    its own surface name, so the HTTP dialects EA-2/EA-5 add are attributable without
    being lumped in with the CLI. A dashboard session binds nothing, keeping every
    interactive turn byte-identical to today.
    """
    from gideon.security.guardrails.policy import INBOUND_PREFIX

    key = session.key or ""
    if not key.startswith(INBOUND_PREFIX):
        if persona_snippet:
            await run_chat(state, session, message, persona_snippet=persona_snippet)
        else:
            await run_chat(state, session, message)
        return

    from gideon.interfaces.cli.run import CLI_RUN_KEY, CLI_SESSION_PREFIX
    from gideon.security.guardrails.budgets import (
        get_meter,
        safety_budget_for_inbound,
        set_current_run_budget,
        set_current_run_key,
    )

    if key.startswith(CLI_SESSION_PREFIX):
        run_key = CLI_RUN_KEY
    else:
        parts = key.split(":")
        run_key = parts[1] if len(parts) > 1 and parts[1] else "inbound"
    set_current_run_key(run_key)
    set_current_run_budget(safety_budget_for_inbound())
    try:
        if persona_snippet:
            await run_chat(state, session, message, persona_snippet=persona_snippet)
        else:
            await run_chat(state, session, message)
    finally:
        try:
            get_meter().end_run(run_key)
        except Exception:  # noqa: BLE001 — bookkeeping must not mask a turn's outcome
            logger.debug("end_run failed for %s", run_key, exc_info=True)


async def api_chat(request: web.Request) -> web.StreamResponse:
    """POST /api/chat — send message to a session, stream response via SSE."""
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_message = body.get("message", "")
    if not isinstance(raw_message, str):
        return json_error(
            "invalid_request", message="message must be a string", status=400
        )
    message = raw_message.strip()
    agent = body.get("agent", "")
    session_name = body.get("session")
    color_theme = body.get("color_theme", "")
    user_meta = body.get("meta")
    if not isinstance(user_meta, dict):
        user_meta = None
    client_ts = ""
    if user_meta:
        _raw_ts = user_meta.pop("client_ts", "")
        if isinstance(_raw_ts, str) and _raw_ts:
            try:
                datetime.fromisoformat(_raw_ts)
                client_ts = _raw_ts
            except (ValueError, TypeError):
                client_ts = ""
        if not user_meta:
            user_meta = None
    if str(body.get("input_origin", "")).strip().lower() == "voice":
        from gideon.integrations.voice.duplex import VOICE_DISCLAIMER

        user_meta = {**(user_meta or {}), "input_origin": "voice"}
        if message and AppConfig.load().voice.voice_disclaimer_enabled:
            if VOICE_DISCLAIMER not in message:
                message = f"{message}\n\n{VOICE_DISCLAIMER}"

    from gideon.interfaces.dashboard.chat_utils import persona_themes

    if not isinstance(color_theme, str) or color_theme not in {"", *persona_themes()}:
        color_theme = ""
    if not isinstance(agent, str) or not (agent == "" or _AGENT_NAME_RE.match(agent)):
        _emit_agent_assignment(
            str(session_name or ""), str(agent), outcome="denied_invalid"
        )
        return web.json_response({"error": "invalid agent name"}, status=400)
    if not isinstance(session_name, str) and session_name is not None:
        session_name = None

    if session_name:
        if not session_key_exists(state, session_name):
            return json_error("session_not_found", status=404)
        _rehydrate_session_from_history(state, session_name, include_archived=True)
    session = state.get_or_create_session(session_name, app=request.get("app", ""))

    request_app = request.get("app", "")
    if request_app:
        if not session._app:
            sel().log_api_access(
                caller=request_app,
                operation="chat_send",
                outcome="denied",
                source="app_isolation",
                resources=f"session={session.key}",
                error="app cannot access unscoped sessions",
            )
            return web.json_response(
                {"error": "app cannot access unscoped sessions"}, status=403
            )
        elif request_app != session._app:
            sel().log_api_access(
                caller=request_app,
                operation="chat_send",
                outcome="denied",
                source="app_isolation",
                resources=f"session={session.key}",
                error="app does not own this session",
            )
            return web.json_response(
                {"error": "app does not own this session"}, status=403
            )

    if session.agent not in (None, ""):
        # Session already has an agent — only reject explicit mismatches (non-empty different agent).  # noqa: E501
        # Empty agent in request means "use existing" (e.g. follow-up messages from frontend).
        if agent and session.agent != agent:
            _emit_agent_assignment(session.key, agent or "", outcome="denied_mismatch")
            return web.json_response({"error": "session agent mismatch"}, status=409)
        else:
            logger.debug("agent match for session=%s agent=%s", session.key, agent)
    elif agent:
        if session.running:
            _emit_agent_assignment(session.key, agent, outcome="denied_running")
            return web.json_response(
                {"error": "cannot set agent on running session"},
                status=409,
            )
        session.agent = agent
        _emit_agent_assignment(session.key, agent)
    else:
        pass

    if "color_theme" in body:
        session.color_theme = color_theme
    if "natural_voice" in body:
        from gideon.integrations.natural_voice import normalize_conversation_choice

        session.natural_voice = normalize_conversation_choice(body.get("natural_voice"))

    if session.running:
        if message:
            _cr = await _maybe_cancel_and_replace(state, session, message)
            if _cr is not None:
                return _cr
        mode = str(body.get("queue_mode") or _default_mid_turn_mode()).strip().lower()
        if (
            message
            and mode == "steer"
            and state.sessions.add_steer(_history_key_for(session.key), message)
        ):
            _c, _ = redact_exfiltration_urls(message)
            _c, _ = redact_credentials(_c)
            state.broadcast_ws(
                "activity_event",
                {
                    "session": session.key,
                    "kind": "status",
                    "text": f"Steering: {_redact_for_display(_c)[:80]}",
                },
            )
            return web.json_response({"ok": True, "steered": True})
        if message:
            qid = session.queue_append(message)
            _c, _ = redact_exfiltration_urls(message)
            _c, _ = redact_credentials(_c)
            _redacted = _redact_for_display(_c)
            state.broadcast_ws(
                "queue_push",
                {
                    "session": session.key,
                    "content": _redacted,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "queue_id": qid,
                },
            )
        return web.json_response({"ok": True, "queued": True})

    if not message:
        return web.json_response({"error": "message is required"}, status=400)

    ws_mode = request.query.get("ws") == "1"

    session._has_reader = not ws_mode
    if user_meta:
        user_meta = _redact_meta(user_meta)
    session.append("user", message, "msg msg-u", ts=client_ts, meta=user_meta)

    try:
        from gideon.automation.triggers.nudge import get_instance as _autonudge_get

        _autonudge = _autonudge_get()
        if _autonudge is not None:
            _autonudge.notify_user_input(session.key)
    except Exception:
        logger.warning("autonudge.notify_user_input failed", exc_info=True)

    try:
        from gideon.automation.triggers.idle_poll import notify_activity
        from gideon.automation.triggers.store import TriggerStore

        notify_activity(
            session_key=session.key, store=TriggerStore(base_dir=config_dir())
        )
    except Exception:
        logger.debug("idle re-arm on user input failed", exc_info=True)

    persona_snippet = f"persona-{session.color_theme}" if session.color_theme else ""
    task = asyncio.create_task(
        _run_chat_scoped(state, session, message, persona_snippet=persona_snippet)
    )
    session.task = task
    session._recovery_retrigger_count = 0
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    state.push_sessions_update()

    session._routing_suggestion = None
    try:
        from gideon.engine.agents.routing import suggest_for_send

        _suggestion = suggest_for_send(state, session, message)
        if _suggestion is not None:
            session._routing_suggestion = {
                "session": session.key,
                "agent": _suggestion.agent,
                "specialty": _suggestion.specialty,
                "score": round(_suggestion.score, 3),
                "method": _suggestion.method,
            }
            state.broadcast_ws(
                "routing_suggestion",
                session._routing_suggestion,
            )
    except Exception:
        logger.debug("routing suggestion hook failed", exc_info=True)

    if ws_mode:
        return web.json_response({"ok": True, "session": session.key})

    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    try:
        while True:
            pending = session.drain()
            for msg in pending:
                if msg["cls"] == "done":
                    await resp.write(b"data: [DONE]\n\n")
                    session._has_reader = False
                    return resp
                chunk = _build_stream_chunk(msg)
                await resp.write(f"data: {chunk}\n\n".encode())
            try:
                await asyncio.wait_for(session.event.wait(), timeout=30)
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, ClientConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        session.drain()
        session._has_reader = False
    return resp


def _default_mid_turn_mode() -> str:
    """The `queue_mode` a mid-turn message gets when the request doesn't state one.

    Derived from `resilience.mid_turn_policy`: policy ``steer`` defaults to steering,
    everything else defaults to queueing. Historically this was hardcoded to
    ``"steer"``, which meant the platform policy had no say over the webui's own
    behavior. An explicit `queue_mode` in the request still wins (owner ruling) —
    this only supplies the default.

    Best-effort: any config failure falls back to ``"queue"``, the safe behavior
    (never drop, never cancel).
    """
    try:
        from gideon.core.config.loader import AppConfig

        return (
            "steer"
            if AppConfig.load().resilience.mid_turn_policy == "steer"
            else "queue"
        )
    except Exception:
        logger.debug("mid_turn_policy read failed; defaulting to queue", exc_info=True)
        return "queue"


async def _maybe_cancel_and_replace(
    state: "ConsoleState", session: "_ChatSession", message: str
) -> "web.Response | None":
    """Cancel-and-replace decision for a follow-up sent mid-turn (PLATFORM-RESILIENCE
    §6.3). Returns a JSON response when it HANDLED the message (cancelled the in-flight
    turn + queued the new one, which the turn-end drain delivers as the next turn), or
    ``None`` to fall through to the normal steer/queue path.

    Eligibility: the resolved mid-turn policy is ``cancel_and_replace`` AND the
    in-flight turn is an interactive origin (webui) AND the per-session debounce window
    has elapsed. Everything is best-effort — any failure returns ``None`` (queue), so a
    broken check never blocks a message.
    """
    import time as _time

    from gideon.core.config.loader import AppConfig
    from gideon.operations.resilience.active_jobs import (
        get_tracker,
        is_cancellable_origin,
    )

    try:
        cfg = AppConfig.load().resilience
        if cfg.mid_turn_policy != "cancel_and_replace":
            return None
        key = session.key
        tracker = get_tracker()
        job = tracker.get(key) or tracker.get(f"dashboard:{key}")
        if job is not None and not is_cancellable_origin(job.origin):
            return None
        now = _time.time()
        if tracker.within_debounce(key, cfg.cancel_replace_min_interval_secs, now=now):
            return None
        tracker.mark_cancel(key, now=now)
    except Exception:
        logger.debug("cancel-and-replace check failed", exc_info=True)
        return None

    try:
        outcome = await state.sessions.stop_turn(
            _history_key_for(session.key), force=False, preserve_queue=True
        )
        qid = session.queue_append(message)
        state.broadcast_ws(
            "chat_done",
            {"session": session.key, "superseded": True, "superseded_by": qid},
        )
        sel().log_tool_invocation(
            session_key=_history_key_for(session.key),
            agent=getattr(session, "agent", "") or "gideon",
            source="dashboard",
            tool_name="mid_turn_cancel_replace",
            tool_kind="command",
            outcome=str(outcome),
            metadata={"session": session.key, "queue_id": qid},
        )
        return web.json_response({"ok": True, "cancelled_and_replaced": True})
    except Exception:
        logger.warning("cancel-and-replace stop_turn failed", exc_info=True)
        return None


_WORKER_PREFIX_ORIGIN = (("loop-", "loop"), ("campaign-", "campaign"))

_LOOP_PLAN_PREFIX = "loop-plan-"


def _origin_of(name: str, app: str = "") -> tuple[str, str]:
    """Classify a session by origin → ``(origin, source_id)``.

    ``origin`` is ``manual`` for a user-initiated chat, else ``loop`` (every unified
    loop kind — general/goal/code/design) or ``campaign``. Prefer the persisted/in-
    memory ``app`` tag when present; fall back to the key prefix (the only signal for
    disk-only worker sessions). The source_id is the originating loop id.
    """
    for prefix, origin in _WORKER_PREFIX_ORIGIN:
        if name.startswith(prefix):
            if origin == "loop":
                if name.startswith(_LOOP_PLAN_PREFIX):
                    return origin, ""
                rest = name[len(prefix) :]
                if loop_files.valid_loop_id(rest):
                    return origin, rest
                head = rest.split("-", 1)[0]
                return origin, (head if loop_files.valid_loop_id(head) else rest)
            return origin, name[len(prefix) :]
    if app in ("loop", "code", "campaign"):
        return "loop" if app == "code" else app, ""
    return "manual", ""


def _origin_label(origin: str, source_id: str) -> str:
    """A friendly name for a worker session's originating loop (any unified kind), for
    the history row's origin chip. Falls back to the id. Best-effort: a missing/failed
    lookup yields the bare id."""
    if origin == "channel":
        return f"Channel · {source_id}" if source_id else "Channel"
    if not source_id:
        return ""
    try:
        if origin == "loop":
            from gideon.automation.loop import store as loop_store

            lp = loop_store.get(source_id)
            return lp.name if lp and lp.name else source_id
    except Exception:
        logger.debug(
            "origin label lookup failed for %s/%s", origin, source_id, exc_info=True
        )
    return source_id


async def api_chat_sessions(request: web.Request) -> web.Response:
    """GET /api/chat/sessions — list all chat sessions.

    Merges in-memory sessions with persisted-on-disk ones so the history list
    survives gateway restarts and ``restore_sessions=false`` (older chats live
    only on disk until opened). In-memory entries win on key collision (they're
    live/authoritative). Non-persistent (incognito/temporary) histories are
    excluded. Worker sessions (goal loops / code projects / campaigns) ARE included
    but tagged with their ``origin`` + ``source_id``/``source_label`` so the UI can
    default-hide them behind a filter and link each back to its cockpit.
    """
    state: ConsoleState = request.app["state"]
    _arch = request.query.get("archived", "")
    _all = request.query.get("all", "")
    want_archived = _arch in ("1", "true", "yes")
    want_all = _all in ("1", "true", "yes")

    def _lifecycle_ok(lifecycle: str) -> bool:
        if want_all:
            return True
        return (lifecycle == "archived") if want_archived else (lifecycle != "archived")

    out: list[dict] = []
    seen: set[str] = set()
    for s in state._sessions.values():
        if getattr(s, "memory_mode", "persistent") in ("incognito", "temporary"):
            seen.add(s.key)
            continue
        d = s.to_dict()
        link_thread = link_channel = None
        try:
            link_thread, link_channel = state.sessions.get_channel_link(s.key)
        except Exception:
            link_thread = link_channel = None
        if link_thread:
            origin, sid = "channel", (link_channel or "")
        else:
            origin, sid = _origin_of(s.key, getattr(s, "_app", "") or "")
        d["origin"] = origin
        if origin != "manual":
            d["source_id"] = sid
            d["source_label"] = _origin_label(origin, sid)
        seen.add(s.key)
        if not _lifecycle_ok(str(d.get("lifecycle") or "active")):
            continue
        out.append(d)

    if state.conversation_log:
        try:
            disk = state.conversation_log.list_sessions()
        except Exception:
            logger.warning("list_sessions failed for chat history merge", exc_info=True)
            disk = []
        for d in disk:
            raw_key = d.get("key", "")
            if raw_key.startswith("dashboard:"):
                name = raw_key.removeprefix("dashboard:")
            elif raw_key.startswith("dashboard_"):
                name = raw_key.removeprefix("dashboard_")
            else:
                name = raw_key
            try:
                link_thread, link_channel = state.sessions.get_channel_link(name)
            except Exception:
                link_thread = link_channel = None
            if (
                not link_thread
                and raw_key == name
                and not raw_key.startswith(("dashboard:", "dashboard_"))
            ):
                continue
            if name in seen:
                continue
            meta = state.conversation_log.get_metadata(raw_key)
            if meta.get("closed"):
                continue
            if meta.get("memory_mode") in ("incognito", "temporary"):
                continue
            seen.add(name)
            if link_thread:
                origin, sid = "channel", (link_channel or "")
            else:
                origin, sid = _origin_of(name, meta.get("app", "") or "")
            row = {
                "key": name,
                "title": d.get("title") or name,
                "agent": meta.get("agent", d.get("agent", "")),
                "model": meta.get("model", ""),
                "messages": d.get("messages", 0),
                "running": False,
                "created": meta.get("created_at") or d.get("created", ""),
                "last_ts": "",
                "last_activity_ts": (
                    datetime.fromtimestamp(d["modified"], tz=timezone.utc).isoformat()
                    if d.get("modified")
                    else ""
                ),
                "folder_id": meta.get("folder_id", ""),
                "pinned": bool(meta.get("pinned")),
                "tags": [t for t in meta.get("tags", []) if isinstance(t, str)],
                "color_index": meta.get("color_index"),
                "memory_mode": meta.get("memory_mode", "persistent"),
                "origin": origin,
                "lifecycle": (
                    meta["lifecycle"]
                    if meta.get("lifecycle") in ("active", "archived")
                    else "active"
                ),
                "last_activity_at": (
                    float(meta["last_activity_at"])
                    if isinstance(meta.get("last_activity_at"), (int, float))
                    else 0.0
                ),
                "never_archive": bool(meta.get("never_archive")),
            }
            if origin != "manual":
                row["source_id"] = sid
                row["source_label"] = _origin_label(origin, sid)
            if not _lifecycle_ok(str(row.get("lifecycle") or "active")):
                continue
            out.append(row)

    return web.json_response(out)


async def api_chat_tool_result(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/tool-result/{rid} — the FULL raw output of
    a projected tool result (tool-output-projection raw store), for the chat
    card's "Show full result" affordance. Optional ?grep= / ?start= / ?end= to
    pull a slice. Read-only; redacted the same way the live output was."""
    from gideon.integrations.tool_providers import result_store
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    name = _history_key_for(request.match_info["session"])
    rid = request.match_info["rid"]
    result_path = resolve_tool_result_path(name, rid)
    if result_path is None or not result_path.is_file():
        return web.json_response(
            {"error": f"no stored result {rid!r} (it may have expired)"}, status=404
        )
    grep = request.query.get("grep") or None
    try:
        start = int(request.query.get("start") or 0)
    except ValueError:
        start = 0
    end_raw = request.query.get("end")
    end = int(end_raw) if (end_raw and end_raw.isdigit()) else None
    res = result_store.fetch_slice(
        name, rid, start=start, end=end, grep=grep, max_chars=200_000
    )
    if not res.get("ok"):
        return web.json_response({"error": res.get("error", "not found")}, status=404)
    content, _ = redact_exfiltration_urls(res.get("content", ""))
    content, _ = redact_credentials(content)
    res["content"] = content
    return web.json_response(res)


async def api_chat_session_bound_project(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/bound-project — the CALLING session's bound Project id.

    ACP-AGENT-PARITY §2.6 gap 10. An ACP CLI's tools run in a separate ``mcp-core``
    process, where the native runtime's per-turn project contextvar is empty by
    construction, so ``artifact_save`` there stamped nothing. The session key already
    crosses to that process, so this endpoint closes the loop with no protocol change.

    Keyed off the ``X-Session-Key`` header, never off a path segment or a query
    parameter: the caller must prove which session it IS, and letting it name any
    session would turn a stamping helper into a cross-session read of someone else's
    project binding.

    Returns ``{"project_id": ""}`` — a 200, not a 404 — when the header is absent, the
    session is unknown or the session binds no project. All three mean the same thing to
    the one caller ("nothing to stamp"), and an error status would make a normal
    unscoped save look like a failure in its logs. Deliberately does NOT fall back to
    the Personal default the way ``/api/context`` does: filing an unscoped save under a
    project the user never chose is worse than an unstamped artifact.
    """
    state: ConsoleState = request.app["state"]
    sk = request.headers.get("X-Session-Key", "")
    if not sk or sk == "dashboard:ui":
        return web.json_response({"project_id": ""})
    name = sk.split(":", 1)[-1] if ":" in sk else sk
    session = (getattr(state, "_sessions", {}) or {}).get(name)
    return web.json_response(
        {"project_id": str(getattr(session, "project_id", "") or "") if session else ""}
    )


async def api_chat_session_detail(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session} — message history for a session.

    Two query modes:
      - Default (no ``limit`` and no ``before``): return the full chained history
        from disk across gateway restarts.
      - Paginated (``limit`` and/or ``before``): ``limit`` caps the number of
        messages returned; ``before`` returns messages before that index.
    """
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        session = _rehydrate_session_from_history(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    resolved_key = persisted_history_key(state.conversation_log, session.key)

    limit_raw = request.query.get("limit")
    before = request.query.get("before")

    if limit_raw is None and before is None:
        mem_msgs = list(session.messages)
        if session._disk_older_count > 0 and state.conversation_log:
            history_key = resolved_key
            try:
                disk_msgs = state.conversation_log.read_messages_chained(history_key)
            except Exception:
                logger.warning(
                    "read_messages_chained failed for %s", history_key, exc_info=True
                )
                disk_msgs = []
            older = disk_msgs[: session._disk_older_count] if disk_msgs else []
            messages = older + mem_msgs
        else:
            messages = mem_msgs
        total = len(messages)
        has_more = False
    else:
        limit = min(int(limit_raw or "200"), 500)
        history_key = resolved_key
        try:
            all_msgs = (
                state.conversation_log.read_messages_chained(history_key)
                if state.conversation_log
                else []
            )
        except Exception:
            logger.warning(
                "read_messages_chained failed for %s", history_key, exc_info=True
            )
            all_msgs = []
        mem_len = len(session.messages)
        disk_len = len(all_msgs)
        current_session_disk = max(0, disk_len - session._disk_older_count)
        unflushed = mem_len - current_session_disk
        if unflushed > 0:
            all_msgs = list(all_msgs) + list(session.messages[-unflushed:])
        total = len(all_msgs)
        if before is not None:
            end = max(0, min(int(before), total))
        else:
            end = total
        start = max(0, end - limit)
        messages = all_msgs[start:end]
        has_more = start > 0

    prepared = _prepare_messages(messages, session.running)

    forked_from = getattr(session, "forked_from", "") or ""
    forked_from_title = ""
    if forked_from:
        parent_key = forked_from.removeprefix("dashboard:")
        parent = state._sessions.get(parent_key)
        if parent is not None:
            forked_from_title = (parent.title if parent._titled else "") or parent_key
        elif state.conversation_log:
            try:
                parent_meta = state.conversation_log.get_metadata(forked_from)
            except Exception:
                parent_meta = {}
            if parent_meta:
                forked_from_title = str(parent_meta.get("title") or "") or parent_key

    return web.json_response(
        {
            "key": session.key,
            "title": session.title,
            "running": session.running,
            "stopping": session._stopping,
            "messages": prepared,
            "queue": [
                {"id": q["id"], "content": _redact_for_display(q["content"])}
                for q in session._queue
            ],
            "total": total,
            "has_more": has_more,
            "agent": session.agent or "",
            "model": session.model or "",
            "mode": getattr(session, "mode", "") or "",
            "acp_provider": getattr(session, "acp_provider", "") or "",
            "acp_provider_agent": getattr(session, "acp_provider_agent", "") or "",
            "reasoning_effort": getattr(session, "reasoning_effort", "") or "",
            "task_mode": getattr(session, "_task_mode", "agent") or "agent",
            "investigate": (
                {
                    "kind": str(
                        (getattr(session, "_investigate_ctx", None) or {}).get(
                            "kind", ""
                        )
                    ),
                    "title": str(
                        (getattr(session, "_investigate_ctx", None) or {}).get(
                            "title", ""
                        )
                    ),
                    "back_link": str(
                        (getattr(session, "_investigate_ctx", None) or {}).get(
                            "back_link", ""
                        )
                    ),
                }
                if isinstance(getattr(session, "_investigate_ctx", None), dict)
                else None
            ),
            "approval": (
                "yolo"
                if state.is_yolo_active()
                else (
                    "trust"
                    if session._trust
                    else "trust_reads" if session._trust_reads else "normal"
                )
            ),
            "memory_mode": getattr(session, "memory_mode", "persistent")
            or "persistent",
            **_natural_voice_payload(session),
            "forked_from": forked_from,
            "forked_from_title": forked_from_title,
            "pending_approval": any(
                not f.done() for f in session._approval_futures.values()
            ),
            "side": (
                session._side.to_dict()
                if session._side is not None and session._side.messages
                else None
            ),
        }
    )


async def api_chat_session_create(request: web.Request) -> web.Response:
    """POST /api/chat/sessions — create a new chat session."""
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        return web.json_response({"error": "name must be a string"}, status=400)
    agent = body.get("agent", "")
    model = body.get("model", "")
    project_id = str(body.get("project_id", "") or "")

    workspace_dir = ""
    try:
        cfg = AppConfig.load()
        if agent and agent in cfg.agents:
            workspace_dir = resolve_session_workspace(cfg, agent)
    except Exception:
        logger.warning("Failed to resolve bindings for session create", exc_info=True)
    if project_id:
        try:
            from gideon.engine.tasks.hierarchy import HierarchyStore

            proj = HierarchyStore().get_project(project_id)
            pdir = str(getattr(proj, "workspace_dir", "") or "") if proj else ""
            if pdir:
                workspace_dir = pdir
        except Exception:
            logger.debug(
                "project workspace resolve failed for %s", project_id, exc_info=True
            )

    try:
        memory_mode = body.get("memory_mode", "persistent")
        if memory_mode not in ("persistent", "incognito", "temporary"):
            return web.json_response({"error": "invalid memory_mode"}, status=400)
        session = state.get_or_create_session(
            name,
            agent=agent,
            workspace_dir=workspace_dir,
            model=model,
            mode=body.get("mode", ""),
            memory_mode=memory_mode,
            ephemeral=body.get("ephemeral"),
            app=request.get("app", ""),
            project_id=project_id,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    if session.is_restricted:
        logger.info(
            "Session %s created with memory_mode=%s", session.key, session.memory_mode
        )
    if project_id:
        try:
            from gideon.engine.tasks.hierarchy import HierarchyStore

            ctx = str(HierarchyStore().context_dir(project_id))
            if ctx and ctx not in (session._extra_tool_roots or []):
                session._extra_tool_roots = [*(session._extra_tool_roots or []), ctx]
        except Exception:
            logger.debug(
                "project context-dir tool-root grant failed for %s",
                project_id,
                exc_info=True,
            )
    if not session.workspace_dir:
        session.workspace_dir = default_workspace_dir()
    _sync_dashboard_sessions(state)
    return web.json_response({**session.to_dict(), **_natural_voice_payload(session)})


def _stop_reach_report(session: _ChatSession) -> dict:
    """What the stop actually REACHED, off the provider's cancel scope (PR2-12).

    The stop card used to say only "stopped", which is a claim about the button rather
    than about the work. This is the evidence behind it — whether the model request was
    aborted, how many child processes were reaped, how many queued tool calls were
    dropped, how many subagents were stopped — and it is the shape PR2-13 consumes.

    ``getattr`` because only a provider that OWNS a turn's cancellation can report on
    it: an ACP-backed session delegates the turn to an external agent process, so it
    has no scope and legitimately reports nothing. Absent → ``{}``, never a guess.
    """
    reporter = getattr(getattr(session, "provider", None), "last_stop_report", None)
    if reporter is None:
        return {}
    try:
        report = reporter()
    except Exception:
        logger.debug("stop report unavailable", exc_info=True)
        return {}
    return report if isinstance(report, dict) else {}


def _resolve_stop_event(session: _ChatSession, outcome: str) -> None:
    """Update the in-flight stop_event message in place with final state."""
    stop_id = session._stop_event_id
    logger.debug("_resolve_stop_event: outcome=%s stop_id=%r", outcome, stop_id)
    if not stop_id:
        return
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    final_state = "stopped" if outcome == "soft" else "stop_failed_reset"
    reached = _stop_reach_report(session)
    found = False
    for msg in reversed(session.messages):
        cls_val = msg.get("cls", "")
        if not cls_val:
            continue
        try:
            cls_data = json.loads(cls_val) if isinstance(cls_val, str) else None
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(cls_data, dict) or cls_data.get("kind") != "stop_event":
            continue
        if cls_data.get("id") != stop_id:
            continue
        cls_data["state"] = final_state
        cls_data["outcome"] = outcome
        cls_data["ts_end"] = now_ts
        if reached:
            cls_data["reached"] = reached
        serialized = json.dumps(cls_data)
        msg["cls"] = serialized
        msg["content"] = serialized
        session._dirty = True
        found = True
        on_msg = getattr(session, "_on_message", None)
        if on_msg:
            try:
                on_msg(session.key, msg)
            except Exception:
                logger.debug("stop_event re-broadcast failed", exc_info=True)
        break
    if not found:
        logger.debug("_resolve_stop_event: no matching message for stop_id=%s", stop_id)
    session._stop_event_id = None


async def api_chat_session_stop(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/stop — cooperative stop with kill fallback.

    First press: soft cancel (cooperative). Second press (?force=true):
    hard kill. Inserts a stop_event message into the session transcript.
    """
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    force = request.query.get("force", "").lower() == "true"

    if session._stop_state == "soft_pending" and force:
        session._stop_state = "killing"
        state.push_sessions_update()
        logger.info("Stop (force): hard-killing session for session %s", name)

        async def _on_hard_force() -> None:
            if session._stop_state != "killing":
                return
            _resolve_stop_event(session, "hard")
            session._stop_state = "idle"
            state.push_sessions_update()

        await state.sessions.stop_turn(
            _history_key_for(name), force=True, on_hard=_on_hard_force
        )
        sel().log_tool_invocation(
            session_key=_history_key_for(name),
            agent=getattr(session, "agent", "") or "gideon",
            source="dashboard",
            tool_name="dashboard_stop",
            tool_kind="command",
            outcome="hard",
            metadata={"session": name, "force": True},
        )
        return web.json_response({"ok": True})

    if session._stop_state != "idle" or not session.running:
        if not session.running:
            logger.info("Stop: session %s not running, ignoring", name)
        return web.json_response({"ok": True})

    session._stop_state = "soft_pending"
    session._queue.clear()

    stop_id = f"stop-{uuid.uuid4().hex}"
    session._stop_event_id = stop_id
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    stop_data = {
        "kind": "stop_event",
        "id": stop_id,
        "state": "stopping",
        "outcome": None,
        "ts_start": now_ts,
    }
    stop_msg = json.dumps(stop_data)
    session.append("system", stop_msg, stop_msg)
    state.push_sessions_update()
    logger.info(
        "Stop: cooperative cancel for session %s (queue=%d)", name, len(session._queue)
    )

    async def _on_soft() -> None:
        logger.debug(
            "_on_soft called: stop_state=%r stop_event_id=%r",
            session._stop_state,
            session._stop_event_id,
        )
        if session._stop_state != "soft_pending":
            logger.debug("_on_soft: state not soft_pending, bail")
            return
        _resolve_stop_event(session, "soft")
        session._stop_state = "idle"
        state.push_sessions_update()

    async def _on_hard() -> None:
        logger.debug("_on_hard called: stop_state=%r", session._stop_state)
        if session._stop_state not in ("soft_pending", "killing"):
            logger.debug("_on_hard: state not soft_pending/killing, bail")
            return
        _resolve_stop_event(session, "hard")
        session._stop_state = "idle"
        state.push_sessions_update()

    outcome = await state.sessions.stop_turn(
        _history_key_for(name), force=False, on_soft=_on_soft, on_hard=_on_hard
    )
    sel().log_tool_invocation(
        session_key=_history_key_for(name),
        agent=getattr(session, "agent", "") or "gideon",
        source="dashboard",
        tool_name="dashboard_stop",
        tool_kind="command",
        outcome=outcome,
        metadata={"session": name, "force": False},
    )
    return web.json_response({"ok": True})


async def api_chat_screen_state(request: web.Request) -> web.Response:
    """GET /api/chat/screen-frame?session=<id> — can this session share its screen?

    Returns ``{enabled, delivery, reason, staged}``. The composer reads this to
    decide whether to offer the share control at all (``enabled``) and, when the
    bound model can't be given the frame in any form, to render the control
    disabled carrying ``reason`` — which is composed server-side so the UI can't
    drift into its own explanation of a decision it doesn't make.
    """
    from gideon.interfaces.dashboard import screen_context

    state: ConsoleState = request.app["state"]
    name = str(request.query.get("session") or "")
    session = state._sessions.get(name)
    enabled = bool(AppConfig.load().dashboard.screen_share_enabled)
    model_label = getattr(session, "model", "") or "" if session else ""
    delivery, reason = screen_context.resolve_delivery(model_label)
    return web.json_response(
        {
            "enabled": enabled,
            "delivery": delivery,
            "reason": reason,
            "staged": bool(session and screen_context.pending(session.key)),
        }
    )


async def api_chat_screen_frame(request: web.Request) -> web.Response:
    """POST /api/chat/screen-frame — stage one screen frame for the next chat turn.

    MULTIMODAL-IO §5.3. Body: ``{session, action, frame_b64}`` where ``action`` is
    one of:

    * ``start`` — the user just picked a screen/window in the browser's share
      dialog. Audited, and clears any stale slot so a share always begins blank.
    * ``frame`` (the default) — stage ``frame_b64`` for the next turn, REPLACING
      any frame already staged (latest-wins, §5.4).
    * ``stop`` — sharing ended (chip, browser stop button, or session close).
      Audited, and drops the slot immediately rather than waiting for a drain.

    **The config gate is enforced HERE, not only in the UI.** ``screen_share_enabled``
    is read per request and a frame is refused with 403 when it is off, so a client
    that kept a stale bundle, forged the call by hand, or simply had the toggle
    flipped off underneath it cannot stage anything. The hidden button is a
    convenience; this check is the control.
    """
    from gideon.interfaces.dashboard import screen_context

    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    name = str(body.get("session") or "")
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    session_key = session.key

    request_app = request.get("app", "")
    if request_app:
        sel().log_api_access(
            caller=request_app,
            operation="chat.screen_frame",
            outcome="denied",
            source="screen_share",
            resources=f"session={session_key}",
            error="screen frames are dashboard-only",
        )
        return web.json_response(
            {"error": "screen frames are dashboard-only"}, status=403
        )

    action = str(body.get("action") or "frame").strip().lower()
    if action not in ("start", "frame", "stop"):
        return web.json_response(
            {"error": "action must be start, frame or stop"}, status=400
        )

    if action == "stop":
        screen_context.clear(session_key)
        sel().log_api_access(
            caller="dashboard",
            operation="chat.screen_share_stop",
            outcome="success",
            source="screen_share",
            resources=f"session={session_key}",
        )
        return web.json_response({"ok": True, "sharing": False})

    if not AppConfig.load().dashboard.screen_share_enabled:
        screen_context.clear(session_key)
        sel().log_api_access(
            caller="dashboard",
            operation=f"chat.screen_{'share_start' if action == 'start' else 'frame'}",
            outcome="denied",
            source="screen_share",
            resources=f"session={session_key}",
            error="dashboard.screen_share_enabled is off",
        )
        return web.json_response(
            {
                "error": "Screen sharing is off. Turn it on in Settings → Chat.",
                "code": "screen_share_disabled",
            },
            status=403,
        )

    if action == "start":
        screen_context.clear(session_key)
        sel().log_api_access(
            caller="dashboard",
            operation="chat.screen_share_start",
            outcome="success",
            source="screen_share",
            resources=f"session={session_key}",
        )
        return web.json_response({"ok": True, "sharing": True})

    try:
        frame = screen_context.parse_frame(str(body.get("frame_b64") or ""))
    except screen_context.FrameRejected as exc:
        sel().log_api_access(
            caller="dashboard",
            operation="chat.screen_frame",
            outcome="denied",
            source="screen_share",
            resources=f"session={session_key}",
            error=str(exc),
        )
        return web.json_response({"error": str(exc)}, status=400)

    screen_context.stage(session_key, frame)
    sel().log_api_access(
        caller="dashboard",
        operation="chat.screen_frame",
        outcome="success",
        source="screen_share",
        resources=f"session={session_key}:{frame.media_type}:{frame.byte_len}b",
    )
    return web.json_response({"ok": True, "staged": True})


async def api_chat_screen_frame_pin(request: web.Request) -> web.Response:
    """POST /api/chat/screen-frame/pin — promote one frame to an ordinary attachment.

    MULTIMODAL-IO §5.4. Pinning is the ONLY way a screen frame becomes a file, and it
    is deliberately a separate verb from sharing: sharing is a read, pinning is a
    write. The bytes come from the CLIENT rather than from a server-side slot,
    because there is no server-side slot to take them from once a turn has drained it
    — which is the ephemerality guarantee working as designed, not a limitation.

    Once written, the frame is an ordinary upload: the uploads dir, the same
    sanitized-name + random-prefix + 0600 treatment, and the same content extraction
    every attachment gets. Sending it on to the knowledge library is then the user's
    normal explicit ingest action; nothing here touches knowledge.db or memory.db.

    Refused in incognito/temporary sessions — "writes suppressed" is the whole
    contract of those modes, and a pinned screenshot is a write.
    """
    import mimetypes as _mt
    import re
    import uuid as _uuid

    from gideon.interfaces.dashboard import screen_context
    from gideon.interfaces.dashboard.attachment_extract import get_extractor
    from gideon.interfaces.dashboard.handlers.files import _upload_dir
    from gideon.workspace.uploads.policy import check_upload

    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    name = str(body.get("session") or "")
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    if request.get("app", ""):
        return web.json_response(
            {"error": "screen frames are dashboard-only"}, status=403
        )

    if not AppConfig.load().dashboard.screen_share_enabled:
        sel().log_api_access(
            caller="dashboard",
            operation="chat.screen_frame_pin",
            outcome="denied",
            source="screen_share",
            resources=f"session={session.key}",
            error="dashboard.screen_share_enabled is off",
        )
        return web.json_response(
            {
                "error": "Screen sharing is off. Turn it on in Settings → Chat.",
                "code": "screen_share_disabled",
            },
            status=403,
        )

    if session.is_restricted:
        sel().log_api_access(
            caller="dashboard",
            operation="chat.screen_frame_pin",
            outcome="denied",
            source="screen_share",
            resources=f"session={session.key}:{session.memory_mode}",
            error="writes are suppressed in this session",
        )
        return web.json_response(
            {
                "error": "This chat is temporary — pinning a frame would write it to disk.",
                "code": "session_restricted",
            },
            status=409,
        )

    try:
        frame = screen_context.parse_frame(str(body.get("frame_b64") or ""))
    except screen_context.FrameRejected as exc:
        return web.json_response({"error": str(exc)}, status=400)

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[
        frame.media_type
    ]
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"screen-{stamp}{ext}"
    raw = base64.b64decode(frame.b64)
    check = check_upload(filename, frame.media_type, size=len(raw))
    if not check.ok:
        return web.json_response({"error": check.reason}, status=check.status)

    _upload_dir().mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]", "_", filename)
    dest = _upload_dir() / f"{_uuid.uuid4().hex}_{safe}"
    dest.write_bytes(raw)
    os.chmod(dest, 0o600)
    try:
        get_extractor().start(
            str(dest), frame.media_type or _mt.guess_type(str(dest))[0]
        )
    except Exception:
        logger.debug("pinned-frame extract kickoff failed", exc_info=True)

    sel().log_api_access(
        caller="dashboard",
        operation="chat.screen_frame_pin",
        outcome="success",
        source="screen_share",
        resources=f"session={session.key}:{frame.media_type}:{frame.byte_len}b",
    )
    return web.json_response({"ok": True, "path": str(dest), "name": filename})


async def api_chat_session_interrupt(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/interrupt — stop the turn, KEEP the queue.

    Unlike /stop (which clears the queue), /interrupt soft-cancels the current
    turn and preserves the queue so the run_chat finally-block dequeue picks up
    the next queued message immediately. Optional body ``{"queue_id": ...}``
    promotes a specific queued message to the front first.

    Preconditions: the session must be running (else ``{ok, info}``) and the
    queue must be non-empty (else 400 — with nothing queued, /interrupt is just
    /stop, so the two verbs stay distinct).
    """
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    if not session.running:
        return web.json_response({"ok": True, "info": "not running"})
    if not session._queue:
        return web.json_response(
            {"error": "queue empty, use /stop instead"}, status=400
        )
    if session._stop_state != "idle":
        return web.json_response({"ok": True, "info": "already stopping"})

    body = {}
    if request.body_exists:
        try:
            body = await read_json_body(request)
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "body must be a JSON object"}, status=400
            )
    queue_id = body.get("queue_id")
    if queue_id:
        if not session.queue_promote(str(queue_id)):
            return web.json_response({"error": "queue_id not found"}, status=404)
        state.broadcast_ws(
            "queue_promoted", {"session": name, "queue_id": str(queue_id)}
        )

    session._stop_state = "soft_pending"

    stop_id = f"stop-{uuid.uuid4().hex}"
    session._stop_event_id = stop_id
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    stop_data = {
        "kind": "stop_event",
        "id": stop_id,
        "state": "interrupting",
        "outcome": None,
        "ts_start": now_ts,
    }
    stop_msg = json.dumps(stop_data)
    session.append("system", stop_msg, stop_msg)
    state.push_sessions_update()
    logger.info(
        "Interrupt: cooperative cancel for session %s (queue=%d preserved)",
        name,
        len(session._queue),
    )

    async def _on_soft() -> None:
        if session._stop_state != "soft_pending":
            return
        _resolve_stop_event(session, "soft")
        session._stop_state = "idle"
        state.push_sessions_update()

    async def _on_hard() -> None:
        if session._stop_state not in ("soft_pending", "killing"):
            return
        _resolve_stop_event(session, "hard")
        session._stop_state = "idle"
        state.push_sessions_update()

    outcome = await state.sessions.stop_turn(
        _history_key_for(name),
        force=False,
        preserve_queue=True,
        on_soft=_on_soft,
        on_hard=_on_hard,
    )
    sel().log_tool_invocation(
        session_key=_history_key_for(name),
        agent=getattr(session, "agent", "") or "gideon",
        source="dashboard",
        tool_name="dashboard_interrupt",
        tool_kind="command",
        outcome=outcome,
        metadata={"session": name, "queue_len": len(session._queue)},
    )
    return web.json_response({"ok": True})


async def api_chat_session_queue_cancel(request: web.Request) -> web.Response:
    """DELETE /api/chat/sessions/{session}/queue/{queue_id} — cancel a queued message.

    Removes the message from the backend queue and broadcasts a
    ``queue_cancel`` WebSocket event so the frontend can move the
    text back to the input box.
    """
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    queue_id = request.match_info["queue_id"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    content = session.queue_remove_by_id(queue_id)
    if content is None:
        return web.json_response({"error": "queue item not found"}, status=404)
    _remove_queued_by_id(session.messages, queue_id)
    _redacted = _redact_for_display(content)
    state.broadcast_ws(
        "queue_cancel", {"session": name, "queue_id": queue_id, "content": _redacted}
    )
    state.push_sessions_update()
    sel().log_tool_invocation(
        session_key=f"dashboard:{name}",
        agent="gideon",
        source="dashboard",
        tool_name="queue_cancel",
        tool_kind="permission",
        outcome="allowed",
        metadata={"queue_id": queue_id, "session": name},
    )
    return web.json_response({"ok": True, "content": _redacted})


async def api_chat_session_delete(request: web.Request) -> web.Response:
    """DELETE /api/chat/sessions/{session} — stop and remove a UI session.

    Kills the per-tab ACP agent session and saves history.  The session
    will be recreated from the warm pool if the tab is resumed later.
    """
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    history_key = persisted_history_key(state.conversation_log, name)
    on_disk = False
    if not session and state.conversation_log:
        try:
            on_disk = bool(
                state.conversation_log.get_metadata(history_key)
            ) or state.conversation_log.has_log(history_key)
        except Exception:
            on_disk = False
    if not session and not on_disk:
        return web.json_response({"error": "not found"}, status=404)

    request_app = request.get("app", "")
    if request_app and session is not None:
        if session._app != request_app:
            sel().log_api_access(
                caller=request_app,
                operation="session_delete",
                outcome="denied",
                source="app_isolation",
                resources=f"session={name}",
                error="app does not own this session",
            )
            return web.json_response(
                {"error": "app does not own this session"}, status=403
            )
        if not session._app:
            sel().log_api_access(
                caller=request_app,
                operation="session_delete",
                outcome="denied",
                source="app_isolation",
                resources=f"session={name}",
                error="app cannot delete unscoped sessions",
            )
            return web.json_response(
                {"error": "app cannot delete unscoped sessions"}, status=403
            )
    elif request_app and session is None:
        return web.json_response(
            {"error": "app cannot delete unscoped sessions"}, status=403
        )

    state._sessions.pop(name, None)
    if session is not None and session.running and session.task is not None:
        session.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(session.task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    try:
        if state.conversation_log:
            state.conversation_log.delete_session(history_key)
    except Exception:
        logger.warning(
            "hard-delete: history file removal failed for %s", name, exc_info=True
        )
    for _sid in {history_key, name}:
        if not purge_session_workspace(_sid):
            logger.debug("hard-delete: no workspace removed for %s", _sid)
    try:
        from gideon.engine import turn_checkpoints

        keys = {history_key, name}
        if session is not None:
            keys.add(session.key)
        for _sid in keys:
            turn_checkpoints.prune_session(_sid)
    except Exception:
        logger.warning(
            "hard-delete: checkpoint purge failed for %s", name, exc_info=True
        )
    state._restricted_keys.discard(f"dashboard:{name}")
    await state.sessions.remove(history_key)
    _sync_dashboard_sessions(state)
    state.push_sessions_update()
    state.push_refresh("history")
    return web.json_response({"ok": True})


async def api_chat_sessions_cleanup(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/cleanup — bulk-archive inactive sessions to history.

    Body: ``{"max_inactive_days": 3, "active_session": "chat-1-123"}``
    Skips the active session and pinned sessions.
    """
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        body = {}
    max_days = 3
    try:
        max_days = max(1, int(body.get("max_inactive_days", 3)))
    except (ValueError, TypeError):
        pass
    active_session = body.get("active_session", "")
    dry_run = body.get("dry_run", False)
    request_app = request.get("app", "")
    cutoff = time.time() - max_days * 86400
    stale_keys: list[str] = []
    active_is_stale = False
    for name in list(state._sessions):
        session = state._sessions.get(name)
        if session is None or session.pinned:
            continue
        if request_app:
            if session._app != request_app:
                continue
        last_activity = 0.0
        if session.messages:
            for m in reversed(session.messages):
                ts = m.get("ts", "")
                if not ts:
                    continue
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    last_activity = dt.timestamp()
                except (ValueError, TypeError):
                    continue
                break
        if not last_activity:
            try:
                dt = datetime.fromisoformat(session.created_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                last_activity = dt.timestamp()
            except Exception:
                last_activity = 0.0
        if not last_activity:
            continue
        if last_activity >= cutoff:
            continue
        if name == active_session:
            active_is_stale = True
            continue
        stale_keys.append(name)
    if dry_run:
        sel().log_api_access(
            caller="dashboard",
            operation="chat.cleanup_dry_run",
            outcome="allowed",
            source="dashboard",
            resources=f"count={len(stale_keys)} threshold={max_days}d",
        )
        return web.json_response(
            {
                "ok": True,
                "dry_run": True,
                "keys": stale_keys,
                "count": len(stale_keys),
                "active_is_stale": active_is_stale,
            }
        )
    archived: list[str] = []
    failed: list[str] = []
    _tasks_to_cancel: list[asyncio.Task] = []
    for name in stale_keys:
        removed = state._sessions.pop(name, None)
        if not removed:
            continue
        try:
            save_session_to_history(state, removed, closed=True)
        except Exception:
            logger.error("Cleanup: failed to archive session %s", name, exc_info=True)
            state._sessions[name] = removed
            failed.append(name)
            continue
        else:
            state._restricted_keys.discard(f"dashboard:{name}")
        try:
            await state.sessions.remove(_history_key_for(name))
        except Exception:
            logger.warning("Cleanup: session remove failed for %s", name, exc_info=True)
        archived.append(name)
        if removed.running and removed.task is not None:
            removed.task.cancel()
            _tasks_to_cancel.append(removed.task)
    if _tasks_to_cancel:
        await asyncio.wait(_tasks_to_cancel, timeout=5.0)
    if archived:
        _sync_dashboard_sessions(state)
        state.push_sessions_update()
        state.push_refresh("history")
    sel().log_api_access(
        caller="dashboard",
        operation="chat.sessions_cleanup",
        outcome="ok" if not failed else ("partial" if archived else "error"),
        source="dashboard",
        resources=f"archived={len(archived)} failed={len(failed)} threshold={max_days}d keys={','.join(archived[:10])}",  # noqa: E501
    )
    return web.json_response(
        {"ok": True, "archived": len(archived), "keys": archived, "failed": failed}
    )


async def api_chat_session_agent(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/agent — set agent for a chat session."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    agent_name = body.get("agent", "")
    if agent_name and not _AGENT_NAME_RE.match(agent_name):
        return web.json_response({"error": "invalid agent name"}, status=400)
    session.agent = agent_name
    session.acp_provider = ""
    session.acp_provider_agent = ""
    session._acp_meta_binding = ""

    try:
        cfg = AppConfig.load()
        matched = agent_name if agent_name in cfg.agents else None
        if agent_name and not matched:
            for k, v in cfg.agents.items():
                if v.provider_agent == agent_name:
                    matched = k
                    break
        if matched:
            session.workspace_dir = resolve_session_workspace(
                cfg, matched, session.workspace_dir
            )
    except Exception:
        logger.warning(
            "Failed to resolve agent bindings for %r", agent_name, exc_info=True
        )

    logger.info(
        "Session %s agent switched to %r, resetting session",
        name,
        agent_name or "gideon",
    )
    await state.sessions.reset(_history_key_for(name))
    if state.conversation_log:
        try:
            save_session_to_history(
                state,
                session,
                force=True,
                metadata_only=True,
            )
        except Exception:
            logger.warning(
                "Failed to persist agent for session %s", name, exc_info=True
            )
    state.push_sessions_update()
    return web.json_response(
        {"ok": True, "agent": agent_name, "workspace_dir": session.workspace_dir}
    )


def _effort_not_honorable(provider: str, effort: str) -> str | None:
    """Why *effort* cannot be honored on ACP runtime *provider*, or None if it can (`G21`).

    The composer already hides its effort pill when the bound agent declares no options
    (``effortsForAgent`` → ``[]``), but the API accepted, PERSISTED and echoed back an
    effort regardless — so the axis existed on the wire for a runtime that had reported it
    does not exist, and the value rode into the session metadata where a later read treats
    it as a real pin. A control the provider cannot honor is worse than a missing one: it
    is a setting the user is told took effect.

    Judges only what the runtime DECLARED, never a guess:
      * ``None`` from :func:`declared_efforts` → unknown (discovery cold/stale/failed).
        Fail OPEN: refusing a bind we cannot judge would make the picker unusable whenever
        discovery has not warmed, and the format check still applies.
      * ``[]`` → the backend was asked and reported no effort axis. Refuse.
      * a non-empty set → the effort must be one of the backend's own verbatim values.
        This is also why the bind path can no longer use a hardcoded ``low/medium/high/max``
        ladder: a backend declaring ``xhigh`` or ``minimal`` was being refused a value it
        had itself offered.
    """
    if not effort or not provider:
        return None
    from gideon.interfaces.dashboard.handlers.providers import declared_efforts

    declared = declared_efforts(provider)
    if declared is None:
        return None
    if not declared:
        return f"{provider} declares no reasoning-effort options, so an effort cannot be pinned on it"
    if effort not in declared:
        return (
            f"{provider} declares reasoning efforts "
            f"{', '.join(declared)} — {effort!r} is not one of them"
        )
    return None


async def api_chat_session_acp_agent(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/acp-agent — bind a DISCOVERED ACP agent.

    Body: ``{provider, provider_agent?, model?, reasoning_effort?}`` where
    ``provider`` is the runtime id (``acp:<cli>``), ``provider_agent`` the ACP
    modeId (persona-style agent; omit/empty for claude), ``model`` an optional override
    from the runtime's model list, and ``reasoning_effort`` the pinned effort
    (claude effort-agents). These are EPHEMERAL session overrides — nothing is
    written to config (discovered catalogs are account-dynamic). They win over the
    named-definition resolution in chat_runner. Passing an empty ``provider``
    clears the override (revert to the saved/default agent)."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    provider = str(body.get("provider", "") or "").strip()
    if provider and not provider.startswith("acp:"):
        return web.json_response(
            {"error": "provider must be an acp:<cli> runtime id"}, status=400
        )
    provider_agent = str(body.get("provider_agent", "") or "").strip()
    if provider_agent and not _AGENT_NAME_RE.match(provider_agent):
        return web.json_response({"error": "invalid provider_agent"}, status=400)
    raw_effort = str(body.get("reasoning_effort", "") or "").strip()
    effort = _validate_reasoning_effort(raw_effort)
    if raw_effort and not effort:
        return json_error(
            "invalid_reasoning_effort",
            message="reasoning_effort must be a short lowercase token (a-z0-9_-) or ''",
            status=400,
        )
    _refusal = _effort_not_honorable(provider, effort)
    if _refusal:
        return json_error("reasoning_effort_not_declared", message=_refusal, status=400)

    session.acp_provider = provider
    session.acp_provider_agent = provider_agent if provider else ""
    session._acp_meta_binding = ""
    if "model" in body:
        session.model = str(body.get("model", "") or "")
    session.reasoning_effort = effort

    logger.info(
        "Session %s ACP override → provider=%r agent=%r model=%r effort=%r",
        name,
        provider,
        provider_agent,
        session.model,
        effort,
    )
    await state.sessions.reset(_history_key_for(name))
    if state.conversation_log:
        try:
            save_session_to_history(
                state,
                session,
                force=True,
                metadata_only=True,
            )
        except Exception:
            logger.warning(
                "Failed to persist ACP override for session %s", name, exc_info=True
            )
    state.push_sessions_update()
    return web.json_response(
        {
            "ok": True,
            "provider": provider,
            "provider_agent": session.acp_provider_agent,
            "model": session.model,
            "reasoning_effort": effort,
        }
    )


async def api_chat_session_model(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/model — set model for a chat session."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    model_name = _normalize_model(body.get("model", ""))
    if session.model == model_name:
        return web.json_response({"ok": True, "model": model_name})
    session.model = model_name
    logger.info(
        "Session %s model switched to %r, resetting session", name, model_name or "auto"
    )
    await state.sessions.reset(_history_key_for(name))
    state.push_sessions_update()
    return web.json_response({"ok": True, "model": model_name})


async def api_chat_session_reasoning_effort(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/reasoning-effort — set reasoning effort.

    Body: {"reasoning_effort": "" | "low" | "medium" | "high" | "max"}.
    "" = provider default (e.g. CC falls back to its opus heuristic).

    Currently consumed by Claude Code only; ACP/OpenCode wired later via
    the same `reasoning_effort_override` factory kwarg seam.
    """
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_effort = body.get("reasoning_effort", "")
    if not isinstance(raw_effort, str):
        return web.json_response(
            {"error": "reasoning_effort must be a string"}, status=400
        )
    effort = _validate_reasoning_effort(raw_effort)
    if raw_effort and not effort:
        return web.json_response(
            {
                "error": "reasoning_effort must be a short lowercase token (a-z0-9_-) or ''"
            },
            status=400,
        )
    _refusal = _effort_not_honorable(
        str(getattr(session, "acp_provider", "") or ""), effort
    )
    if _refusal:
        return json_error("reasoning_effort_not_declared", message=_refusal, status=400)
    if session.reasoning_effort == effort:
        return web.json_response({"ok": True, "reasoning_effort": effort})
    session.reasoning_effort = effort
    logger.info(
        "Session %s reasoning_effort switched to %r, resetting session",
        name,
        effort or "default",
    )
    await state.sessions.reset(_history_key_for(name))
    state.push_sessions_update()
    return web.json_response({"ok": True, "reasoning_effort": effort})


async def api_chat_session_workspace_dir(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/workspace-dir — set the working directory.

    The working directory is the session's workspace: it is the agent's cwd and
    scopes the session's memory partition.

    Clearing is an EXPLICIT ``{"workspace_dir": ""}``. A body that omits the key is
    refused rather than treated as a clear: measured during the `AAP-3` sweep, a
    request with a mistyped key (``{"dir": "/some/path"}``) answered
    ``{"ok": true, "workspace_dir": ""}`` and *unbound* the session's workspace. For
    an ACP session that binding decides where the agent's CLI actually runs, so a
    silent clear is the same defect class as the profile-bound cwd escape (`G39`) —
    the caller believes it set a directory and the agent lands somewhere else.
    """
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    if "workspace_dir" not in body:
        return web.json_response(
            {"error": "workspace_dir is required (send an empty string to clear it)"},
            status=400,
        )
    workspace_dir = body["workspace_dir"]
    if not isinstance(workspace_dir, str):
        return web.json_response(
            {"error": "workspace_dir must be a string"}, status=400
        )
    workspace_dir = workspace_dir.strip()
    if workspace_dir:
        workspace_dir = os.path.realpath(os.path.expanduser(workspace_dir))
        if not os.path.isdir(workspace_dir):
            return web.json_response({"error": "Not a directory"}, status=400)
        if is_sensitive_path(workspace_dir):
            sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="chat_session_workspace_dir",
                outcome="denied",
                resources=f"session={name} workspace_dir={workspace_dir}",
                error="sensitive path",
            )
            return web.json_response({"error": "Access denied"}, status=403)
    session.workspace_dir = workspace_dir
    logger.info("Session %s workspace_dir set to %r", name, workspace_dir)
    sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="chat_session_workspace_dir",
        outcome="allowed",
        resources=f"session={name} workspace_dir={workspace_dir}",
    )
    if workspace_dir:
        try:
            await asyncio.to_thread(_save_recent_project, workspace_dir)
        except Exception:
            logger.warning("Failed to save recent workspace dir", exc_info=True)
    state.push_sessions_update()
    return web.json_response({"ok": True, "workspace_dir": workspace_dir})


_MAX_RECENT_PROJECTS = 10


def _recent_projects_path() -> Path:
    return config_dir() / "recent_projects.json"


def _save_recent_project(path: str) -> None:
    """Prepend path to recent projects list (deduped, capped)."""

    fp = _recent_projects_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else []
    except (json.JSONDecodeError, OSError):
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing = [p for p in existing if p != path]
    existing.insert(0, path)
    existing = existing[:_MAX_RECENT_PROJECTS]
    atomic_write(fp, json.dumps(existing))


async def api_recent_projects(request: web.Request) -> web.Response:
    """GET /api/recent-projects — list recently used project directories."""

    def _read_recent_projects() -> list[str]:
        fp = _recent_projects_path()
        try:
            dirs = json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else []
        except Exception:
            dirs = []
        if not isinstance(dirs, list):
            dirs = []
        return [
            d
            for d in dirs
            if isinstance(d, str) and os.path.isdir(d) and not is_sensitive_path(d)
        ]

    dirs = await asyncio.to_thread(_read_recent_projects)
    sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="recent_projects",
        outcome="allowed",
        resources=f"count={len(dirs)}",
    )
    return web.json_response({"dirs": dirs})


async def api_chat_session_resume(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/resume — load a history session into a session."""
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    if name.startswith("dashboard_"):
        name = name.removeprefix("dashboard_")
    if not state.conversation_log:
        return web.json_response({"error": "no conversation log"}, status=400)
    try:
        body = await read_json_body(request)
    except Exception:
        body = {}
    history_key = body.get("key", name)

    canonical = _history_key_for(history_key)
    existing = state._sessions.get(name)
    if not existing:
        for session in state._sessions.values():
            if _history_key_for(session.key) == canonical:
                existing = session
                break
    if existing:
        request_app = request.get("app", "")
        if request_app:
            if not existing._app:
                sel().log_api_access(
                    caller=request_app,
                    operation="session_resume",
                    outcome="denied",
                    source="app_isolation",
                    resources=f"session={existing.key}",
                    error="app cannot access unscoped sessions",
                )
                return web.json_response(
                    {"error": "app cannot access unscoped sessions"}, status=403
                )
            elif request_app != existing._app:
                sel().log_api_access(
                    caller=request_app,
                    operation="session_resume",
                    outcome="denied",
                    source="app_isolation",
                    resources=f"session={existing.key}",
                    error="app does not own this session",
                )
                return web.json_response(
                    {"error": "app does not own this session"}, status=403
                )
        total = len(existing.messages)
        recent = existing.messages[-200:] if total > 200 else existing.messages
        prepared = _prepare_messages(recent, existing.running)
        return web.json_response(
            {
                "ok": True,
                "key": existing.key,
                "messages": prepared,
                "queue": [
                    {"id": q["id"], "content": _redact_for_display(q["content"])}
                    for q in existing._queue
                ],
                "total": total,
                "has_more": total > 200,
                "memory_mode": existing.memory_mode,
            }
        )

    if not session_key_exists(state, history_key):
        return json_error("session_not_found", status=404)
    session = state.get_or_create_session(name, app=request.get("app", ""))
    resolved_key = persisted_history_key(state.conversation_log, history_key)
    title = body.get("title", "")
    if title:
        session.title = title
        session._titled = True
    else:
        sessions = state.conversation_log.list_sessions()
        for s in sessions:
            if s.get("key") == resolved_key:
                session.title = s.get("title", resolved_key)
                session._titled = True
                break
    meta = state.conversation_log.get_metadata(resolved_key)
    if meta.get("created_at"):
        session.created_at = meta["created_at"]
    if meta.get("agent"):
        session.agent = meta["agent"]
    if meta.get("workspace_dir"):
        session.workspace_dir = meta["workspace_dir"]
    if meta.get("mode"):
        session.mode = meta["mode"]
    if meta.get("folder_id"):
        session.folder_id = meta["folder_id"]
    if meta.get("pinned"):
        session.pinned = True
    if meta.get("color_index") is not None:
        session.color_index = meta["color_index"]
    if meta.get("color_theme"):
        session.color_theme = meta["color_theme"]
    if meta.get("natural_voice"):
        from gideon.integrations.natural_voice import normalize_conversation_choice

        session.natural_voice = normalize_conversation_choice(meta["natural_voice"])
    mm = meta.get("memory_mode", "persistent")
    session.memory_mode = mm
    if mm != "persistent":
        state._restricted_keys.add(f"dashboard:{name}")
    else:
        state._restricted_keys.discard(f"dashboard:{name}")
    if meta.get("forked_from") is not None:
        session.forked_from = meta["forked_from"]
    if meta.get("closed"):
        try:
            path = state.conversation_log._path(resolved_key)
            if path.exists():
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                if lines:
                    first_line_data = json.loads(lines[0])
                    first_line_data.pop("closed", None)
                    lines[0] = json.dumps(first_line_data) + "\n"
                    atomic_write(path, "".join(lines))
                    state.conversation_log._meta_cache.pop(resolved_key, None)
        except Exception:
            logger.warning(
                "Failed to clear closed flag for %s", resolved_key, exc_info=True
            )
    all_messages = state.conversation_log.read_messages_chained(resolved_key)
    disk_total = len(all_messages)
    for m in all_messages:
        role = m.get("role", "assistant")
        cls = "msg msg-u" if role == "user" else "msg msg-a"
        content = m.get("content", "")
        if role != "user":
            content, _ = redact_exfiltration_urls(content)
            content, _ = redact_credentials(content)
        session.append(
            role,
            content,
            cls,
            ts=m.get("ts", ""),
            meta=m.get("meta") if isinstance(m.get("meta"), dict) else None,
        )
        _attach_variants(session, m)
        _attach_rewound(session, m)
    session.drain()
    session._resumed_count = len(session.messages)
    total = disk_total
    recent = (
        session.messages[-200:] if len(session.messages) > 200 else session.messages
    )
    _sync_dashboard_sessions(state)
    state.push_sessions_update()
    return web.json_response(
        {
            "ok": True,
            "key": session.key,
            "messages": _prepare_messages(recent, session.running),
            "queue": [
                {"id": q["id"], "content": _redact_for_display(q["content"])}
                for q in session._queue
            ],
            "total": total,
            "has_more": total > len(recent),
            "memory_mode": session.memory_mode,
        }
    )


VALID_APPROVAL_MODES = ("normal", "trust", "trust_reads", "yolo")


def _session_approval_mode(state: ConsoleState, session: _ChatSession | None) -> str:
    if state.is_yolo_active():
        return "yolo"
    if session is None:
        return "normal"
    if session._trust:
        return "trust"
    if session._trust_reads:
        return "trust_reads"
    return "normal"


async def api_chat_mode(request: web.Request) -> web.Response:
    """POST /api/chat/mode — set the tool APPROVAL mode (whether tools auto-approve).

    Modes:
      - ``normal``: reset to interactive (ask for each tool)
      - ``trust_reads``: auto-approve read-only tools
      - ``trust``: auto-approve tools for active session
      - ``yolo``: auto-approve all tools everywhere

    Orthogonal to the TASK mode (agent/ask/plan/build — see ``/api/chat/task-mode``),
    which gates *which* tools are available + how the agent frames the work. Unlike
    the per-tool approve endpoint, this doesn't require a pending approval — it
    preemptively sets the mode for future tools.
    """
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    mode = body.get("mode", "normal")
    if mode not in VALID_APPROVAL_MODES:
        return web.json_response(
            {
                "ok": False,
                "error": f"invalid mode (expected one of {VALID_APPROVAL_MODES})",
            },
            status=400,
        )
    session_name = body.get("session") or None
    if session_name is not None and session_name not in state._sessions:
        return web.json_response({"ok": False, "error": "unknown session"}, status=400)

    from gideon.security.guardrails.ladder import approval_screening_verdict

    screening = approval_screening_verdict(mode)
    if not screening.allowed:
        current = _session_approval_mode(
            state, state._sessions.get(session_name) if session_name else None
        )
        return web.json_response(
            {
                "ok": False,
                "mode": current,
                "approval_screening": screening.to_dict(),
            }
        )

    if mode == "yolo":
        state.enable_yolo()
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:yolo",
                outcome="enabled",
                resources=",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning("SEL audit failed for YOLO mode activation", exc_info=True)
    elif mode == "trust_reads":
        state.disable_yolo()
        if session_name is not None:
            state._sessions[session_name]._trust = False
            state._sessions[session_name]._trust_reads = True
        else:
            for session in state._sessions.values():
                session._trust = False
                session._trust_reads = True
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:trust_reads",
                outcome="enabled",
                resources=session_name
                or ",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning(
                "SEL audit failed for trust_reads mode activation", exc_info=True
            )
    elif mode == "trust":
        state.disable_yolo()
        if session_name is not None:
            state._sessions[session_name]._trust = True
        else:
            for session in state._sessions.values():
                session._trust = True
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:trust",
                outcome="enabled",
                resources=session_name
                or ",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning("SEL audit failed for trust mode activation", exc_info=True)
    else:
        state.disable_yolo()
        if session_name is not None:
            state._sessions[session_name]._trust = False
            state._sessions[session_name]._trust_reads = False
        else:
            for session in state._sessions.values():
                session._trust = False
                session._trust_reads = False
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:normal",
                outcome="disabled",
                resources=session_name
                or ",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning("SEL audit failed for normal mode activation", exc_info=True)

    if mode in ("trust", "yolo"):
        for session in state._sessions.values():
            for aid, fut in list(session._approval_futures.items()):
                if not fut.done():
                    fut.set_result("approved")
                    _mark_permission_resolved(session.messages, aid, mode)
                    state.broadcast_ws(
                        "approval_resolved", {"id": aid, "approved": True}
                    )
                    try:
                        sel().log_api_access(
                            caller=f"dashboard:{session.key}",
                            operation=f"tool_approval:bulk_{mode}",
                            outcome="approved",
                            resources=aid,
                        )
                    except Exception:
                        logger.warning(
                            "SEL audit failed for bulk approval %s", aid, exc_info=True
                        )
        for aid in list(state._approval_futures):
            fut = state._approval_futures[aid]
            if not fut.done():
                state.resolve_approval(aid, True)
                try:
                    sel().log_api_access(
                        caller="dashboard:background",
                        operation=f"tool_approval:bulk_{mode}",
                        outcome="approved",
                        resources=aid,
                    )
                except Exception:
                    logger.warning(
                        "SEL audit failed for bulk approval %s", aid, exc_info=True
                    )
    for session in state._sessions.values():
        policy = "auto" if session._trust or state.is_yolo_active() else ""
        state.sessions.set_approval_policy(f"dashboard:{session.key}", policy)

    state.push_sessions_update()
    return web.json_response(
        {
            "ok": True,
            "mode": mode,
            "approval_screening": screening.to_dict(),
        }
    )


VALID_TASK_MODES = ("agent", "ask", "plan", "build")


async def api_chat_task_mode(request: web.Request) -> web.Response:
    """POST /api/chat/task-mode — set the per-session TASK mode.

    Task mode is ORTHOGONAL to the approval mode (``/api/chat/mode``): approval
    gates *whether* a tool auto-approves; task mode gates *which* tools are
    available + *how* the agent frames the work, layered on the active agent:
      - ``agent``: full execution (default)
      - ``ask``:   read-only — only SAFE/read tools run; mutating tools denied
      - ``plan``:  the agent plans; NO tool executes
      - ``build``: scoped to producing an artifact/widget/skill

    Body: ``{mode: <task mode>, session?: <key>}``. ``mode`` is REQUIRED. When
    ``session`` is omitted the mode applies to all sessions (mirrors the approval
    handler's broadcast shape); the response names the sessions it changed.
    """
    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    mode = body.get("mode")
    if mode not in VALID_TASK_MODES:
        return web.json_response(
            {"error": f"invalid task mode (expected one of {VALID_TASK_MODES})"},
            status=400,
        )
    session_name = body.get("session") or None
    if session_name is not None and session_name not in state._sessions:
        return web.json_response({"ok": False, "error": "unknown session"}, status=400)

    targets = (
        [state._sessions[session_name]]
        if session_name is not None
        else list(state._sessions.values())
    )
    if mode != "plan":
        from gideon.interfaces.dashboard import chat_plan

        gated = [s.key for s in targets if chat_plan.awaiting_review(s.key)]
        if gated:
            return web.json_response(
                {
                    "error": {
                        "code": "plan_awaiting_approval",
                        "message": (
                            "This chat's plan is awaiting your review — approve it or "
                            "cancel plan mode to change the task mode."
                        ),
                    },
                    "sessions": gated,
                },
                status=409,
            )
    for session in targets:
        apply_task_mode(state, session, mode)
    try:
        sel().log_api_access(
            caller="dashboard:task-mode",
            operation=f"task_mode_change:{mode}",
            outcome="enabled",
            resources=session_name or ",".join(s.key for s in state._sessions.values()),
        )
    except Exception:
        logger.warning("SEL audit failed for task-mode change", exc_info=True)

    state.push_sessions_update()
    return web.json_response(
        {"ok": True, "task_mode": mode, "sessions": [s.key for s in targets]}
    )


_APPROVE_ACTIONS = frozenset(
    {"approved", "rejected", "trust", "trust_agent", "trust_reads", "yolo"}
)


def persistable_grant_target(
    session: _ChatSession, request_id: str, cfg: AppConfig
) -> str | None:
    """Return the configured agent eligible for a prompt/write/record grant."""
    permission_kind = ""
    for message in reversed(session.messages):
        if message.get("role") != "permission":
            continue
        try:
            meta = json.loads(message.get("cls", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        if meta.get("request_id") == request_id:
            permission_kind = str(meta.get("tool_kind") or "").lower()
            break
    if permission_kind not in {"prompt", "write", "record"}:
        return None

    from gideon.engine.agents.defaults import is_reserved_agent

    agent_name = (session.agent or "").strip() or cfg.default_agent
    if agent_name and not is_reserved_agent(agent_name) and agent_name in cfg.agents:
        return agent_name
    return None


async def api_chat_session_approve(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/approve — resolve a pending tool approval."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    action = body.get("action", "rejected")
    if action not in _APPROVE_ACTIONS:
        return web.json_response(
            {
                "error": f"unknown action {action!r}",
                "allowed": sorted(_APPROVE_ACTIONS),
            },
            status=400,
        )
    original_action = action
    reported_decision = original_action
    request_id = body.get("request_id", "")
    screening = None
    requested_mode = {
        "trust": "trust",
        "trust_agent": "trust",
        "trust_reads": "trust_reads",
        "yolo": "yolo",
    }.get(original_action)
    if requested_mode:
        from gideon.security.guardrails.ladder import approval_screening_verdict

        screening = approval_screening_verdict(requested_mode)
    grant_allowed = screening is None or screening.allowed
    if action == "trust" and grant_allowed:
        session._trust = True
        state.sessions.set_approval_policy(f"dashboard:{name}", "auto")
        action = "approved"
    elif action == "trust_agent" and grant_allowed:
        session._trust = True
        state.sessions.set_approval_policy(f"dashboard:{name}", "auto")
        action = "approved"
        try:
            cfg = AppConfig.load()
            agent_name = persistable_grant_target(session, request_id, cfg)
            if agent_name:
                prof = cfg.agents[agent_name]
                if prof.approval_mode != "auto":
                    prof.approval_mode = "auto"
                    cfg.save()
                sel().log_api_access(
                    caller="dashboard:approval",
                    operation="mode_change:always_for_agent",
                    outcome="enabled",
                    resources=f"{name} agent={agent_name}",
                )
            else:
                reported_decision = "trust_agent_session"
                logger.info(
                    "trust_agent on non-persistable agent %r — session-scope only",
                    agent_name or "(none)",
                )
        except Exception:
            reported_decision = "trust_agent_session"
            logger.warning("Failed to persist always-for-agent grant", exc_info=True)
    elif action == "trust_reads" and grant_allowed:
        action = "approved_trust_reads"
    elif action == "yolo" and grant_allowed:
        state.enable_yolo()
        for s in state._sessions.values():
            state.sessions.set_approval_policy(f"dashboard:{s.key}", "auto")
        action = "approved"
    elif not grant_allowed:
        action = "approved"
    if not request_id:
        pending = [(k, f) for k, f in session._approval_futures.items() if not f.done()]
        if len(pending) == 1:
            request_id, fut = pending[0]
        else:
            fut = None
    else:
        fut = session._approval_futures.get(request_id)
    if not fut or fut.done():
        if not request_id and session._approval_futures:
            pending_ids = [
                k for k, f in session._approval_futures.items() if not f.done()
            ]
            if len(pending_ids) > 1:
                return web.json_response(
                    {
                        "error": "multiple approvals pending, specify request_id",
                        "pending": pending_ids,
                    },
                    status=400,
                )
        return web.json_response({"error": "no pending approval"}, status=404)
    resolved = action if action in ("approved", "approved_trust_reads") else "rejected"
    fut.set_result(resolved)
    if request_id:
        _mark_permission_resolved(
            session.messages,
            request_id,
            (
                reported_decision
                if grant_allowed
                and original_action in ("trust", "trust_agent", "trust_reads")
                else resolved
            ),
        )
    if request_id:
        state.broadcast_ws(
            "approval_resolved",
            {
                "id": request_id,
                "approved": resolved != "rejected",
                "decision": reported_decision if grant_allowed else resolved,
            },
        )
    state.push_sessions_update()
    try:
        sel().log_api_access(
            caller=f"dashboard:{name}",
            operation=f"tool_approval:{original_action}",
            outcome=resolved,
            resources=request_id,
        )
    except Exception:
        logger.warning("SEL audit failed for approval %s", request_id, exc_info=True)
    payload: dict[str, object] = {
        "ok": True,
        "decision": (
            reported_decision
            if grant_allowed
            and original_action in ("trust", "trust_agent", "trust_reads", "yolo")
            else resolved
        ),
    }
    if screening is not None:
        payload["approval_screening"] = screening.to_dict()
        payload["mode"] = _session_approval_mode(state, session)
    return web.json_response(payload)


MAX_COLOR_INDEX = 20


async def api_chat_session_color(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/color — set session color.

    Clearing is an explicit ``{"color_index": null}``; an omitted or misspelled
    field is rejected rather than interpreted as a clear.
    """
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    if "color_index" not in body:
        return web.json_response(
            {"error": "color_index is required (send null to clear it)"}, status=400
        )
    ci = body["color_index"]
    if ci is not None and (
        isinstance(ci, bool)
        or not isinstance(ci, int)
        or ci < 0
        or ci > MAX_COLOR_INDEX
    ):
        return web.json_response(
            {
                "error": f"color_index must be a non-negative integer <= {MAX_COLOR_INDEX} or null"
            },
            status=400,
        )
    session.color_index = ci
    session._dirty = True
    state.push_sessions_update()
    return web.json_response({"ok": True, "color_index": ci})


def _natural_voice_payload(session: _ChatSession) -> dict[str, object]:
    """The composer's natural-voice state for *session*, resolved server-side.

    Carries what this conversation states, what the bound agent's definition
    carries, and the RESOLVED pair (effective + which scope decided). Resolved
    here rather than in the frontend on purpose: the resolution order exists
    exactly once, in ``natural_voice.NATURAL_VOICE_PRECEDENCE``, and a frontend
    that re-derived it would be a second statement of it, free to drift.
    """
    from gideon.integrations import natural_voice as nv

    agent_on = nv.agent_default(session.agent or "")
    resolved = nv.resolve(session.natural_voice, agent_on)
    return {
        "natural_voice": session.natural_voice,
        "natural_voice_agent_default": agent_on,
        "natural_voice_effective": resolved.enabled,
        "natural_voice_source": resolved.source,
    }


async def api_chat_session_natural_voice(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/natural-voice — set the per-conversation scope.

    Body ``{"natural_voice": "" | "on" | "off"}``. ``""`` clears the override so
    the conversation inherits the bound agent's preference again. The field must
    be present: an omitted or misspelled field is not an instruction to clear.
    Responds with the re-resolved state so the composer shows what actually takes
    effect instead of assuming its own click won.
    """
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if not session:
        return json_error("not_found", status=404)
    try:
        body = await read_json_body(request)
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)
    if "natural_voice" not in body:
        return json_error(
            "bad_request",
            message='natural_voice is required (send "" to clear it)',
            status=400,
        )
    from gideon.integrations.natural_voice import normalize_conversation_choice

    raw = body["natural_voice"]
    choice = normalize_conversation_choice(raw)
    if choice == "" and str(raw or "").strip() != "":
        return json_error(
            "bad_request",
            message='natural_voice must be "", "on" or "off"',
            status=400,
        )
    session.natural_voice = choice
    session._dirty = True
    state.push_sessions_update()
    return web.json_response({"ok": True, **_natural_voice_payload(session)})


_MAX_CONTEXT_PER_SOURCE = 10


async def api_chat_session_context(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/context — inject silent background context.

    Adds a ContextEntry to the session's ``_pending_context`` queue.
    The content is consumed on the next user-initiated message via
    ``ctx_builder.build_message()`` and prepended to the LLM prompt.

    No LLM turn is triggered, no WS event is broadcast, and no visible
    message is appended to the session's chat history.

    Body::

        {
            "content": "...",
            "source": "watch-check",   // optional
            "ephemeral": true,         // optional, default true
            "maxAge": 300              // optional, seconds
        }
    """

    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "session not found"}, status=404)

    request_app = request.get("app", "")
    if request_app:
        if not session._app:
            sel().log_api_access(
                caller=request_app,
                operation="context_inject",
                outcome="denied",
                source="app_isolation",
                resources=f"session={name}",
                error="app cannot access unscoped sessions",
            )
            return web.json_response(
                {"error": "app cannot access unscoped sessions"}, status=403
            )
        elif request_app != session._app:
            sel().log_api_access(
                caller=request_app,
                operation="context_inject",
                outcome="denied",
                source="app_isolation",
                resources=f"session={name}",
                error="app does not own this session",
            )
            return web.json_response(
                {"error": "app does not own this session"}, status=403
            )

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    content = body.get("content", "")
    if not content:
        return web.json_response({"error": "content is required"}, status=400)

    max_context_content = 40000
    if len(content) > max_context_content:
        return web.json_response(
            {"error": f"content exceeds {max_context_content} char limit"}, status=400
        )

    entry: dict[str, object] = {
        "content": content,
        "source": body.get("source", ""),
        "ephemeral": body.get("ephemeral", True),
        "injectedAt": time.time(),
    }
    max_age = body.get("maxAge")
    if max_age is not None:
        entry["maxAge"] = max_age

    source = body.get("source", "")
    if source:
        source_count = sum(
            1 for e in session._pending_context if e.get("source") == source
        )
        if source_count >= _MAX_CONTEXT_PER_SOURCE:
            return web.json_response(
                {
                    "error": f"source {source!r} has {_MAX_CONTEXT_PER_SOURCE} pending entries"
                },
                status=429,
            )

    max_pending_context = 50
    while len(session._pending_context) >= max_pending_context:
        session._pending_context.pop(0)

    session._pending_context.append(entry)  # type: ignore[arg-type]

    sel().log_api_access(
        caller=request_app or request.get("user", "dashboard"),
        operation="context_inject",
        outcome="ok",
        source="app_kit",
        resources=f"session={name}",
    )

    return web.json_response({"ok": True, "pending": len(session._pending_context)})


_NAV_MAX_LINKS = 30
_NAV_CONTEXT_CAP = 400
_NAV_SUMMARY_CAP = 80


def _build_nav_links_prompt(links: list[dict[str, str]]) -> str:
    """One prompt for the whole batch — concise human label per (index, url).

    The instruction lives in the prompt system (bundled ``task-nav-links``); we
    assemble the numbered link lines and render them into it."""
    link_lines = []
    for i, link in enumerate(links):
        ctx = link.get("context", "")
        ctx = f" — context: {ctx}" if ctx else ""
        link_lines.append(f"{i}: {link.get('url', '')}{ctx}")
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    return (
        render_use_case_prompt("nav_links", {"numbered_links": "\n".join(link_lines)})
        or ""
    )


def _parse_nav_links_response(text: str, count: int) -> list[str]:
    """Parse ``<index>: <title>`` lines into a list aligned to the input order.

    Any index we can't parse (or that the model omitted) stays an empty string,
    so the response is always positionally aligned to the request.
    """
    summaries = [""] * count
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        idx_part, _, title = line.partition(":")
        try:
            idx = int(idx_part.strip())
        except ValueError:
            continue
        if 0 <= idx < count:
            clean, _ = redact_exfiltration_urls(title.strip())
            clean, _ = redact_credentials(clean)
            summaries[idx] = clean[:_NAV_SUMMARY_CAP]
    return summaries


async def api_nav_resolve_links(request: web.Request) -> web.Response:
    """POST /api/chat/nav/resolve-links — batch-summarize bare links.

    Request ``{"links": [{"url": str, "context": str}]}``; response
    ``{"summaries": [str]}`` positionally aligned to the input (empty string for
    any that fail). One stateless Model-entity call summarizes the whole batch
    (the explicit anti-N+1 goal) via the ``background`` use-case binding.
    """
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_links = body.get("links")
    if not isinstance(raw_links, list):
        return web.json_response({"error": "links must be a list"}, status=400)

    links: list[dict[str, str]] = []
    for item in raw_links[:_NAV_MAX_LINKS]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        links.append(
            {
                "url": url,
                "context": str(item.get("context", "")).strip()[:_NAV_CONTEXT_CAP],
            }
        )

    if not links:
        return web.json_response({"summaries": []})

    try:
        from gideon.integrations.llm_helpers import one_shot_completion

        text = await one_shot_completion(
            _build_nav_links_prompt(links), use_case="background"
        )
    except Exception:
        logger.warning("nav link resolve failed", exc_info=True)
        return web.json_response({"summaries": [""] * len(links)})

    summaries = _parse_nav_links_response(text, len(links))
    try:
        sel().log_api_access(
            caller="dashboard:nav",
            operation="resolve_links",
            outcome="ok",
            resources=f"count={len(links)}",
        )
    except Exception:
        logger.debug("SEL audit failed for nav resolve-links", exc_info=True)
    return web.json_response({"summaries": summaries})
