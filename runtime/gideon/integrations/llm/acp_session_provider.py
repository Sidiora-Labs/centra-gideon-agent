"""Session-scoped provider facade for a connection owned by the ACP pool."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gideon.engine.agents.provider import AgentProvider
from gideon.integrations.acp.outcomes import AcpToolOutcomesMixin
from gideon.integrations.llm.acp_provider_runtime import (
    cancel_turn,
    capability_names,
    pooled_permission_mode,
    relay_events,
)
from gideon.integrations.llm.base import CancelOutcome, LLMEvent, ModelProvider

if TYPE_CHECKING:
    from gideon.integrations.acp.session import AcpConnection, AcpSession

logger = logging.getLogger(__name__)


class AcpSessionProvider(AcpToolOutcomesMixin, AgentProvider, ModelProvider):
    supports_tools = True

    def __init__(
        self,
        connection: AcpConnection,
        session: AcpSession,
        *,
        runtime_id: str,
        model: str = "",
        agent_name: str = "",
        unattended: bool = False,
    ) -> None:
        self._conn, self._session = connection, session
        self._runtime_id = runtime_id
        self._model, self._agent_name = model, agent_name
        self.set_unattended(unattended)

    @property
    def provider_id(self) -> str:
        return self._runtime_id

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def agent_model(self) -> str:
        return self._model

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def pid(self) -> int | None:
        transport = getattr(self._conn, "_transport", None)
        return None if transport is None else transport.pid

    @property
    def declared_capabilities(self) -> frozenset[str]:
        return capability_names(self._conn.agent_capabilities)

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        session_id = self.session_id
        try:
            await self._conn.close_session(session_id)
        except Exception:
            logger.debug(
                "Closing pooled ACP session %s failed", session_id, exc_info=True
            )

    @staticmethod
    def _to_llm_event(event: Any) -> LLMEvent:
        from gideon.integrations.acp.adapter import acp_event_to_agent_event

        return acp_event_to_agent_event(event)

    def _stamp_turn_telemetry(self, event: Any) -> None:
        from gideon.integrations.acp.types import EVENT_COMPLETE

        if event.kind != EVENT_COMPLETE:
            return
        stats = self._session.last_prompt_stats
        event.event_count, event.tool_call_count = stats.event_count, len(
            stats.tool_calls
        )

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        async for event in relay_events(
            self, self._session.stream_events, message, self._stamp_turn_telemetry
        ):
            yield event

    @property
    def supports_native_commands(self) -> bool:
        return bool(self._conn.supports_native_commands)

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        from gideon.integrations.acp.errors import AcpCommandsUnsupported

        if not self.supports_native_commands:
            raise AcpCommandsUnsupported(command)
        async for event in relay_events(
            self, self._session.stream_command, command, self._stamp_turn_telemetry
        ):
            yield event

    async def approve_tool(self, request_id: str | int) -> None:
        await self._session.approve_tool(request_id)

    async def reject_tool(self, request_id: str | int) -> None:
        await self._session.reject_tool(request_id)

    def steer_capable(self) -> bool:
        return self._session.steer_capable()

    def set_steer_source(self, pull: Callable[[], list[str]] | None) -> bool:
        return self._session.set_steer_source(pull)

    def undelivered_steers(self) -> list[str]:
        return self._session.undelivered_steers()

    def context_usage_pct(self) -> float | None:
        return self._session.context_usage_pct()

    def is_alive(self) -> bool:
        return self.is_process_alive()

    def is_process_alive(self) -> bool:
        return self._conn.is_process_alive()

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        return await cancel_turn(
            self._session, "cancel", wait_ack_timeout, tolerate_wait_errors=True
        )

    def set_workspace(self, path: Path) -> None:
        return None

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        return None

    async def _send_dialect_request(self, request) -> None:
        if request is None:
            return
        await self._conn.send_request(request.method, request.params)

    async def _configure(self, axis: str, value: str) -> None:
        if not value or (axis == "agent" and value == self._agent_name):
            return
        builders = {
            "agent": "activate_agent_request",
            "model": "set_model_request",
            "mode": "set_mode_request",
            "effort": "set_effort_request",
        }
        parameters = {"session_id": self.session_id, axis: value}
        if axis == "model":
            parameters["default_model"] = "\x00"
        request = getattr(self._conn._dialect, builders[axis])(**parameters)
        await self._send_dialect_request(request)
        state_field = {"agent": "_agent_name", "model": "_model"}.get(axis)
        if state_field is not None:
            setattr(self, state_field, value)

    async def set_agent(self, agent: str) -> None:
        await self._configure("agent", agent)

    async def set_model(self, model: str) -> None:
        await self._configure("model", model)

    async def set_mode(self, mode: str) -> None:
        await self._configure("mode", pooled_permission_mode(mode, self._unattended))

    async def set_reasoning_effort(self, effort: str) -> None:
        await self._configure("effort", effort)

    def set_unattended(self, unattended: bool) -> None:
        self._unattended = bool(unattended)


def concurrent_sessions_enabled(dialect_id: str | None) -> bool:
    from gideon.core.config import AppConfig
    from gideon.integrations.acp.dialect import get_dialect

    try:
        dialect = get_dialect(dialect_id)
        return bool(
            dialect.supports_concurrent_sessions
            and AppConfig.load().agent.acp_concurrent_sessions
        )
    except Exception:
        logger.debug(
            "Concurrent ACP gate unavailable; leaving it disabled", exc_info=True
        )
        return False


async def open_acp_session_provider(
    connection: AcpConnection,
    *,
    runtime_id: str,
    cwd: Path,
    session_files_dir: Path | None = None,
    model: str = "",
    agent_name: str = "",
    session_key: str | None = "",
    mcp_servers: list | None = None,
    unattended: bool = False,
) -> AcpSessionProvider:
    from gideon.integrations.acp.mcp_servers import core_mcp_servers

    parameters = {"cwd": str(cwd), "mcpServers": mcp_servers}
    if parameters["mcpServers"] is None:
        parameters["mcpServers"] = core_mcp_servers(session_key=session_key)
    session = await connection.new_session(
        parameters, session_files_dir=session_files_dir
    )
    return AcpSessionProvider(
        connection,
        session,
        runtime_id=runtime_id,
        model=model,
        agent_name=agent_name,
        unattended=unattended,
    )
