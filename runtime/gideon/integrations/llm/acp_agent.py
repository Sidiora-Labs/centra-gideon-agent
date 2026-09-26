"""Client-owned ACP runtime exposed through the model and agent provider APIs."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gideon.engine.agents.provider import AgentProvider
from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.outcomes import AcpToolOutcomesMixin
from gideon.integrations.llm.acp_provider_runtime import (
    ProbePlan,
    cancel_turn,
    capability_names,
    launch_arguments,
    relay_events,
)
from gideon.integrations.llm.base import CancelOutcome, LLMEvent, ModelProvider
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.registry import (
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
)

if TYPE_CHECKING:
    from gideon.engine.agents.provider import DiscoveredAgent, ReadinessStatus

logger = logging.getLogger(__name__)


class AcpAgentProvider(AcpToolOutcomesMixin, ModelProvider, AgentProvider):
    def __init__(
        self,
        *,
        command: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        model: str | None = None,
        agent_name: str = "",
        capability_flags: dict[str, bool] | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
        sandbox_mode: str = "auto",
        sandbox: str = "none",
        session_files_dir: Path | None = None,
        dialect: str | None = None,
        mode: str = "",
        reasoning_effort: str = "",
        unattended: bool = False,
        runtime_id: str = "",
    ) -> None:
        from gideon.integrations.acp.dialect import get_dialect

        if not command:
            raise ValueError("AcpAgentProvider requires a non-empty command list")
        self._command = list(command)
        self._cwd = None if cwd is None else Path(cwd)
        self._env = dict(env or {})
        self._model, self._agent_name = model, agent_name
        self._session_key, self._channel_id = session_key, channel_id
        self._sandbox_mode, self._sandbox = sandbox_mode, sandbox or "none"
        self._session_files_dir = (
            None if session_files_dir is None else Path(session_files_dir)
        )
        self._dialect_id, self._runtime_id = dialect, runtime_id or ""
        self._mode, self._reasoning_effort = mode or "", reasoning_effort or ""
        self._unattended = bool(unattended)
        self._capability_flags = dict(capability_flags or {})
        self._negotiated_capabilities: frozenset[str] = frozenset()
        configuration: dict = {
            "work_dir": self._cwd,
            "command": self._command,
            "extra_env": self._env or None,
            "model": self._model,
            "agent": self._agent_name,
            "session_key": self._session_key,
            "channel_id": self._channel_id,
            "sandbox_mode": self._sandbox_mode,
            "sandbox": self._sandbox,
            "session_files_dir": self._session_files_dir,
            "dialect": get_dialect(self._dialect_id),
            "mode": self._mode,
            "reasoning_effort": self._reasoning_effort,
            "unattended": self._unattended,
        }
        self._client = AcpClient(**configuration)

    @property
    def provider_id(self) -> str:
        selected = self._runtime_id.strip()
        if not selected:
            return "acp:" + (Path(self._command[0]).name if self._command else "agent")
        return selected if selected.startswith("acp:") else "acp:" + selected

    @classmethod
    async def probe_readiness(cls, options: dict) -> ReadinessStatus:
        from gideon.engine.agents.provider import ReadinessStatus

        plan = ProbePlan.from_options(options)
        if plan is None:
            return ReadinessStatus(False, "error", "no options.command configured")
        return await plan.readiness(cls)

    @classmethod
    async def discover_agents(cls, options: dict) -> list[DiscoveredAgent]:
        plan = ProbePlan.from_options(options)
        if plan is None or not shutil.which(plan.command[0]):
            return []
        status = await cls.probe_readiness(options)
        if not status.ready:
            logger.debug(
                "ACP discovery unavailable for %s: %s",
                options.get("runtime_id") or plan.command[0],
                status.state,
            )
            return []
        selected = dict(options)
        if not str(selected.get("runtime_id") or "").strip():
            selected["runtime_id"] = "acp:" + Path(plan.command[0]).name
        try:
            snapshot = await plan.snapshot()
        except Exception:
            logger.debug(
                "ACP discovery failed for %s", selected["runtime_id"], exc_info=True
            )
            return []
        return cls.agents_from_snapshot(selected, snapshot)

    @classmethod
    def agents_from_snapshot(
        cls, options: dict, snapshot: dict
    ) -> list[DiscoveredAgent]:
        from gideon.engine.agents.provider import DiscoveredAgent
        from gideon.integrations.acp.dialect import get_dialect

        runtime = str(options.get("runtime_id") or "").strip() or "acp"
        label = (
            str(options.get("runtime_label") or "").strip() or runtime.split(":", 1)[-1]
        )
        discovered = get_dialect(options.get("dialect")).normalize_discovery(
            snapshot or {}
        )
        models, efforts = list(discovered.models), list(discovered.supported_efforts)
        try:
            from gideon.engine.agents.runners import record_capabilities

            axes = {
                "models": models,
                "modes": [str(row["id"]) for row in discovered.agents if row.get("id")],
                "efforts": [
                    str(row["value"])
                    for row in efforts
                    if isinstance(row, dict) and row.get("value")
                ],
            }
            record_capabilities(runtime, **axes)
        except Exception:
            logger.debug(
                "Runner capability recording failed for %s", runtime, exc_info=True
            )

        def make_agent(row: dict) -> DiscoveredAgent:
            raw_id, title = str(row.get("id") or ""), str(row.get("label") or "")
            if row.get("use_runtime_prefix"):
                title = f"{label} ({title})" if title else label
            else:
                title = title or raw_id or label
            return DiscoveredAgent(
                id="/".join((runtime, raw_id)) if raw_id else runtime,
                name=title,
                runtime=runtime,
                description=str(row.get("description") or ""),
                provider_agent=str(row.get("provider_agent") or ""),
                reasoning_effort=str(row.get("reasoning_effort") or ""),
                models=list(models),
                supported_efforts=efforts,
            )

        return [make_agent(row) for row in discovered.agents]

    @property
    def client(self) -> AcpClient:
        return self._client

    async def start(self) -> None:
        await self._client.ensure_ready()
        self._negotiated_capabilities = capability_names(
            getattr(self._client, "_agent_capabilities", None)
        )

    async def shutdown(self) -> None:
        await self._client.shutdown()

    @property
    def declared_capabilities(self) -> frozenset[str]:
        return self._negotiated_capabilities

    @property
    def session_snapshot(self) -> dict:
        return self._client.session_snapshot

    def live_controls(self) -> dict:
        return self._client.live_controls()

    async def set_live_control(self, axis: str, value: str) -> None:
        await self._client.set_live_control(axis, value)
        if axis == "model":
            self._model = value
        else:
            self._reasoning_effort = value

    @staticmethod
    def _to_llm_event(event: Any) -> LLMEvent:
        from gideon.integrations.acp.adapter import acp_event_to_agent_event

        return acp_event_to_agent_event(event)

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        async for event in relay_events(self, self._client.stream_events, message):
            yield event

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        async for event in relay_events(self, self._client.stream_command, command):
            yield event

    @property
    def supports_native_commands(self) -> bool:
        return bool(getattr(self._client, "supports_native_commands", False))

    async def approve_tool(self, request_id: str | int) -> None:
        await self._client.approve_tool(request_id)

    async def reject_tool(self, request_id: str | int) -> None:
        await self._client.reject_tool(request_id)

    async def start_fresh_turn_session(self) -> None:
        await self._client.start_fresh_turn_session()

    async def compact(self, context: str = "") -> None:
        command = "/compact"
        if context:
            command += (
                " Preserve this session context in the summary:\n" + context[:4000]
            )
        await self._client.send_command(command)

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        return await self._client.wait_for_compaction(timeout)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        return await cancel_turn(self._client, "cancel_session", wait_ack_timeout)

    def context_usage_pct(self) -> float | None:
        return self._client.last_prompt_stats.context_pct

    def steer_capable(self) -> bool:
        from gideon.integrations.acp.dialect import get_dialect

        try:
            return bool(get_dialect(self._dialect_id).supports_mid_turn_prompt)
        except Exception:
            return False

    def set_steer_source(self, pull: Callable[[], list[str]] | None) -> bool:
        return self._client.set_steer_source(pull)

    def undelivered_steers(self) -> list[str]:
        return self._client.undelivered_steers()

    def is_alive(self) -> bool:
        return self._client.is_responsive()

    def is_process_alive(self) -> bool:
        return self._client.is_process_alive()

    @property
    def exit_code(self) -> int | None:
        return self._client.exit_code

    def touch_activity(self) -> None:
        self._client.touch_activity()

    def set_workspace(self, path: Path) -> None:
        self._cwd = self._client._work_dir = Path(path)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self._session_key, self._channel_id = session_key, channel_id
        self._client.rekey(session_key, channel_id)

    def set_channel(self, channel_id: str | None) -> None:
        self._channel_id = channel_id

    async def set_model(self, model: str) -> None:
        await self._client.set_model(model)

    async def set_agent(self, agent: str) -> None:
        await self._client.set_agent(agent)

    def set_unattended(self, unattended: bool) -> None:
        self._unattended = self._client._unattended = bool(unattended)

    async def set_mode(self, mode: str) -> None:
        await self._client.set_mode(mode)

    async def set_reasoning_effort(self, effort: str) -> None:
        self._reasoning_effort = effort or ""
        await self._client.set_effort(self._reasoning_effort)

    def set_resume(self, session_id: str) -> None:
        self._client.set_resume_session_id(session_id)

    @property
    def resumed(self) -> bool:
        return bool(getattr(self._client, "resumed", False))

    @property
    def agent_model(self) -> str:
        return self._client._model or ""

    @property
    def agent_name(self) -> str:
        return self._client._agent or ""

    @property
    def pid(self) -> int | None:
        return getattr(self._client, "_pid", None)

    @property
    def session_id(self) -> str:
        return (self._client._session_id or "") if self._client else ""


ACP_AGENT_CAPABILITY = ProviderCapability(
    type="acp_agent",
    capabilities=frozenset(
        {
            Capability.CHAT,
            Capability.CODE_TOOLS,
            Capability.STREAMING,
            Capability.TOOL_APPROVAL,
            Capability.PLANNING,
            Capability.SUMMARIZATION,
        }
    ),
    supports_streaming=True,
    supports_tools=True,
    supports_embeddings=False,
    supports_vision=False,
    max_context_tokens=0,
    notes=(
        "Generic ACP-over-stdio agent; spawn command supplied via "
        "options.command. Per-spawn capabilities are negotiated through "
        "the ACP initialize handshake."
    ),
)


def _factory(
    *, entry: ProviderEntry, session_key: str | None = None, **kwargs: object
) -> ModelProvider:
    arguments = launch_arguments(entry, session_key, kwargs)
    credentials = kwargs.get("credential_store")
    if entry.credential and credentials is not None:
        try:
            resolve = getattr(credentials, "resolve", None)
            if callable(resolve):
                resolve(entry.credential)
        except Exception:
            logger.debug(
                "ACP credential reference unavailable for %r: %r",
                entry.name,
                entry.credential,
            )
    return AcpAgentProvider(**arguments)


def _register_provider() -> None:
    try:
        get_default_registry().register_type(ACP_AGENT_CAPABILITY, _factory)
    except ProviderResolutionError:
        logger.debug("acp_agent provider type already registered with default registry")
    try:
        from gideon.engine.agents.registry import register_agent_provider

        register_agent_provider("acp", AcpAgentProvider)
    except Exception:
        logger.debug("could not register acp AgentProvider family", exc_info=True)


_register_provider()
