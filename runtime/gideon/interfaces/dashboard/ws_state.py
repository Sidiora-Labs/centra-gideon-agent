"""WebSocket transport behavior mixed into the dashboard state controller."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

from gideon.assurance import trace_recorder as _trace

logger = logging.getLogger(__name__)

NOTE_TYPE_NOTIFICATION = "notification"


class WebSocketState:
    def _broadcast(self, note: dict[str, Any]) -> None:
        """Fan a dashboard state note out to the WebSocket clients.

        The interactive dashboard surface runs on a single multiplexed
        WebSocket (see ``web/src/hooks/useWebSocket.ts``), so the dashboard's
        always-on concerns — status, session list/titles, notifications, and
        refresh hints — ride that one connection. This is the single-transport-
        per-concern doctrine: always-on state on the WS, page-scoped
        feeds (loops/logs/file-watch) on their own per-resource SSE.
        """
        if _trace.is_recording():
            _trace.record("ws", str(note.get("_type", "notification")), "note", note)
        if not self._ws_clients:
            return
        msg_type = note.get("_type") or NOTE_TYPE_NOTIFICATION
        extra: dict[str, Any] | None = None
        if msg_type == "sessions":
            data: object = note.get("_sessions_list") or json.loads(note["sessions"])
            extra = {
                "yolo": note.get("_yolo", False),
                "channelTrusted": note.get("channelTrusted", False),
            }
        elif msg_type == "session_title":
            data = {"key": note["key"], "title": note["title"]}
        elif msg_type == "refresh":
            data = {"kinds": note["kinds"].split(",")}
        elif msg_type == "update_progress":
            data = {"step": note["step"], "detail": note.get("detail", "")}
        elif msg_type == "chat_message":
            chat_data: dict[str, Any] = {
                "session": note["session"],
                "role": note["role"],
                "content": note["content"],
                "ts": note.get("ts", ""),
            }
            if note.get("cls"):
                chat_data["cls"] = note["cls"]
            if note.get("meta"):
                chat_data["meta"] = note["meta"]
            data = chat_data
        elif msg_type == NOTE_TYPE_NOTIFICATION:
            data = note
        else:
            logger.error(
                "dashboard note has an unmapped _type %r — dropped rather than broadcast as a "
                "raw notification; add it to _broadcast's translation",
                msg_type,
            )
            return
        self.broadcast_ws(msg_type, data, extra=extra)

    def _send_ws_all(self, msg: str) -> None:
        """Send a pre-serialized JSON string to all WS clients.

        Safe to call from ANY thread. On the gateway loop each send is scheduled
        with ensure_future; off-loop (MCP tool subprocess callbacks, subagent/cron
        announce paths) it's submitted to the captured gateway loop via
        run_coroutine_threadsafe. The old code called ensure_future directly, which
        raised off-loop → the send coroutine was dropped unawaited (a RuntimeWarning
        + a silently-lost frame, e.g. subagent lifecycle cards not updating live)."""
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws_clients):
            if ws.closed:
                dead.append(ws)
                continue
            try:
                if not self._schedule_ws_send(ws.send_str(msg), ws):
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._remove_ws(ws)

    def _schedule_ws_send(  # type: ignore[no-untyped-def]
        self, coro, ws: "web.WebSocketResponse | None" = None
    ) -> bool:
        """Schedule a WS send coroutine on the right loop from any thread.

        Returns False (so the caller can drop the client) only on an actual
        scheduling error. On the gateway loop → ensure_future; off-loop → submit to
        the captured loop with run_coroutine_threadsafe; no loop anywhere (sync
        startup/tests) → close the coroutine cleanly so it isn't reported unawaited.

        The send is wrapped in :meth:`_send_guarded` on BOTH scheduling branches:
        a fire-and-forget task's exception is never retrieved, so a client that
        disconnects mid-broadcast (navigating away from a streaming response — a
        completely routine event) used to surface as asyncio's unretrieved-task
        ERROR traceback. The guard turns it into a DEBUG line and reaps the client
        immediately instead of on the NEXT broadcast's ``ws.closed`` check."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is not None:
                asyncio.ensure_future(self._send_guarded(coro, ws))
            elif self._ws_loop is not None and not self._ws_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._send_guarded(coro, ws), self._ws_loop
                )
            else:
                coro.close()
            return True
        except Exception:
            try:
                coro.close()
            except Exception:
                pass
            return False

    async def _send_guarded(  # type: ignore[no-untyped-def]
        self, coro, ws: "web.WebSocketResponse | None"
    ) -> None:
        """Await one WS send, absorbing delivery failures.

        Nothing above this can act on a failed send — the correct response to ANY
        send error is to log quietly and drop the client, so a disconnect during an
        active broadcast never escalates past DEBUG."""
        try:
            await coro
        except Exception as exc:
            logger.debug("WS send failed (client gone?): %s", exc)
            if ws is not None:
                self._remove_ws(ws)

    def broadcast_ws(
        self, msg_type: str, data: object, *, extra: dict[str, Any] | None = None
    ) -> None:
        """Send a typed message to all WS clients (not SSE). THE one WS producer.

        Owner/dashboard connections get every event. An app-scoped connection
        (sandbox P1) gets an event ONLY if the app's manifest declares it in
        ``permissions.events`` — server-side enforcement so an untrusted app can't
        observe events it didn't ask for (the SDK's client-side filter is advisory).

        ``extra`` merges additional TOP-LEVEL envelope keys, which exists so the
        dashboard-state translator (`_broadcast`) can route through this filter instead of
        writing to the sockets itself. It had its own `_send_ws_all` call, so every
        always-on frame — sessions, titles, refresh hints, chat messages, notifications —
        reached app-scoped sockets regardless of what the app declared. One producer, one
        gate; a second write path is a second place for the gate to be missing from."""
        if not self._ws_clients:
            return
        envelope: dict[str, Any] = {"type": msg_type, "data": data}
        if extra:
            envelope.update(
                {k: v for k, v in extra.items() if k not in ("type", "data")}
            )
        msg = json.dumps(envelope)
        if not self._ws_app:
            self._send_ws_all(msg)
            return
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws_clients):
            if ws.closed:
                dead.append(ws)
                continue
            app = self._ws_app.get(ws, "")
            if app and not self._app_may_see_event(app, msg_type):
                continue
            try:
                if not self._schedule_ws_send(ws.send_str(msg), ws):
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._remove_ws(ws)

    def _app_may_see_event(self, app: str, event_type: str) -> bool:
        """Whether an app-scoped WS may receive ``event_type`` per its manifest."""
        try:
            from gideon.extensions.apps.permissions import checker_for

            checker = checker_for(app)
            return checker is not None and checker.can_use_event(event_type)
        except Exception:
            return False

    def register_ws(self, ws: web.WebSocketResponse, *, app: str = "") -> None:
        """Register a new WebSocket client.

        ``app`` scopes the connection to an installed app (sandbox P1): its events
        are filtered to the app's declared ``permissions.events`` in broadcast_ws.
        Empty (the owner/dashboard) receives the full stream."""
        if self._ws_loop is None:
            try:
                self._ws_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        self._ws_clients.append(ws)
        if app:
            self._ws_app[ws] = app

    def unregister_ws(self, ws: web.WebSocketResponse) -> None:
        """Remove a WebSocket client on disconnect."""
        self._remove_ws(ws)

    def _remove_ws(self, ws: web.WebSocketResponse) -> None:
        """Remove a WS client from all subscriber lists."""
        try:
            self._ws_clients.remove(ws)
        except ValueError:
            pass
        self._ws_app.pop(ws, None)
        self._ws_log_subscribers.discard(ws)
        self._ws_subagent_subscribers.discard(ws)

    def subscribe_logs(self, ws: web.WebSocketResponse) -> None:
        """Subscribe a WS client to log events."""
        self._ws_log_subscribers.add(ws)

    def unsubscribe_logs(self, ws: web.WebSocketResponse) -> None:
        """Unsubscribe a WS client from log events."""
        self._ws_log_subscribers.discard(ws)

    def subscribe_subagents(self, ws: web.WebSocketResponse) -> None:
        self._ws_subagent_subscribers.add(ws)

    def unsubscribe_subagents(self, ws: web.WebSocketResponse) -> None:
        self._ws_subagent_subscribers.discard(ws)

    def broadcast_ws_subagent_subscribers(self, msg_type: str, data: object) -> None:
        """Send to subagent-subscribed clients only (for heavy chunk data).

        Thread-safe like _send_ws_all: subagent chunk events originate off the
        gateway loop, so each send is scheduled onto the captured loop when needed."""
        if not self._ws_subagent_subscribers:
            return
        msg = json.dumps({"type": msg_type, "data": data})
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws_subagent_subscribers):
            if ws.closed:
                dead.append(ws)
                continue
            try:
                if not self._schedule_ws_send(ws.send_str(msg), ws):
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._remove_ws(ws)

    async def close_all_ws(self) -> None:
        """Close all WebSocket connections (called on shutdown)."""
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_clients.clear()
        self._ws_log_subscribers.clear()
        self._ws_subagent_subscribers.clear()
