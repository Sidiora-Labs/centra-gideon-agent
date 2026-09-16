"""A single-session ACP client assembled from transport and session controllers."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from gideon.integrations.acp import translate
from gideon.integrations.acp.errors import (
    AcpCommandsUnsupported,
    AcpError,
    AcpPermissionNeeded,
    AcpProcessDied,
    AcpTimeoutError,
)
from gideon.integrations.acp.transport import (
    AcpProcess,
    _direct_children,
    _get_child_pids,
    _get_start_time,
    _is_our_child,
    _kill_escaped_children,
    _resolve_ssh_auth_sock,
)
from gideon.integrations.acp.types import (
    CAP_LOAD_SESSION,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    METHOD_COMPACTION_STATUS,
    METHOD_METADATA,
    AcpEvent,
    AcpPromptStats,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from gideon.integrations.acp.dialect import ACPDialect
    from gideon.integrations.acp.session import AcpConnection, AcpSession

logger = logging.getLogger(__name__)
CLIENT_NAME = "gideon"
CLIENT_VERSION = "0.1.3"
PROTOCOL_VERSION = "2025-08-22"
DEFAULT_MODEL = "auto"
_INIT_TIMEOUT = 240.0
_DRAIN_DURATION = 10.0
_DEFAULT_PROMPT_TIMEOUT = 7200.0
_STALE_TURN_TIMEOUT = 90.0
_ACP_TRACE = os.environ.get("GIDEON_ACP_TRACE") == "1"


def _acp_trace(direction: str, text: str) -> None:
    if _ACP_TRACE:
        logger.info("ACP-TRACE %s %s", direction, text[:600])


def _make_unified_diff(old: str, new: str, path: str, max_len: int = 6000) -> str:
    return translate.make_unified_diff(old, new, path, max_len)


class _TransportField:
    def __init__(self, target: str, *, convert=None, writable: bool = True):
        self.target = target
        self.convert = convert
        self.writable = writable

    def __get__(self, owner, owner_type=None):
        return self if owner is None else getattr(owner._transport, self.target)

    def __set__(self, owner, value):
        if not self.writable:
            raise AttributeError(self.target)
        setattr(
            owner._transport,
            self.target,
            self.convert(value) if self.convert else value,
        )


class AcpClient:
    _work_dir = _TransportField("_work_dir", convert=Path)
    _process = _TransportField("_process")
    _pid = _TransportField("_pid")
    _last_activity = _TransportField("_last_activity")
    _child_pids = _TransportField("_child_pids")
    _sandbox_handle = _TransportField("_sandbox_handle")
    _stderr_lines = _TransportField("_stderr_lines", writable=False)
    _start_time = _TransportField("_start_time")

    def __init__(
        self,
        work_dir: str | Path | None = None,
        model: str | None = None,
        agent: str = CLIENT_NAME,
        sandbox_mode: str = "auto",
        sandbox: str = "none",
        session_key: str | None = None,
        channel_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        command: list[str] | None = None,
        session_files_dir: Path | None = None,
        dialect: ACPDialect | None = None,
        mode: str | None = None,
        reasoning_effort: str | None = None,
        unattended: bool = False,
    ):
        from gideon.core.config.loader import workspace_root
        from gideon.integrations.acp.dialect import DefaultDialect

        self._dialect = dialect or DefaultDialect()
        self._model, self._agent = model or DEFAULT_MODEL, agent
        self._unattended = bool(unattended)
        self._session_key, self._channel_id = session_key, channel_id
        self._mode = self._authority_mode(mode)
        self._reasoning_effort = reasoning_effort or ""
        self._sandbox_mode, self._sandbox = sandbox_mode, sandbox or "none"
        self._extra_env = dict(extra_env or {})
        self._command = list(command) if command else None
        self._session_files_dir = Path(session_files_dir) if session_files_dir else None
        self._transport = AcpProcess(
            command=self._command or [],
            work_dir=Path(work_dir) if work_dir else workspace_root(),
            sandbox_mode=self._sandbox_mode,
            sandbox=self._sandbox,
            extra_env=self._extra_env or None,
            session_key=session_key,
            channel_id=channel_id,
        )
        self._connection: AcpConnection | None = None
        self._session: AcpSession | None = None
        self._session_id: str | None = None
        self._resume_session_id: str | None = None
        self._resumed = self._can_load_session = self._can_execute_commands = False
        self._agent_capabilities: dict[str, object] = {}
        self._session_new_snapshot: dict[str, object] = {}
        self.last_prompt_stats = AcpPromptStats()
        self._last_stop_reason = ""
        self._steer_pull: Callable[[], list[str]] | None = None
        self._ready_lock = asyncio.Lock()

    def _core_mcp_servers(self) -> list[dict[str, Any]]:
        from gideon.integrations.acp.mcp_servers import core_mcp_servers

        return core_mcp_servers(session_key=self._session_key)

    @property
    def is_ready(self) -> bool:
        return self._process is not None and self._session_id is not None

    @property
    def session_snapshot(self) -> dict[str, object]:
        return self._session_new_snapshot

    @property
    def exit_code(self) -> int | None:
        return self._transport.exit_code

    @property
    def resumed(self) -> bool:
        return self._resumed

    @property
    def supports_native_commands(self) -> bool:
        return self._can_execute_commands

    def is_process_alive(self) -> bool:
        return self._transport.is_alive()

    def is_responsive(self, stale_threshold: float = 600.0) -> bool:
        return self._transport.is_responsive(stale_threshold)

    def touch_activity(self) -> None:
        self._transport.touch()

    def set_resume_session_id(self, sid: str) -> None:
        self._resume_session_id = sid

    def rekey(self, session_key: str, channel_id: str | None = None) -> None:
        self._session_key = self._transport._session_key = session_key
        self._channel_id = self._transport._channel_id = channel_id
        self.touch_activity()

    def _watch_dialect_reply(self, method: str, params: dict, rid, fut) -> None:
        def observed(receipt):
            if receipt.cancelled():
                return
            try:
                error = getattr(receipt.result(), "error", None)
            except Exception:
                return
            if error:
                logger.warning(
                    "ACP adapter REJECTED %s (%s=%r) rid=%s: %r; setting did not apply",
                    method,
                    params.get("configId") or "value",
                    params.get("value"),
                    rid,
                    error,
                )

        fut.add_done_callback(observed)

    async def _send_dialect_request(self, req) -> None:
        if self._connection is None or req is None:
            return
        request_id, receipt = await self._connection.send_request(
            req.method, req.params
        )
        self._watch_dialect_reply(req.method, req.params, request_id, receipt)

    def _require_session_id(self, operation: str) -> str:
        if not self._session_id:
            raise AcpError(f"Cannot {operation} before session is initialized")
        return self._session_id

    async def set_model(self, model_id: str) -> None:
        sid = self._require_session_id("set model")
        await self._send_dialect_request(
            self._dialect.set_model_request(
                session_id=sid, model=model_id, default_model="\0"
            )
        )
        self._model = model_id

    async def set_agent(self, agent: str) -> None:
        if agent and agent != self._agent:
            sid = self._require_session_id("set agent")
            await self._send_dialect_request(
                self._dialect.activate_agent_request(session_id=sid, agent=agent)
            )
            self._agent = agent

    def _authority_mode(self, mode: str | None) -> str:
        from gideon.integrations.acp.permission_authority import sanitize_mode

        decision = sanitize_mode(mode, unattended=self._unattended)
        operation = outcome = resources = ""
        identity = f"session={getattr(self, '_session_key', None) or '-'}"
        if decision.downgraded:
            logger.warning("ACP permission mode clamped: %s", decision.reason)
            operation, outcome = "clamped_to_host_authority", "downgraded"
            resources = (
                f"{identity} requested={decision.requested} effective={decision.mode}"
            )
        elif decision.reason and decision.requested:
            logger.info("ACP permission mode allowed unattended: %s", decision.reason)
            operation, outcome = "unattended_auto_approve", "allowed"
            resources = f"{identity} mode={decision.mode}"
        if operation:
            try:
                from gideon.security.sel import sel

                sel().log_api_access(
                    caller="acp:permission_authority",
                    operation="mode_change:" + operation,
                    outcome=outcome,
                    resources=resources,
                )
            except Exception:
                logger.warning(
                    "SEL audit failed for ACP permission mode", exc_info=True
                )
        return decision.mode

    async def set_mode(self, mode: str) -> None:
        effective = self._authority_mode(mode)
        if effective and effective != self._mode:
            sid = self._require_session_id("set mode")
            await self._send_dialect_request(
                self._dialect.set_mode_request(session_id=sid, mode=effective)
            )
            self._mode = effective

    async def set_effort(self, effort: str) -> None:
        if effort != self._reasoning_effort:
            sid = self._require_session_id("set effort")
            await self._send_dialect_request(
                self._dialect.set_effort_request(session_id=sid, effort=effort)
            )
            self._reasoning_effort = effort

    async def ensure_ready(self) -> None:
        self._work_dir.mkdir(parents=True, exist_ok=True)
        async with self._ready_lock:
            if (
                self._connection is not None
                and self.is_process_alive()
                and self._session_id
            ):
                return
            for attempt in range(2):
                try:
                    if (
                        self._transport.process is not None
                        and self.exit_code is not None
                    ):
                        await self._reset()
                    if self._connection is None:
                        await self._open_connection()
                    await self._initialize_session()
                    try:
                        await self._transport.snapshot_process_tree()
                    except Exception:
                        logger.warning(
                            "ACP process snapshot unavailable", exc_info=True
                        )
                    return
                except asyncio.CancelledError:
                    await self._teardown()
                    raise
                except (AcpTimeoutError, AcpError):
                    await self._teardown()
                    if attempt:
                        raise
                    logger.warning("Retrying ACP initialization with a fresh process")

    async def _open_connection(self) -> None:
        from gideon.integrations.acp.reader import FrameRouter
        from gideon.integrations.acp.session import AcpConnection

        await self._transport.spawn()
        router = FrameRouter(self._transport.readline)
        self._connection = AcpConnection(
            None, router, dialect=self._dialect, transport=self._transport
        )
        router.start()

    def _session_parameters(self) -> dict:
        return {"cwd": str(self._work_dir), "mcpServers": self._core_mcp_servers()}

    async def _try_resume(self, session_id: str) -> bool:
        params = {**self._session_parameters(), "sessionId": session_id}
        if self._session_files_dir is not None:
            hint = self._session_files_dir / (session_id + ".json")
            if hint.exists():
                params["_meta"] = {"_vendor.dev/session_file": str(hint)}
        try:
            session = await self._connection.load_session(
                params,
                session_id=session_id,
                timeout=_INIT_TIMEOUT,
                session_files_dir=self._session_files_dir,
            )
        except (AcpError, AcpTimeoutError):
            logger.info("ACP resume rejected for %s; opening a new session", session_id)
            return False
        if session is None:
            return False
        self._session, self._session_id, self._resumed = session, session_id, True
        return True

    async def _new_session(self) -> None:
        session = await self._connection.new_session(
            self._session_parameters(),
            timeout=_INIT_TIMEOUT,
            session_files_dir=self._session_files_dir,
        )
        self._session, self._session_id = session, session.session_id
        self._session_new_snapshot = dict(
            self._connection.last_session_new_snapshot or {}
        )

    async def _configure_session(self) -> None:
        dialect, sid = self._dialect, self._session_id
        operations = (
            (dialect.activate_agent_request, {"agent": self._agent}),
            (
                dialect.set_model_request,
                {"model": self._model, "default_model": DEFAULT_MODEL},
            ),
            (dialect.set_mode_request, {"mode": self._mode}),
            (dialect.set_effort_request, {"effort": self._reasoning_effort}),
        )
        for builder, values in operations:
            await self._send_dialect_request(builder(session_id=sid, **values))
        await self._connection.drain_init_notifications(duration=_DRAIN_DURATION)

    async def _initialize_session(self) -> None:
        assert self._connection is not None
        await self._connection.initialize(
            {
                "protocolVersion": self._dialect.protocol_version(),
                "clientInfo": self._dialect.client_info(
                    client_name=CLIENT_NAME, client_version=CLIENT_VERSION
                ),
            },
            timeout=_INIT_TIMEOUT,
        )
        self._agent_capabilities = dict(self._connection.agent_capabilities or {})
        self._can_load_session = bool(
            self._agent_capabilities.get(CAP_LOAD_SESSION, False)
        )
        self._can_execute_commands = self._connection.supports_native_commands
        resume_id, self._resume_session_id = self._resume_session_id, None
        self._resumed, self._session, self._session_id = False, None, None
        resumed = bool(
            resume_id and self._can_load_session and await self._try_resume(resume_id)
        )
        if not resumed:
            await self._new_session()
        self.touch_activity()
        await self._configure_session()

    async def start_fresh_turn_session(self) -> None:
        if self._connection is None or not self.is_process_alive():
            return
        if self._session is not None and self._session_id is not None:
            await self._connection.close_session(self._session_id)
        self._session = self._session_id = self._resume_session_id = None
        self._resumed = False
        await self._new_session()
        await self._configure_session()

    async def _reset(self) -> None:
        await self._teardown()

    async def _teardown(self) -> None:
        connection, self._connection = self._connection, None
        try:
            if connection is not None:
                await connection.close()
            elif self._transport.process is not None:
                try:
                    await self._transport.kill(force=True)
                finally:
                    self._transport.teardown()
        except Exception:
            logger.debug("ACP client teardown failed", exc_info=True)
        finally:
            self._session = self._session_id = None
            self._session_new_snapshot = {}
            self._resumed = False
            self._last_stop_reason = ""
            self._can_execute_commands = self._can_load_session = False
            self._agent_capabilities = {}

    async def shutdown(self) -> None:
        await self._teardown()

    async def _events(self, value: str, timeout: float, *, command: bool):
        await self.ensure_ready()
        if command and not self._can_execute_commands:
            raise AcpCommandsUnsupported(value)
        session = self._session
        assert session is not None
        if not command:
            session.set_steer_source(self._steer_pull)
        stream = session.stream_command if command else session.stream_events
        async for event in stream(value, timeout=timeout):
            self._stamp_turn_telemetry(event)
            self.last_prompt_stats = session.last_prompt_stats
            self._last_stop_reason = session._last_stop_reason
            yield event

    async def stream_events(
        self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        async for event in self._events(message, timeout, command=False):
            yield event

    async def stream_command(
        self, command: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        async for event in self._events(command, timeout, command=True):
            yield event

    def steer_capable(self) -> bool:
        return bool(self._dialect.supports_mid_turn_prompt)

    def set_steer_source(self, pull: Callable[[], list[str]] | None) -> bool:
        self._steer_pull = pull
        if self._session is not None:
            return self._session.set_steer_source(pull)
        return pull is not None and self.steer_capable()

    def undelivered_steers(self) -> list[str]:
        return [] if self._session is None else self._session.undelivered_steers()

    async def send_message(
        self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> str:
        return "".join(
            [
                event.text
                async for event in self.stream_events(message, timeout=timeout)
                if event.kind == EVENT_TEXT_CHUNK and event.text
            ]
        )

    async def send_command(self, command: str) -> str:
        fragments = []
        try:
            async for event in self.stream_command(command, timeout=60.0):
                if event.kind == EVENT_TEXT_CHUNK and event.text:
                    fragments.append(event.text)
        except (AcpTimeoutError, AcpCommandsUnsupported):
            logger.debug(
                "ACP command %s did not produce a supported completion", command
            )
        return "".join(fragments)

    def _stamp_turn_telemetry(self, event: AcpEvent) -> None:
        if event.kind == EVENT_COMPLETE and self._session is not None:
            event.event_count = self._session.last_prompt_stats.event_count
            event.tool_call_count = len(self._session.last_prompt_stats.tool_calls)

    async def approve_tool(
        self, request_id: str | int, option_id: str | None = None
    ) -> None:
        if self._session is None:
            raise AcpError("Cannot approve tool before session is initialized")
        await self._session.approve_tool(request_id, option_id)

    async def reject_tool(self, request_id: str | int) -> None:
        if self._session is None:
            raise AcpError("Cannot reject tool before session is initialized")
        await self._session.reject_tool(request_id)

    async def cancel_session(self) -> None:
        if self._session is not None:
            await self._session.cancel()

    async def wait_turn_done(self, timeout: float) -> str:
        return (
            await self._session.wait_turn_done(timeout)
            if self._session is not None
            else ""
        )

    def has_active_turn(self) -> bool:
        return self._session is not None and self._session.has_active_turn()

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        if self._connection is None or self._session_id is None:
            return {"type": "timeout"}
        return await self._connection.wait_for_session_frame(
            self._session_id,
            method=METHOD_COMPACTION_STATUS,
            terminal_types=("completed", "failed"),
            timeout=timeout,
            also_track=(METHOD_METADATA,),
        )
