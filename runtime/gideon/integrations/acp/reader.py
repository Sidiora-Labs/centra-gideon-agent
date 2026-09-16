"""A bounded mailbox dispatcher for one ACP byte stream."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from gideon.integrations.acp.types import JsonRpcMessage

logger = logging.getLogger(__name__)
_SESSION_QUEUE_MAX = 2048
_EARLY_RESPONSE_MAX = 256


class FrameRouter:
    def __init__(
        self,
        readline: Callable[[], Awaitable[bytes]],
        *,
        on_broadcast: Callable[[JsonRpcMessage], None] | None = None,
        on_server_request: Callable[[JsonRpcMessage], None] | None = None,
    ) -> None:
        self._readline = readline
        self._on_broadcast = on_broadcast
        self._on_server_request = on_server_request
        self._sessions: dict[str, asyncio.Queue[JsonRpcMessage]] = {}
        self._pending: dict[Any, asyncio.Future[JsonRpcMessage]] = {}
        self._early_responses: dict[Any, JsonRpcMessage] = {}
        self._reader_task: asyncio.Task | None = None
        self._closed = False
        self._closed_exc: Exception | None = None

    def register_session(self, session_id: str) -> asyncio.Queue[JsonRpcMessage]:
        if session_id not in self._sessions:
            mailbox: asyncio.Queue[JsonRpcMessage] = asyncio.Queue(_SESSION_QUEUE_MAX)
            self._sessions[session_id] = mailbox
            if self._closed:
                self._offer(mailbox, JsonRpcMessage(method="_router/closed"))
        return self._sessions[session_id]

    def unregister_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    def expect(self, req_id: Any) -> asyncio.Future[JsonRpcMessage]:
        future: asyncio.Future[JsonRpcMessage] = (
            asyncio.get_event_loop().create_future()
        )
        buffered = self._early_responses.pop(req_id, None)
        if buffered is not None:
            future.set_result(buffered)
        elif self._closed:
            future.set_exception(
                self._closed_exc or ConnectionError("ACP connection closed")
            )
        else:
            self._pending[req_id] = future
        return future

    def start(self) -> None:
        if not self._closed and self._reader_task is None:
            self._reader_task = asyncio.ensure_future(self._run())

    def _finish(self, reason: Exception | None) -> None:
        if self._closed:
            return
        self._closed, self._closed_exc = True, reason
        outstanding, self._pending = self._pending, {}
        self._early_responses.clear()
        for future in outstanding.values():
            if not future.done():
                future.set_exception(reason or ConnectionError("ACP connection closed"))
        for mailbox in self._sessions.values():
            self._offer(mailbox, JsonRpcMessage(method="_router/closed"))

    async def close(self, exc: Exception | None = None) -> None:
        self._finish(exc)
        task, self._reader_task = self._reader_task, None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @staticmethod
    def _decode(line: bytes) -> JsonRpcMessage | None:
        try:
            fields = json.loads(line)
        except (ValueError, UnicodeError):
            return None
        if not isinstance(fields, dict):
            return None
        return JsonRpcMessage(
            **{
                key: fields.get(key)
                for key in ("id", "method", "result", "error", "params")
            }
        )

    async def _run(self) -> None:
        failure: Exception | None = None
        try:
            while not self._closed:
                chunk = await self._readline()
                if chunk == b"":
                    failure = ConnectionError("ACP stdout EOF")
                    break
                message = self._decode(chunk)
                if message is not None:
                    self._route(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = exc
            logger.warning("ACP stream dispatcher stopped", exc_info=True)
        finally:
            self._finish(failure)

    def _route(self, msg: JsonRpcMessage) -> None:
        if msg.id is not None and msg.method is None:
            waiter = self._pending.pop(msg.id, None)
            if waiter is None:
                if len(self._early_responses) < _EARLY_RESPONSE_MAX:
                    self._early_responses[msg.id] = msg
            elif not waiter.done():
                waiter.set_result(msg)
            return
        session_id = (
            msg.params.get("sessionId") if isinstance(msg.params, dict) else None
        )
        mailbox = (
            self._sessions.get(session_id) if isinstance(session_id, str) else None
        )
        if mailbox is not None:
            self._offer(mailbox, msg)
            return
        callback = (
            self._on_server_request
            if msg.id is not None and msg.method is not None
            else self._on_broadcast
        )
        if callback is not None:
            callback(msg)

    @staticmethod
    def _offer(q: asyncio.Queue[JsonRpcMessage], msg: JsonRpcMessage) -> None:
        if q.full():
            try:
                q.get_nowait()
                logger.warning("ACP session mailbox full; discarding oldest frame")
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass
