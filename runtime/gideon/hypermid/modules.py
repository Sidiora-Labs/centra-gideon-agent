"""Session-scoped supervision for Hypermid role modules."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .foundation import Id, Scope, Trace
from .roles import TOOL_ROLE_V1, RoleProcess, RoleRequest
from .tool_provider import HypermidRoleToolProvider


class ModuleLifecycleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolModuleSpec:
    module_id: Id
    command: tuple[str, ...]
    provider_name: str
    display_name: str
    carrier_id: Id
    preset: str = "default"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    composition: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "module_id", Id(self.module_id))
        object.__setattr__(self, "carrier_id", Id(self.carrier_id))
        object.__setattr__(self, "command", tuple(self.command))
        if not self.command or any(
            not isinstance(part, str)
            or not part
            or "\x00" in part
            or any(character in part for character in ("\r", "\n"))
            for part in self.command
        ):
            raise ModuleLifecycleError("module command is invalid")
        if not self.provider_name or not self.display_name or not self.preset:
            raise ModuleLifecycleError("module identity is incomplete")
        object.__setattr__(self, "parameters", dict(self.parameters))
        object.__setattr__(self, "composition", dict(self.composition))


@dataclass(frozen=True, slots=True)
class ModuleHealth:
    module_id: str
    session_key_digest: str
    provider_name: str
    connected: bool
    pid: int | None
    catalog_generation: int | None
    active_calls: int


@dataclass(slots=True)
class _RunningModule:
    spec: ToolModuleSpec
    route: RoleProcess
    provider: HypermidRoleToolProvider


class HypermidModuleSupervisor:
    """Own real role processes and bind every one to a Gideon session."""

    def __init__(self, state_root: str | Path, *, scope: Scope) -> None:
        self.state_root = Path(state_root)
        if not self.state_root.is_absolute():
            raise ModuleLifecycleError("module state root must be absolute")
        self.scope = scope
        self._enabled = False
        self._modules: dict[tuple[str, str], _RunningModule] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True
        set_active_module_supervisor(self)

    async def disable(self) -> None:
        self._enabled = False
        if get_active_module_supervisor() is self:
            set_active_module_supervisor(None)
        await self.shutdown()

    async def open_tool_module(
        self, session_key: str, spec: ToolModuleSpec
    ) -> HypermidRoleToolProvider:
        key = self._key(session_key, spec.module_id)
        async with self._lock:
            if not self._enabled:
                raise ModuleLifecycleError(
                    "Hypermid module supervision requires an authenticated lifecycle"
                )
            current = self._modules.get(key)
            if current is not None:
                if current.spec != spec:
                    raise ModuleLifecycleError(
                        "module identity is already bound to different configuration"
                    )
                return current.provider
            route = RoleProcess(spec.command, self._module_root(*key))
            try:
                await asyncio.to_thread(route.start)
                await self._verify_role(route)
                provider = HypermidRoleToolProvider(
                    route=route,
                    scope=self.scope,
                    provider_name=spec.provider_name,
                    display_name=spec.display_name,
                    carrier_id=spec.carrier_id,
                    preset=spec.preset,
                    parameters=spec.parameters,
                    composition=spec.composition,
                )
                await provider.list_tools()
            except BaseException:
                await asyncio.to_thread(route.close)
                raise
            self._modules[key] = _RunningModule(spec, route, provider)
            return provider

    def providers_for_session(self, session_key: str) -> tuple[HypermidRoleToolProvider, ...]:
        digest = _session_digest(session_key)
        return tuple(
            running.provider
            for (bound_session, _), running in sorted(self._modules.items())
            if bound_session == digest
        )

    async def close_session(self, session_key: str) -> None:
        digest = _session_digest(session_key)
        async with self._lock:
            selected = [key for key in self._modules if key[0] == digest]
            running = [self._modules.pop(key) for key in selected]
        await asyncio.gather(
            *(asyncio.to_thread(item.route.close) for item in running),
            return_exceptions=True,
        )

    async def shutdown(self) -> None:
        async with self._lock:
            running = tuple(self._modules.values())
            self._modules.clear()
        await asyncio.gather(
            *(asyncio.to_thread(item.route.close) for item in running),
            return_exceptions=True,
        )

    def health(self) -> tuple[ModuleHealth, ...]:
        result = []
        for (session_digest, module_id), running in sorted(self._modules.items()):
            health = running.provider.health()
            result.append(
                ModuleHealth(
                    module_id=module_id,
                    session_key_digest=session_digest,
                    provider_name=running.provider.name,
                    connected=health.connected,
                    pid=running.route.pid,
                    catalog_generation=health.catalog_generation,
                    active_calls=health.active_calls,
                )
            )
        return tuple(result)

    async def _verify_role(self, route: RoleProcess) -> None:
        suffix = uuid4().hex
        response = await asyncio.to_thread(
            route.request,
            RoleRequest(
                TOOL_ROLE_V1,
                "describe",
                {},
                Trace(Id(f"trace-{suffix}"), Id(f"request-{suffix}")),
                self.scope,
            ),
        )
        result = response.get("result") if isinstance(response, dict) else None
        majors = result.get("majors") if isinstance(result, dict) else None
        compatible = any(
            isinstance(major, dict)
            and major.get("version") == TOOL_ROLE_V1
            and {"catalog", "call"}.issubset(set(major.get("ops", ())))
            for major in majors or ()
        )
        if not compatible:
            raise ModuleLifecycleError("module does not implement the tool role")

    @staticmethod
    def _key(session_key: str, module_id: Id) -> tuple[str, str]:
        if not isinstance(session_key, str) or not session_key:
            raise ModuleLifecycleError("session key is required")
        return (_session_digest(session_key), str(module_id))

    def _module_root(self, session_digest: str, module_id: str) -> Path:
        module_digest = hashlib.sha256(module_id.encode("utf-8")).hexdigest()
        return self.state_root / session_digest / module_digest


_active_supervisor: HypermidModuleSupervisor | None = None


def set_active_module_supervisor(
    supervisor: HypermidModuleSupervisor | None,
) -> None:
    global _active_supervisor
    _active_supervisor = supervisor


def get_active_module_supervisor() -> HypermidModuleSupervisor | None:
    return _active_supervisor


def providers_for_session(session_key: str) -> tuple[HypermidRoleToolProvider, ...]:
    supervisor = get_active_module_supervisor()
    return () if supervisor is None else supervisor.providers_for_session(session_key)


async def close_session_modules(session_key: str) -> None:
    supervisor = get_active_module_supervisor()
    if supervisor is not None:
        await supervisor.close_session(session_key)


def _session_digest(session_key: str) -> str:
    if not isinstance(session_key, str) or not session_key:
        raise ModuleLifecycleError("session key is required")
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()


__all__ = [
    "HypermidModuleSupervisor",
    "ModuleHealth",
    "ModuleLifecycleError",
    "ToolModuleSpec",
    "close_session_modules",
    "get_active_module_supervisor",
    "providers_for_session",
    "set_active_module_supervisor",
]
