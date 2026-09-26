"""ACP connections and independent session turn controllers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import Path

from gideon.integrations.acp import translate
from gideon.integrations.acp.errors import AcpError, AcpTimeoutError
from gideon.integrations.acp.translate import extract_text_chunk  # noqa: F401
from gideon.integrations.acp.turn_decode import TurnDecoder
from gideon.integrations.acp.types import (
    CAP_COMMANDS,
    EVENT_COMPLETE,
    METHOD_AGENT_SWITCHED,
    METHOD_CLEAR_STATUS,
    METHOD_COMMANDS_EXECUTE,
    METHOD_COMPACTION_STATUS,
    METHOD_METADATA,
    METHOD_PROMPT,
    METHOD_REQUEST_PERMISSION,
    METHOD_SESSION_UPDATE,
    OPTION_ALLOW_ONCE,
    STOP_REASON_END_TURN,
    AcpEvent,
    AcpPromptStats,
    JsonRpcMessage,
)

logger = logging.getLogger(__name__)
_STALE_TURN_TIMEOUT = 90.0
_QUEUE_POLL = 1.0
_DEFAULT_PROMPT_TIMEOUT = 7200.0
_MAX_STEERS_PER_TURN = 4
_ACTIONS = {
    METHOD_REQUEST_PERMISSION: "permission",
    METHOD_SESSION_UPDATE: "update",
    METHOD_METADATA: "metadata",
    METHOD_COMPACTION_STATUS: "compaction",
    METHOD_CLEAR_STATUS: "clear",
    METHOD_AGENT_SWITCHED: "agent_switched",
}


def classify_frame(msg: JsonRpcMessage, req_id: int) -> str:
    if msg.method is None and msg.id == req_id:
        return "error" if msg.error else "complete"
    if msg.method is None:
        return "skip"
    return _ACTIONS.get(msg.method, "skip")


def _parse_slash_command(command: str) -> tuple[str, dict]:
    tokens = command.strip().split(maxsplit=1)
    if not tokens:
        return command.lstrip("/"), {}
    return tokens[0].lstrip("/"), {"value": tokens[1]} if len(tokens) == 2 else {}


class AcpSession:
    def __init__(
        self,
        session_id: str,
        queue: asyncio.Queue[JsonRpcMessage],
        *,
        send_request,
        send_response,
        cancel_session,
        is_process_alive,
        dialect=None,
        session_files_dir: Path | None = None,
    ) -> None:
        from gideon.integrations.acp.dialect import DefaultDialect

        self.session_id = session_id
        self._queue = queue
        self._send_request = send_request
        self._send_response = send_response
        self._cancel_session = cancel_session
        self._is_process_alive = is_process_alive
        self._dialect = dialect or DefaultDialect()
        self._turn_lock = asyncio.Lock()
        self._turn_done = asyncio.Event()
        self._closed = self._cancelled = False
        self._tool_call_inputs: dict[str, str] = {}
        self._tool_call_seen: dict[str, translate.SeenToolCall] = {}
        self._offered_options: dict[str, list[dict[str, str]]] = {}
        self.last_prompt_stats = AcpPromptStats()
        self._last_stop_reason = ""
        self._session_files_dir = session_files_dir
        self._jsonl_pos = 0
        self._steer_pull: Callable[[], list[str]] | None = None
        self._steer_pending: list[str] = []
        self._steer_inflight: dict[object, str] = {}
        self._steer_rejected: list[str] = []
        self._steers_delivered = 0

    def close(self) -> None:
        self._closed = True
        self._turn_done.set()

    def steer_capable(self) -> bool:
        return bool(self._dialect.supports_mid_turn_prompt)

    def set_steer_source(self, pull: Callable[[], list[str]] | None) -> bool:
        self._steer_pull = pull if self.steer_capable() else None
        return self._steer_pull is not None

    def undelivered_steers(self) -> list[str]:
        return [*self._steer_pending, *self._steer_rejected]

    def _watch_steer_reply(self, rid: object, fut: asyncio.Future) -> None:
        def settled(reply):
            text = self._steer_inflight.pop(rid, "")
            rejected = reply.cancelled()
            if not rejected:
                try:
                    rejected = bool(getattr(reply.result(), "error", None))
                except Exception:
                    rejected = True
            if rejected and text:
                self._steer_rejected.append(text)
                logger.warning(
                    "ACP session %s steer %s was not accepted", self.session_id, rid
                )

        fut.add_done_callback(settled)

    async def _deliver_steers_at_tool_boundary(self) -> int:
        if self._steer_pull is not None:
            try:
                additions = self._steer_pull() or []
                self._steer_pending.extend(
                    text for text in additions if text and text.strip()
                )
            except Exception:
                logger.debug(
                    "ACP steer source failed for %s", self.session_id, exc_info=True
                )
        available = max(0, _MAX_STEERS_PER_TURN - self._steers_delivered)
        sent = 0
        for _ in range(min(available, len(self._steer_pending))):
            text = self._steer_pending[0]
            request = self._dialect.mid_turn_prompt_request(
                session_id=self.session_id, text=text
            )
            if request is None:
                break
            try:
                request_id, receipt = await self._send_request(
                    request.method, request.params
                )
            except Exception:
                logger.warning(
                    "ACP steer write failed for %s", self.session_id, exc_info=True
                )
                break
            self._steer_inflight[request_id] = text
            self._watch_steer_reply(request_id, receipt)
            del self._steer_pending[0]
            self._steers_delivered += 1
            sent += 1
        return sent

    async def cancel(self) -> None:
        self._cancelled = True
        try:
            await self._cancel_session()
        except Exception:
            logger.debug(
                "ACP cancellation failed for %s", self.session_id, exc_info=True
            )

    async def approve_tool(
        self, request_id: str | int, option_id: str | None = None
    ) -> None:
        choices = self._offered_options.pop(str(request_id), [])
        selected = option_id
        if selected is None:
            selected = (
                self._dialect.select_allow_option_id(choices) or OPTION_ALLOW_ONCE
            )
        await self._send_response(request_id, self._dialect.approve_outcome(selected))

    async def reject_tool(self, request_id: str | int) -> None:
        choices = self._offered_options.pop(str(request_id), [])
        selected = self._dialect.select_reject_option_id(choices)
        await self._send_response(request_id, self._dialect.reject_outcome(selected))

    async def _drain_turn(
        self,
        req_id: int,
        response_future: asyncio.Future[JsonRpcMessage],
        timeout: float,
    ) -> AsyncIterator[JsonRpcMessage]:
        deadline = time.monotonic() + timeout
        received_at: float | None = None
        reader: asyncio.Task | None = None
        try:
            while not (self._closed or self._cancelled):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                message = None
                if reader is not None and reader.done():
                    message, reader = reader.result(), None
                elif reader is None:
                    try:
                        message = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                if message is not None:
                    if message.method == "_router/closed":
                        if response_future.done() and not response_future.cancelled():
                            try:
                                terminal = response_future.result()
                            except Exception:
                                return
                            yield terminal
                        return
                    received_at = time.monotonic()
                    yield message
                    continue
                if response_future.done():
                    if response_future.cancelled():
                        return
                    try:
                        terminal = response_future.result()
                    except Exception:
                        logger.debug(
                            "ACP terminal response failed for %s",
                            self.session_id,
                            exc_info=True,
                        )
                        return
                    yield terminal
                    return
                if reader is None:
                    reader = asyncio.ensure_future(self._queue.get())
                ready, _ = await asyncio.wait(
                    (reader, response_future),
                    timeout=min(remaining, _QUEUE_POLL),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not ready:
                    if not self._is_process_alive():
                        return
                    if (
                        received_at is not None
                        and time.monotonic() - received_at > _STALE_TURN_TIMEOUT
                    ):
                        return
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)

    def _begin_turn(self) -> None:
        self._cancelled = False
        self._turn_done.clear()
        self._last_stop_reason = ""

    def _reset_turn_data(self) -> None:
        self.last_prompt_stats = AcpPromptStats(
            context_pct=self.last_prompt_stats.context_pct
        )
        for cache in (
            self._tool_call_inputs,
            self._tool_call_seen,
            self._offered_options,
            self._steer_pending,
            self._steer_inflight,
            self._steer_rejected,
        ):
            cache.clear()
        self._steers_delivered = 0

    async def _invoke(
        self, method: str, payload: dict, timeout: float, *, command: bool = False
    ):
        async with self._turn_lock:
            if self._closed:
                raise AcpError("ACP session is closed")
            self._begin_turn()
            try:
                request_id, future = await self._send_request(
                    method, {"sessionId": self.session_id, **payload}
                )
                async for event in self._dispatch_frames(
                    request_id,
                    future,
                    timeout,
                    extract_agent_from_result=command,
                    method=method,
                ):
                    yield event
            finally:
                self._turn_done.set()

    async def stream_events(
        self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        async for event in self._invoke(
            METHOD_PROMPT, {"prompt": translate.encode_prompt_content(message)}, timeout
        ):
            yield event

    async def stream_command(
        self, command: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        name, arguments = _parse_slash_command(command)
        async for event in self._invoke(
            METHOD_COMMANDS_EXECUTE,
            {"command": {"command": name, "args": arguments}},
            timeout,
            command=True,
        ):
            yield event

    async def _dispatch_frames(
        self,
        req_id: int,
        response_future: asyncio.Future[JsonRpcMessage],
        timeout: float,
        *,
        extract_agent_from_result: bool = False,
        method: str = "",
    ) -> AsyncIterator[AcpEvent]:
        self._reset_turn_data()
        decoder = TurnDecoder(self, method=method, command=extract_agent_from_result)
        async for message in self._drain_turn(req_id, response_future, timeout):
            for event in decoder.accept(classify_frame(message, req_id), message):
                if event.kind == EVENT_COMPLETE:
                    self._last_stop_reason = event.stop_reason
                    self._turn_done.set()
                yield event
            if decoder.finished:
                return
            if decoder.tool_boundary:
                await self._deliver_steers_at_tool_boundary()
        self._last_stop_reason = ""
        self._turn_done.set()
        if decoder.stale_eligible:
            yield AcpEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_END_TURN)
            return
        raise AcpTimeoutError()

    async def wait_turn_done(self, timeout: float) -> str:
        await asyncio.wait_for(self._turn_done.wait(), timeout)
        return self._last_stop_reason

    def has_active_turn(self) -> bool:
        return self._turn_lock.locked() and not self._turn_done.is_set()

    def context_usage_pct(self) -> float | None:
        return self.last_prompt_stats.context_pct

    def _read_new_tool_results(self) -> list[AcpEvent]:
        if self._session_files_dir is None:
            return []
        path = self._session_files_dir / (self.session_id + ".jsonl")
        batch, position = translate.read_new_tool_results(path, self._jsonl_pos)
        self._jsonl_pos = position
        return batch


class AcpConnection:
    def __init__(self, proc, router, *, dialect=None, transport=None) -> None:
        self._proc = proc
        self._router = router
        self._dialect = dialect
        self._transport = transport
        self._next_id = 0
        self._sessions: dict[str, AcpSession] = {}
        self._agent_capabilities: dict = {}
        self._last_session_new_snapshot: dict = {}

    @classmethod
    async def spawn(
        cls,
        *,
        command: list[str],
        work_dir,
        dialect=None,
        sandbox_mode: str = "auto",
        extra_env: dict | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
    ) -> AcpConnection:
        from gideon.integrations.acp.reader import FrameRouter
        from gideon.integrations.acp.transport import AcpProcess

        process = AcpProcess(
            command=command,
            work_dir=work_dir,
            sandbox_mode=sandbox_mode,
            extra_env=extra_env,
            session_key=session_key,
            channel_id=channel_id,
        )
        await process.spawn()
        router = FrameRouter(process.readline)
        connection = cls(None, router, dialect=dialect, transport=process)
        router.start()
        return connection

    def _req_id(self) -> int:
        self._next_id = self._next_id + 1
        return self._next_id

    def is_process_alive(self) -> bool:
        if self._transport is None:
            return self._proc is not None and self._proc.returncode is None
        return self._transport.is_alive()

    async def _write(self, obj: dict) -> None:
        frame = json.dumps(obj) + "\n"
        if self._transport is None:
            self._proc.stdin.write(frame.encode())
            await self._proc.stdin.drain()
        else:
            await self._transport.write(frame)

    async def send_request(self, method: str, params: dict):
        request_id = self._req_id()
        response = self._router.expect(request_id)
        try:
            await self._write(
                dict(jsonrpc="2.0", id=request_id, method=method, params=params)
            )
        except BaseException:
            response.cancel()
            raise
        return request_id, response

    async def send_response(self, req_id, result: dict) -> None:
        await self._write(dict(jsonrpc="2.0", id=req_id, result=result))

    async def request(self, method: str, params: dict, *, timeout: float = 60.0):
        _, response = await self.send_request(method, params)
        return await asyncio.wait_for(response, timeout)

    async def initialize(self, params: dict, *, timeout: float = 240.0) -> dict:
        response = await self.request("initialize", params, timeout=timeout)
        result = response.result if isinstance(response.result, dict) else {}
        self._agent_capabilities = result.get("agentCapabilities") or {}
        return self._agent_capabilities

    async def _cancel_bound_session(self, session_id: str) -> None:
        await self._write(
            dict(
                jsonrpc="2.0", method="session/cancel", params={"sessionId": session_id}
            )
        )

    def _bind_session(self, sid: str, session_files_dir=None) -> AcpSession:
        session = AcpSession(
            sid,
            self._router.register_session(sid),
            send_request=self.send_request,
            send_response=self.send_response,
            cancel_session=partial(self._cancel_bound_session, sid),
            is_process_alive=self.is_process_alive,
            dialect=self._dialect,
            session_files_dir=session_files_dir,
        )
        self._sessions[sid] = session
        return session

    async def new_session(
        self, params: dict, *, timeout: float = 60.0, session_files_dir=None
    ) -> AcpSession:
        response = await self.request("session/new", params, timeout=timeout)
        result = response.result if isinstance(response.result, dict) else {}
        if not result.get("sessionId"):
            raise RuntimeError(
                f"session/new returned no sessionId (result={response.result!r})"
            )
        self._last_session_new_snapshot = dict(result)
        return self._bind_session(result["sessionId"], session_files_dir)

    async def load_session(
        self,
        params: dict,
        *,
        session_id: str,
        timeout: float = 60.0,
        session_files_dir=None,
    ) -> AcpSession | None:
        response = await self.request("session/load", params, timeout=timeout)
        if isinstance(response.result, dict) and "modes" in response.result:
            self._last_session_new_snapshot = dict(response.result)
            return self._bind_session(session_id, session_files_dir)
        return None

    async def drain_init_notifications(self, *, duration: float = 10.0) -> None:
        until = time.monotonic() + min(3.0, duration)
        while time.monotonic() < until:
            await asyncio.sleep(0.05)
            if not self.is_process_alive():
                break

    async def wait_for_session_frame(
        self,
        session_id: str,
        *,
        method: str,
        terminal_types: tuple[str, ...],
        timeout: float,
        also_track: tuple[str, ...] = (),
    ) -> dict:
        mailbox = self._router.register_session(session_id)
        until = time.monotonic() + timeout
        while (remaining := until - time.monotonic()) > 0:
            try:
                frame = await asyncio.wait_for(
                    mailbox.get(), min(_QUEUE_POLL, remaining)
                )
            except TimeoutError:
                if self.is_process_alive():
                    continue
                break
            if frame.method == "_router/closed":
                break
            if frame.method in also_track and frame.method == METHOD_METADATA:
                session = self._sessions.get(session_id)
                percent = translate.extract_context_pct(frame)
                if session is not None and percent is not None:
                    session.last_prompt_stats.context_pct = percent
            if frame.method != method:
                continue
            params = frame.params or {}
            status = params.get("status", {})
            name = status.get("type", "") if isinstance(status, dict) else str(status)
            if name in terminal_types:
                return {"type": name, "summary": params.get("summary", "")}
        return {"type": "timeout"}

    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def agent_capabilities(self) -> dict:
        return self._agent_capabilities

    @property
    def supports_native_commands(self) -> bool:
        return bool(self.agent_capabilities.get(CAP_COMMANDS, False))

    @property
    def last_session_new_snapshot(self) -> dict:
        return self._last_session_new_snapshot

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()
        self._router.unregister_session(session_id)

    async def close(self) -> None:
        sessions, self._sessions = self._sessions, {}
        for session in sessions.values():
            session.close()
        try:
            await self._router.close()
        finally:
            if self._transport is not None:
                try:
                    await self._transport.kill(force=True)
                finally:
                    self._transport.teardown()
