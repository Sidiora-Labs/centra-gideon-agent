"""ACP connections and independent session turn controllers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from functools import partial
from pathlib import Path

from gideon.core.turn_streams import closing_stream
from gideon.integrations.acp import translate
from gideon.integrations.acp.errors import AcpError, AcpProcessDied, AcpTimeoutError
from gideon.integrations.acp.translate import extract_text_chunk  # noqa: F401
from gideon.integrations.acp.turn_decode import TurnDecoder
from gideon.integrations.acp.types import (
    CAP_COMMANDS,
    EVENT_CARRIED_ON,
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
    STOP_REASON_CANCELLED,
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
    "elicitation/create": "elicitation",
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
    return _ACTIONS.get(msg.method, "request" if msg.id is not None else "skip")


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
        send_error=None,
        is_process_alive,
        dialect=None,
        session_files_dir: Path | None = None,
        session_key: str | None = None,
    ) -> None:
        from gideon.integrations.acp.dialect import DefaultDialect

        self.session_id = session_id
        self._session_key = session_key
        self._permission_answers: dict[str, dict] = {}
        self._queue = queue
        self._send_request = send_request
        self._send_response = send_response
        self._send_error = send_error
        self._question_handler = None
        self._questions_enabled = True
        self._questions_permitted = True
        self._question_tasks: dict[tuple[type, object], asyncio.Task[None]] = {}
        self._question_answers: set[tuple[type, object]] = set()
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
        self._owed_answer: asyncio.Future | None = None
        self._refusal_answers: dict[str, dict] = {}
        self._asked_steps: dict[str, str] = {}
        self._declined_steps: list[str] = []
        self._carry_ons = 0
        self._answered_permissions: set[str] = set()
        self._session_files_dir = session_files_dir
        self._jsonl_pos = 0
        self._steer_pull: Callable[[], list[str]] | None = None
        self._steer_pending: list[str] = []
        self._steer_inflight: dict[object, str] = {}
        self._steer_rejected: list[str] = []
        self._steers_delivered = 0

    def close(self) -> None:
        self._closed = True
        for task in self._question_tasks.values():
            task.cancel()
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
            if not rejected and text:
                from gideon.engine.steering import acknowledge_steering

                acknowledge_steering(text)
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

    def set_question_handler(self, handler) -> None:
        self._question_handler = handler
        self._questions_enabled = callable(handler)

    async def _answer_question(self, message) -> None:
        from .elicitation import CANCEL, answer_owner_form, attended_owner

        identity = (type(message.id), message.id)
        result = dict(CANCEL)
        try:
            if (
                self._questions_enabled
                and self._questions_permitted
                and not self._cancelled
                and attended_owner(self._session_key)
            ):
                if self._question_handler is None:
                    result = await answer_owner_form(
                        message.params,
                        session_key=self._session_key,
                        call_id="acp-question:" + uuid.uuid4().hex,
                    )
                else:
                    result = await self._question_handler(message.params)
        except asyncio.CancelledError:
            result = dict(CANCEL)
        except Exception:
            logger.warning("ACP owner question failed", exc_info=True)
        finally:
            if identity not in self._question_answers:
                self._question_answers.add(identity)
                if self._cancelled or self._closed:
                    result = dict(CANCEL)
                try:
                    await self._send_response(message.id, result)
                except Exception:
                    logger.debug(
                        "ACP question response could not be sent", exc_info=True
                    )
            self._question_tasks.pop(identity, None)

    def _start_question(self, message) -> None:
        identity = (type(message.id), message.id)
        if identity in self._question_tasks or identity in self._question_answers:
            return
        self._question_tasks[identity] = asyncio.create_task(
            self._answer_question(message)
        )

    async def _cancel_questions(self) -> None:
        tasks = list(self._question_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for identity in list(self._question_tasks):
            self._question_tasks.pop(identity, None)
            if identity not in self._question_answers:
                self._question_answers.add(identity)
                try:
                    await self._send_response(identity[1], {"action": "cancel"})
                except Exception:
                    logger.debug(
                        "ACP cancelled question response failed", exc_info=True
                    )

    async def cancel(self) -> None:
        self._cancelled = True
        await self._cancel_questions()
        for request_id in list(self._offered_options):
            try:
                await self.reject_tool(request_id)
            except Exception:
                logger.debug("ACP pending refusal cleanup failed", exc_info=True)
        try:
            await self._cancel_session()
        except Exception:
            logger.debug(
                "ACP cancellation failed for %s", self.session_id, exc_info=True
            )

    async def approve_tool(
        self, request_id: str | int, option_id: str | None = None
    ) -> None:
        if self._cancelled:
            await self.reject_tool(request_id)
            return
        if str(request_id) in self._answered_permissions:
            return
        self._answered_permissions.add(str(request_id))
        choices = self._offered_options.pop(str(request_id), [])
        selected = option_id
        if selected is None:
            selected = (
                self._dialect.select_allow_option_id(choices) or OPTION_ALLOW_ONCE
            )
        allowed = next((row for row in choices if row.get("id") == selected), None)
        if allowed is None or not str(
            allowed.get("kind") or allowed.get("id") or ""
        ).startswith("allow"):
            self._answered_permissions.discard(str(request_id))
            self._offered_options[str(request_id)] = choices
            await self.reject_tool(request_id)
            return
        answer = self._dialect.approve_outcome(selected)
        await self._send_response(request_id, answer)
        self._record_permission_answer(request_id, choices, answer)

    def permission_answer(self, request_id: str | int) -> dict | None:
        from copy import deepcopy

        return deepcopy(self._permission_answers.get(str(request_id)))

    def _record_permission_answer(self, request_id, offered, answer) -> None:
        from copy import deepcopy

        from gideon.security.sel import redact_event, sel

        record = {
            "remote_session_id": self.session_id,
            "offered": deepcopy(offered),
            "sent": deepcopy(answer),
        }
        self._permission_answers[str(request_id)] = record
        try:
            safe = redact_event({"metadata": record})["metadata"]
            sel().log_tool_invocation(
                session_key=self._session_key or "",
                source="acp:permission",
                tool_name="permission_response",
                outcome="sent",
                request_id=request_id,
                metadata=safe,
            )
        except Exception:
            logger.exception("ACP permission response audit failed")

    def deny_outcome(self, request_id: str | int) -> dict:
        offered = self._offered_options.get(str(request_id), [])
        ends = self._dialect.deny_ends_turn(offered)
        consequence = (
            "declines" if not ends else "carries_on" if self._carry_ons < 4 else "ends"
        )
        return {
            "ends_turn": ends,
            "consequence": consequence,
            "offered": [dict(row) for row in offered],
        }

    def refusal_answer(self, request_id: str | int) -> dict | None:
        answer = self._refusal_answers.get(str(request_id))
        return dict(answer) if answer else None

    async def reject_tool(self, request_id: str | int) -> None:
        identity = str(request_id)
        if identity in self._answered_permissions:
            return
        self._answered_permissions.add(identity)
        choices = self._offered_options.pop(identity, [])
        selected = (
            "" if self._cancelled else self._dialect.select_reject_option_id(choices)
        )
        if selected:
            self._refusal_answers[identity] = next(
                dict(row) for row in choices if row.get("id") == selected
            )
        if not self._cancelled and self._dialect.deny_ends_turn(choices):
            self._declined_steps.append(
                self._asked_steps.get(identity, "the requested step")
            )
        answer = self._dialect.reject_outcome(selected)
        await self._send_response(request_id, answer)
        self._record_permission_answer(request_id, choices, answer)

    async def settle_owed_answer(self, timeout: float = 2.0) -> bool:
        future = self._owed_answer
        if future is None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout)
        except TimeoutError:
            return False
        except Exception:
            self._owed_answer = None
            return False
        self._owed_answer = None
        while not self._queue.empty():
            self._queue.get_nowait()
        return True

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
            while not self._closed:
                if self._cancelled:
                    try:
                        terminal = await asyncio.wait_for(
                            asyncio.shield(response_future), 2.0
                        )
                    except (TimeoutError, ConnectionError):
                        return
                    yield terminal
                    return
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
                    yield response_future.result()
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
        self._refusal_answers.clear()
        self._permission_answers.clear()
        self._asked_steps.clear()
        self._declined_steps.clear()
        self._carry_ons = 0
        self._answered_permissions.clear()
        self._question_answers.clear()

    async def _invoke(
        self, method: str, payload: dict, timeout: float, *, command: bool = False
    ):
        await self._turn_lock.acquire()
        held = True
        try:
            if self._closed:
                raise AcpError("ACP session is closed")
            if not await self.settle_owed_answer():
                raise AcpProcessDied(
                    "The agent has not answered the turn it was told to stop"
                )
            self._begin_turn()
            request_id, future = await self._send_request(
                method, {"sessionId": self.session_id, **payload}
            )
            frames = self._dispatch_frames(
                request_id,
                future,
                timeout,
                extract_agent_from_result=command,
                method=method,
            )
            async with closing_stream(frames):
                async for event in frames:
                    if event.kind == EVENT_COMPLETE and held:
                        await frames.aclose()
                        held = False
                        self._turn_lock.release()
                    yield event
        finally:
            if held:
                self._turn_done.set()
                self._turn_lock.release()

    def stream_events(
        self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        return self._invoke(
            METHOD_PROMPT, {"prompt": translate.encode_prompt_content(message)}, timeout
        )

    def stream_command(
        self, command: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        name, arguments = _parse_slash_command(command)
        return self._invoke(
            METHOD_COMMANDS_EXECUTE,
            {"command": {"command": name, "args": arguments}},
            timeout,
            command=True,
        )

    async def _dispatch_frames(
        self,
        req_id: int,
        response_future: asyncio.Future[JsonRpcMessage],
        timeout: float,
        *,
        extract_agent_from_result: bool = False,
        method: str = "",
    ) -> AsyncGenerator[AcpEvent, None]:
        self._reset_turn_data()
        deadline = time.monotonic() + timeout
        try:
            while True:
                decoder = TurnDecoder(
                    self, method=method, command=extract_agent_from_result
                )
                drain = self._drain_turn(
                    req_id, response_future, max(0.0, deadline - time.monotonic())
                )
                carry_on = False
                async with closing_stream(drain):
                    async for message in drain:
                        action = classify_frame(message, req_id)
                        if action == "elicitation":
                            self._start_question(message)
                            continue
                        if action == "request":
                            identity = (type(message.id), message.id)
                            if (
                                identity not in self._question_answers
                                and identity not in self._question_tasks
                            ):
                                self._question_answers.add(identity)
                                if self._send_error is not None:
                                    await self._send_error(
                                        message.id, -32601, "Method not found"
                                    )
                            continue
                        for event in decoder.accept(action, message):
                            if event.kind == EVENT_COMPLETE:
                                carry_on = (
                                    method == METHOD_PROMPT
                                    and event.stop_reason == STOP_REASON_CANCELLED
                                    and not self._cancelled
                                    and bool(self._declined_steps)
                                    and self._carry_ons < 4
                                    and self._is_process_alive()
                                    and time.monotonic() < deadline
                                )
                                if carry_on:
                                    break
                                if self._cancelled:
                                    event.stop_reason = STOP_REASON_CANCELLED
                                self._last_stop_reason = event.stop_reason
                                self._turn_done.set()
                            yield event
                        if decoder.finished:
                            break
                        if decoder.tool_boundary:
                            await self._deliver_steers_at_tool_boundary()
                if carry_on:
                    self._carry_ons += 1
                    steps = list(self._declined_steps)
                    self._declined_steps.clear()
                    note = (
                        "The user refused these steps, which did not run: "
                        + "; ".join(steps)
                        + ". Continue the original task without these steps, using what is already "
                        "available. State any resulting limits and do not request those steps again."
                    )
                    req_id, response_future = await self._send_request(
                        METHOD_PROMPT,
                        {
                            "sessionId": self.session_id,
                            "prompt": [{"type": "text", "text": note}],
                        },
                    )
                    yield AcpEvent(
                        kind=EVENT_CARRIED_ON,
                        text="The denied step ended the agent’s turn. Continuing the same task without it.",
                    )
                    continue
                if decoder.finished:
                    return
                if self._cancelled:
                    self._last_stop_reason = STOP_REASON_CANCELLED
                    self._turn_done.set()
                    yield AcpEvent(
                        kind=EVENT_COMPLETE, stop_reason=STOP_REASON_CANCELLED
                    )
                    return
                if not self._is_process_alive():
                    raise AcpProcessDied(
                        "The agent process ended before answering its prompt"
                    )
                raise AcpTimeoutError()
        finally:
            await self._cancel_questions()
            if not response_future.done():
                self._owed_answer = response_future
                await self.cancel()
                await self.settle_owed_answer()

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
    def __init__(
        self,
        proc,
        router,
        *,
        dialect=None,
        transport=None,
        session_meta=None,
        session_key=None,
    ) -> None:
        from gideon.integrations.acp.options import session_metadata

        self._session_meta = session_metadata(session_meta)
        self._session_key = session_key
        self._proc = proc
        self._router = router
        self._dialect = dialect
        self._transport = transport
        self._next_id = 0
        self._sessions: dict[str, AcpSession] = {}
        self._agent_capabilities: dict = {}
        self._last_session_new_snapshot: dict = {}
        self._server_responses: set[tuple[type, object]] = set()
        self._server_tasks: set[asyncio.Task[None]] = set()
        self._router._on_server_request = self._route_server_request

    def _route_server_request(self, message) -> None:
        if message.method == "elicitation/create":
            session_id = (
                message.params.get("sessionId")
                if isinstance(message.params, dict)
                else None
            )
            active = [
                session
                for session in self._sessions.values()
                if session.has_active_turn()
            ]
            session = (
                self._sessions.get(session_id)
                if isinstance(session_id, str)
                else (active[0] if len(active) == 1 else None)
            )
            if session is not None and session.has_active_turn():
                session._queue.put_nowait(message)
                return
            result = {"action": "cancel"}
        else:
            result = None
        identity = (type(message.id), message.id)
        if identity in self._server_responses:
            return
        self._server_responses.add(identity)

        async def respond():
            try:
                if result is None:
                    await self.send_error(message.id, -32601, "Method not found")
                else:
                    await self.send_response(message.id, result)
            except Exception:
                logger.debug("ACP server request response failed", exc_info=True)

        task = asyncio.create_task(respond())
        self._server_tasks.add(task)
        task.add_done_callback(self._server_tasks.discard)

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
        session_meta: dict | None = None,
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
        connection = cls(
            None,
            router,
            dialect=dialect,
            transport=process,
            session_meta=session_meta,
            session_key=session_key,
        )
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

    async def send_error(self, req_id, code: int, message: str) -> None:
        await self._write(
            dict(jsonrpc="2.0", id=req_id, error={"code": code, "message": message})
        )

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

    def _bind_session(
        self, sid: str, session_files_dir=None, audit_session_key=None
    ) -> AcpSession:
        session = AcpSession(
            sid,
            self._router.register_session(sid),
            send_request=self.send_request,
            send_response=self.send_response,
            send_error=self.send_error,
            cancel_session=partial(self._cancel_bound_session, sid),
            is_process_alive=self.is_process_alive,
            dialect=self._dialect,
            session_files_dir=session_files_dir,
            session_key=(
                audit_session_key
                if audit_session_key is not None
                else self._session_key
            ),
        )
        self._sessions[sid] = session
        return session

    def _with_session_meta(self, params: dict) -> dict:
        from gideon.integrations.acp.options import session_metadata

        meta = {**self._session_meta, **session_metadata(params.get("_meta"))}
        return {**params, "_meta": session_metadata(meta)} if meta else dict(params)

    async def new_session(
        self,
        params: dict,
        *,
        timeout: float = 60.0,
        session_files_dir=None,
        audit_session_key=None,
    ) -> AcpSession:
        response = await self.request(
            "session/new", self._with_session_meta(params), timeout=timeout
        )
        if response.error:
            from gideon.security.security import redact_field

            raise AcpError(
                "The agent could not open a session: "
                + redact_field(str(response.error))[:500]
            )
        result = response.result if isinstance(response.result, dict) else {}
        if not result.get("sessionId"):
            raise RuntimeError(
                "The agent did not return a session ID. Check its configuration and sign-in, then retry."
            )
        self._last_session_new_snapshot = dict(result)
        return self._bind_session(
            result["sessionId"], session_files_dir, audit_session_key
        )

    async def load_session(
        self,
        params: dict,
        *,
        session_id: str,
        timeout: float = 60.0,
        session_files_dir=None,
    ) -> AcpSession | None:
        response = await self.request(
            "session/load", self._with_session_meta(params), timeout=timeout
        )
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
            await session._cancel_questions()
            session.close()
        self._router.unregister_session(session_id)

    async def close(self) -> None:
        sessions, self._sessions = self._sessions, {}
        for session in sessions.values():
            await session._cancel_questions()
            session.close()
        try:
            if self._server_tasks:
                await asyncio.gather(*self._server_tasks, return_exceptions=True)
            await self._router.close()
        finally:
            if self._transport is not None:
                try:
                    await self._transport.kill(force=True)
                finally:
                    self._transport.teardown()
