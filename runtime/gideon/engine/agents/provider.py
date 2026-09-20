"""Runtime records and the required and optional agent-provider contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.integrations.llm.events import AgentEvent


@dataclass
class AgentRuntimeDefinition:
    name: str
    provider: str = "native"
    system_prompt: str = ""
    model: str = ""
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_store: str = ""
    workspace_dir: str = ""
    approval_mode: str = ""
    triggers: list[str] = field(default_factory=list)


@dataclass
class ReadinessStatus:
    ready: bool
    state: str
    detail: str = ""
    login_command: list[str] | None = None


@dataclass
class DiscoveredAgent:
    id: str
    name: str
    runtime: str
    description: str = ""
    provider_agent: str = (
        ""  # ACP modeId for session/set_mode (default-dialect personas)
    )
    reasoning_effort: str = ""
    models: list[str] = field(default_factory=list)
    supported_efforts: list[dict] = field(default_factory=list)


@dataclass
class PermissionCapability:
    supported_modes: list[str] = field(default_factory=list)


class AgentProvider(ABC):
    """One stateful runtime; optional capabilities retain neutral defaults."""

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @classmethod
    async def probe_readiness(cls, options: dict) -> ReadinessStatus:
        return ReadinessStatus(True, "ready")

    @classmethod
    async def discover_agents(cls, options: dict) -> list[DiscoveredAgent]:
        return list()

    @classmethod
    def agents_from_snapshot(
        cls, options: dict, snapshot: dict
    ) -> list[DiscoveredAgent]:
        return list()

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    @abstractmethod
    def stream(self, message: str) -> AsyncIterator[AgentEvent]: ...

    @property
    def supports_native_commands(self) -> bool:
        return False

    @property
    def compacts_in_process(self) -> bool:
        return False

    async def stream_command(self, command: str) -> AsyncIterator[AgentEvent]:
        turn = aiter(self.stream(command))
        while True:
            try:
                event = await anext(turn)
            except StopAsyncIteration:
                return
            yield event

    @abstractmethod
    async def approve_tool(self, request_id: str | int) -> None: ...

    @abstractmethod
    async def reject_tool(self, request_id: str | int) -> None: ...

    def context_usage_pct(self) -> float | None:
        return None

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "no_turn"

    async def compact(self, context: str = "") -> None:
        return None

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        return dict(type="timeout")

    def is_alive(self) -> bool:
        return True

    def is_process_alive(self) -> bool:
        return True

    def touch_activity(self) -> None:
        return None

    def set_workspace(self, path: Path) -> None:
        return None

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        return None

    def set_channel(self, channel_id: str | None) -> None:
        return None

    async def set_model(self, model: str) -> None:
        return None

    async def set_agent(self, agent: str) -> None:
        return None

    async def set_reasoning_effort(self, effort: str) -> None:
        return None

    def set_resume(self, session_id: str) -> None:
        return None

    @property
    def resumed(self) -> bool:
        return False

    @property
    def session_id(self) -> str:
        return ""

    @property
    def pid(self) -> int | None:
        return None

    @property
    def agent_model(self) -> str:
        return ""

    @property
    def agent_name(self) -> str:
        return ""

    async def cleanup_session(self, session_id: str) -> None:
        return None
