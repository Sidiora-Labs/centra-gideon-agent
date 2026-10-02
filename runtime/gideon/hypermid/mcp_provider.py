"""Native Gideon ToolProvider over the authenticated daemon MCP bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import weakref
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from gideon.engine.task_modes import infer_risk_from_name
from gideon.integrations.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

from .client import HypermidClient, HypermidOutcomeUnknown, HypermidRemoteError
from .foundation import EffectState, Id
from .tool_provider import CancellationOutcome, CancellationState


class DaemonMcpViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DaemonMcpHealth:
    module_id: str
    connected_sessions: int
    catalog_generation: int
    active_calls: int
    process_ready: bool


@dataclass(slots=True)
class _ActiveCall:
    effect_id: Id | None
    request: asyncio.Task[object]


@dataclass(frozen=True, slots=True)
class _CatalogTool:
    definition: ToolDefinition
    effect: str


class DaemonMcpToolProvider(ToolProvider):
    """Expose one authenticated daemon session's MCP catalog to Gideon's native loop."""

    def __init__(
        self,
        client: HypermidClient,
        *,
        session_key: str,
        provider_name: str = "hypermid-mcp",
        display_name: str = "Hypermid MCP",
    ) -> None:
        if not isinstance(session_key, str) or not session_key:
            raise DaemonMcpViolation("Gideon session key is required")
        if not provider_name or not display_name:
            raise DaemonMcpViolation("provider identity is required")
        self._client = client
        self._session_key = session_key
        self._name = provider_name
        self._display_name = display_name
        self._catalog: dict[str, _CatalogTool] = {}
        self._active: dict[str, _ActiveCall] = {}
        self._effects: dict[str, Id] = {}
        self._outcomes: dict[str, EffectState] = {}
        self._catalog_lock = asyncio.Lock()
        self._connected = True
        from gideon.integrations.tool_providers.registry import register_provider

        registration = register_provider(
            self,
            owner_type="mcp",
            owner=self._name,
            instance_id=self._session_key,
        )
        if registration.get("accepted") is False:
            raise DaemonMcpViolation("daemon MCP provider registration was refused")
        _register(self)

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def connected(self) -> bool:
        return self._connected and self._client.connected

    @property
    def active_call_keys(self) -> tuple[str, ...]:
        return tuple(self._active)

    async def list_tools(self) -> list[ToolDefinition]:
        self._require_open()
        async with self._catalog_lock:
            payload = await self._client.request("mcp.catalog", {})
            raw = _mapping(payload, "MCP catalog")
            tools = raw.get("tools")
            if not isinstance(tools, list):
                raise DaemonMcpViolation("MCP catalog tools are malformed")
            catalog: dict[str, _CatalogTool] = {}
            for value in tools:
                descriptor = _mapping(value, "MCP tool descriptor")
                name = _string(descriptor.get("name"), "tool name")
                description = _string(
                    descriptor.get("description", ""), "tool description", allow_empty=True
                )
                schema = descriptor.get("input_schema")
                if not isinstance(schema, dict):
                    raise DaemonMcpViolation("MCP tool input schema is malformed")
                if descriptor.get("requires_approval") is not True:
                    raise DaemonMcpViolation("daemon MCP tools must require Gideon approval")
                effect = descriptor.get("effect")
                if effect not in {"query", "idempotent", "durable"}:
                    raise DaemonMcpViolation("MCP tool effect class is malformed")
                if name in catalog:
                    raise DaemonMcpViolation("MCP catalog contains duplicate tool names")
                catalog[name] = _CatalogTool(
                    ToolDefinition(
                        name=name,
                        description=description,
                        provider=self.name,
                        parameters=schema,
                        requires_approval=True,
                        risk_level=RiskLevel(infer_risk_from_name(name)),
                    ),
                    effect,
                )
            self._catalog = catalog
            return [tool.definition for tool in catalog.values()]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self._require_open()
        if not isinstance(arguments, dict):
            return _failure("tool arguments must be an object", EffectState.NOT_STARTED)
        if not self._catalog:
            try:
                await self.list_tools()
            except Exception as error:
                return _failure(
                    f"Hypermid MCP discovery failed: {str(error)[:300]}",
                    EffectState.NOT_STARTED,
                )
        tool = self._catalog.get(tool_name)
        if tool is None:
            return _failure(
                "tool is not owned by this Hypermid MCP provider",
                EffectState.NOT_STARTED,
            )
        call_key = f"call-{uuid4().hex}"
        effect_id = (
            Id(f"effect-{uuid4().hex}") if tool.effect != "query" else None
        )
        payload: dict[str, Any] = {"tool": tool_name, "arguments": arguments}
        if effect_id is not None:
            payload.update(
                effect_id=str(effect_id),
                input_digest=hashlib.sha256(
                    json.dumps(
                        {"arguments": arguments, "tool": tool_name},
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            )
        request = asyncio.create_task(
            self._client.request(
                "mcp.invoke",
                payload,
                effect_kind=tool.effect,
            ),
            name=f"hypermid-mcp-{call_key}",
        )
        if effect_id is not None:
            self._effects[call_key] = effect_id
        self._active[call_key] = _ActiveCall(effect_id, request)
        try:
            payload = await request
        except asyncio.CancelledError:
            self._outcomes[call_key] = EffectState.UNKNOWN
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return _failure(
                "Hypermid MCP call was cancelled after dispatch",
                EffectState.UNKNOWN,
                call_key=call_key,
                effect_id=effect_id,
                code="MCP_OUTCOME_UNKNOWN",
            )
        except HypermidOutcomeUnknown as error:
            self._outcomes[call_key] = EffectState.UNKNOWN
            return _failure(
                str(error), EffectState.UNKNOWN, call_key=call_key, effect_id=effect_id
            )
        except HypermidRemoteError as error:
            state = error.error.effect_state or EffectState.NOT_STARTED
            self._outcomes[call_key] = EffectState(state)
            return _failure(
                error.error.message,
                EffectState(state),
                call_key=call_key,
                effect_id=effect_id,
                code=error.error.code,
            )
        except Exception as error:
            self._outcomes[call_key] = EffectState.UNKNOWN
            return _failure(
                f"Hypermid MCP route failed: {str(error)[:300]}",
                EffectState.UNKNOWN,
                call_key=call_key,
                effect_id=effect_id,
            )
        finally:
            self._active.pop(call_key, None)
            _trim_effects(self._effects)
            _trim_outcomes(self._outcomes)
        response = _mapping(payload, "MCP invocation result")
        if response.get("classification") != "committed" or "result" not in response:
            self._outcomes[call_key] = EffectState.UNKNOWN
            return _failure(
                "Hypermid MCP route omitted its committed terminal result",
                EffectState.UNKNOWN,
                call_key=call_key,
                effect_id=effect_id,
            )
        value = response["result"]
        self._outcomes[call_key] = EffectState.COMMITTED
        output = (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return ToolResult(
            True,
            output=output,
            metadata={
                "call_key": call_key,
                "effect_state": EffectState.COMMITTED.value,
                **({"effect_id": str(effect_id)} if effect_id is not None else {}),
            },
        )

    async def cancel(self, call_key: str) -> CancellationOutcome:
        terminal = self._outcomes.get(call_key)
        if terminal is not None and call_key not in self._active:
            return _cancellation_outcome(terminal)
        effect_id = self._effects.get(call_key)
        active = self._active.get(call_key)
        if effect_id is None and active is None:
            return CancellationOutcome(
                CancellationState.REFUSED, EffectState.NOT_STARTED
            )
        if active is not None and not active.request.done():
            active.request.cancel()
            try:
                await active.request
            except (asyncio.CancelledError, Exception):
                pass
        state = EffectState.UNKNOWN
        for _ in range(50):
            active_calls = None
            reported: object = None
            try:
                health = await self.health()
                active_calls = sum(module.active_calls for module in health)
            except Exception:
                pass
            if effect_id is not None:
                try:
                    payload = await self._client.request(
                        "mcp.effect.status", {"effect_id": str(effect_id)}
                    )
                    raw = _mapping(payload, "MCP effect status")
                    reported = raw.get("state")
                    if reported in {"intent", "not_started"}:
                        state = EffectState.NOT_STARTED
                    elif reported == "committed":
                        state = EffectState.COMMITTED
                    elif reported == "unknown":
                        state = EffectState.UNKNOWN
                    elif reported != "dispatched":
                        raise DaemonMcpViolation("MCP effect state is malformed")
                except Exception:
                    state = EffectState.UNKNOWN
            if active_calls == 0 and (
                effect_id is None or state is not EffectState.UNKNOWN or reported == "unknown"
            ):
                break
            await asyncio.sleep(0.01)
        self._outcomes[call_key] = state
        return _cancellation_outcome(state)

    async def health(self) -> tuple[DaemonMcpHealth, ...]:
        self._require_open()
        payload = await self._client.request("mcp.health", {})
        raw = _mapping(payload, "MCP health")
        modules = raw.get("modules")
        if not isinstance(modules, list):
            raise DaemonMcpViolation("MCP health modules are malformed")
        return tuple(_health(value) for value in modules)

    async def close_session(self) -> None:
        if not self._connected:
            self._unregister_tool_provider()
            _unregister(self)
            return
        error: BaseException | None = None
        try:
            payload = await self._client.request(
                "mcp.session.evict", {}, effect_kind="idempotent"
            )
            result = _mapping(payload, "MCP session eviction")
            if (
                result.get("classification") != "evicted"
                or result.get("connected_sessions") != 0
                or result.get("active_calls") != 0
            ):
                raise DaemonMcpViolation("MCP session eviction receipt is malformed")
        except BaseException as caught:
            error = caught
        finally:
            self._connected = False
            self._unregister_tool_provider()
            _unregister(self)
            for active in tuple(self._active.values()):
                active.request.cancel()
            self._active.clear()
        if error is not None:
            raise error

    def _require_open(self) -> None:
        if not self._connected:
            raise DaemonMcpViolation("Hypermid MCP provider session is closed")

    def _unregister_tool_provider(self) -> None:
        from gideon.integrations.tool_providers.registry import unregister_provider

        unregister_provider(
            self._name,
            owner_type="mcp",
            owner=self._name,
            instance_id=self._session_key,
        )


_providers: dict[str, weakref.WeakSet[DaemonMcpToolProvider]] = {}


def _register(provider: DaemonMcpToolProvider) -> None:
    _providers.setdefault(provider._session_key, weakref.WeakSet()).add(provider)


def _unregister(provider: DaemonMcpToolProvider) -> None:
    providers = _providers.get(provider._session_key)
    if providers is None:
        return
    providers.discard(provider)
    if not providers:
        _providers.pop(provider._session_key, None)


async def evict_daemon_mcp_session(session_key: str) -> None:
    providers = tuple(_providers.pop(session_key, ()))
    results = await asyncio.gather(
        *(provider.close_session() for provider in providers), return_exceptions=True
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    if failures:
        raise RuntimeError("daemon MCP session eviction failed") from failures[0]


def _health(value: object) -> DaemonMcpHealth:
    raw = _mapping(value, "MCP module health")
    module_id = _string(raw.get("module_id"), "module id")
    connected = _natural(raw.get("connected_sessions"), "connected sessions")
    generation = _natural(raw.get("catalog_generation"), "catalog generation")
    active = _natural(raw.get("active_calls"), "active calls")
    ready = raw.get("process_ready")
    if not isinstance(ready, bool):
        raise DaemonMcpViolation("MCP process readiness is malformed")
    return DaemonMcpHealth(module_id, connected, generation, active, ready)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DaemonMcpViolation(f"{label} is malformed")
    return value


def _string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise DaemonMcpViolation(f"{label} is malformed")
    return value


def _natural(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DaemonMcpViolation(f"{label} is malformed")
    return value


def _trim_effects(effects: dict[str, Id]) -> None:
    while len(effects) > 1_024:
        effects.pop(next(iter(effects)))


def _trim_outcomes(outcomes: dict[str, EffectState]) -> None:
    while len(outcomes) > 1_024:
        outcomes.pop(next(iter(outcomes)))


def _cancellation_outcome(state: EffectState) -> CancellationOutcome:
    return {
        EffectState.NOT_STARTED: CancellationOutcome(
            CancellationState.WITHDRAWN, EffectState.NOT_STARTED
        ),
        EffectState.COMMITTED: CancellationOutcome(
            CancellationState.COMPLETED, EffectState.COMMITTED
        ),
        EffectState.UNKNOWN: CancellationOutcome(
            CancellationState.UNKNOWN, EffectState.UNKNOWN
        ),
    }[state]


def _failure(
    message: str,
    effect_state: EffectState,
    *,
    call_key: str | None = None,
    effect_id: Id | None = None,
    code: str = "HYPERMID_MCP_ERROR",
) -> ToolResult:
    metadata: dict[str, Any] = {"code": code, "effect_state": effect_state.value}
    if call_key is not None:
        metadata["call_key"] = call_key
    if effect_id is not None:
        metadata["effect_id"] = str(effect_id)
    return ToolResult(False, error=message[:300], metadata=metadata)


__all__ = [
    "DaemonMcpHealth",
    "DaemonMcpToolProvider",
    "DaemonMcpViolation",
    "evict_daemon_mcp_session",
]
