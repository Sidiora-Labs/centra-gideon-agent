"""Scoped, credential-free status facade for Hypermid runtime integrations."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Literal, Protocol, Sequence
from uuid import uuid4

from .contracts import AccessRequest, GrantOperation
from .foundation import Id, Trace
from .memory_client import MemoryClient
from .models import JsonValue, Scope
from .modules import HypermidModuleSupervisor


IntegrationOperation = Literal["enable", "disable", "reconnect", "stop"]
ConnectionState = Literal[
    "ready", "connected", "disconnected", "disabled", "stopped", "unavailable"
]
ActionState = Literal["applied", "unavailable", "unsupported", "failed"]


class IntegrationStatusError(RuntimeError):
    code = "integration_status_error"


class IntegrationScopeError(IntegrationStatusError):
    code = "scope_mismatch"


class McpBridgeStatusSource(Protocol):
    async def health(self) -> Sequence[object]: ...

    async def shutdown(self, now_ms: int) -> None: ...


class ConditionStatusSource(Protocol):
    def snapshot(
        self, scope: Scope, session_key: str
    ) -> (
        Sequence[ConditionObservation | ConditionUnavailable]
        | Awaitable[Sequence[ConditionObservation | ConditionUnavailable]]
    ): ...


@dataclass(frozen=True, slots=True)
class ObservedFact:
    label: str
    value: str

    def __post_init__(self) -> None:
        _identifier(self.label, "observed fact label")
        _identifier(self.value, "observed fact value")

    def to_wire(self) -> dict[str, JsonValue]:
        return {"label": self.label, "value": self.value}


@dataclass(frozen=True, slots=True)
class ConditionObservation:
    condition_id: str
    result: bool
    transition_id: str | None
    checked_at_ms: int
    observed_facts: tuple[ObservedFact, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.condition_id, "condition id")
        if self.transition_id is not None:
            _identifier(self.transition_id, "transition id")
        _timestamp(self.checked_at_ms)
        object.__setattr__(self, "observed_facts", tuple(self.observed_facts))
        if len(self.observed_facts) > 16:
            raise IntegrationStatusError("condition observed facts exceed their bound")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "condition_id": self.condition_id,
            "availability": "available",
            "result": self.result,
            "transition_id": self.transition_id,
            "transition_identity": (
                "available" if self.transition_id is not None else "unavailable"
            ),
            "failure_code": None,
            "checked_at_ms": self.checked_at_ms,
            "observed_facts": [item.to_wire() for item in self.observed_facts],
        }


@dataclass(frozen=True, slots=True)
class ConditionUnavailable:
    failure_code: str
    checked_at_ms: int
    condition_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.failure_code, "condition failure code")
        if self.condition_id is not None:
            _identifier(self.condition_id, "condition id")
        _timestamp(self.checked_at_ms)

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "condition_id": self.condition_id,
            "availability": "unavailable",
            "result": None,
            "transition_id": None,
            "transition_identity": "unavailable",
            "failure_code": self.failure_code,
            "checked_at_ms": self.checked_at_ms,
            "observed_facts": [],
        }


@dataclass(slots=True)
class _ObservedTransition:
    result: bool
    transition_id: str | None


class SmartNoteConditionSource:
    """Read authoritative SmartNote decisions and expose stable scoped transitions."""

    def __init__(self, client: MemoryClient, *, scope: Scope, limit: int = 32) -> None:
        if client.scope != scope:
            raise IntegrationScopeError(
                "condition source scope does not match its authenticated memory client"
            )
        if not 1 <= limit <= 32:
            raise IntegrationStatusError("condition source limit must be between 1 and 32")
        self._client = client
        self.scope = scope
        self._limit = limit
        self._transitions: dict[str, _ObservedTransition] = {}
        self._lock = asyncio.Lock()

    async def snapshot(
        self, scope: Scope, session_key: str
    ) -> tuple[ConditionObservation | ConditionUnavailable, ...]:
        if scope != self.scope:
            raise IntegrationScopeError(
                "condition source scope does not match runtime scope"
            )
        session_key = _session_key(session_key)
        async with self._lock:
            return await self._snapshot(session_key)

    async def _snapshot(
        self, session_key: str
    ) -> tuple[ConditionObservation | ConditionUnavailable, ...]:
        suffix = uuid4().hex
        session_digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:12]
        trace = Trace(
            Id(f"condition-trace-{session_digest}-{suffix}"),
            Id(f"condition-request-{suffix}"),
        )
        request = AccessRequest(
            operation=GrantOperation.READ,
            actor_scope=self.scope,
            target_scope=self.scope,
            resource_id=Id("memory-records"),
            trace=trace,
        )
        page = await self._client.smart_note_candidates(request, limit=self._limit)
        checked_at_ms = _now_ms()
        observations: list[ConditionObservation | ConditionUnavailable] = []
        current_keys: set[str] = set()
        for candidate in page.candidates:
            condition_id = _identifier(str(candidate.record_id), "condition id")
            revision_digest = str(candidate.revision_digest)
            predicate_digest = str(candidate.predicate_digest)
            identity = hashlib.sha256(
                f"{condition_id}:{revision_digest}:{predicate_digest}".encode("utf-8")
            ).hexdigest()
            current_keys.add(identity)
            if candidate.last_result is None or candidate.last_evaluated_cursor is None:
                observations.append(
                    ConditionUnavailable(
                        "condition_not_evaluated", checked_at_ms, condition_id
                    )
                )
                continue
            previous = self._transitions.get(identity)
            transition_id = previous.transition_id if previous is not None else None
            if candidate.last_result and (
                previous is None or previous.result != candidate.last_result
            ):
                cursor = candidate.last_evaluated_cursor
                transition_id = hashlib.sha256(
                    (
                        f"{identity}:{str(candidate.last_result).lower()}:"
                        f"{cursor.epoch}:{cursor.sequence}"
                    ).encode("utf-8")
                ).hexdigest()
            elif not candidate.last_result:
                transition_id = None
            self._transitions[identity] = _ObservedTransition(
                candidate.last_result, transition_id
            )
            facts = [
                ObservedFact("Registry", "Canonical SmartNote"),
                ObservedFact(
                    "Evaluation", "Matched" if candidate.last_result else "Not matched"
                ),
                ObservedFact("Revision", revision_digest[:16]),
                ObservedFact("Predicate", predicate_digest[:16]),
                ObservedFact(
                    "Cursor",
                    f"{candidate.last_evaluated_cursor.epoch}:"
                    f"{candidate.last_evaluated_cursor.sequence}",
                ),
            ]
            if candidate.next_evaluation_at_ms is not None:
                facts.append(
                    ObservedFact("Next evaluation", str(candidate.next_evaluation_at_ms))
                )
            observations.append(
                ConditionObservation(
                    condition_id,
                    candidate.last_result,
                    transition_id,
                    checked_at_ms,
                    tuple(facts),
                )
            )
        self._transitions = {
            key: value for key, value in self._transitions.items() if key in current_keys
        }
        return tuple(observations)


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    connection_id: str
    kind: Literal["host", "module_supervisor", "stdio_module", "mcp_bridge", "mcp_module"]
    coverage: Literal["full_host", "tool_bridge"]
    transport: Literal["daemon", "stdio"]
    state: ConnectionState
    available: bool
    display_name: str
    capabilities: tuple[str, ...]
    operations: tuple[IntegrationOperation, ...]
    module_id: str | None = None
    connected_sessions: int | None = None
    catalog_generation: int | None = None
    active_calls: int | None = None
    process_ready: bool | None = None
    failure_code: str | None = None

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "connection_id": self.connection_id,
            "kind": self.kind,
            "coverage": self.coverage,
            "transport": self.transport,
            "state": self.state,
            "available": self.available,
            "display_name": self.display_name,
            "capabilities": list(self.capabilities),
            "operations": list(self.operations),
            "module_id": self.module_id,
            "connected_sessions": self.connected_sessions,
            "catalog_generation": self.catalog_generation,
            "active_calls": self.active_calls,
            "process_ready": self.process_ready,
            "budget": {"state": "unavailable"},
            "fault": {
                "state": "unavailable",
                "code": self.failure_code,
            },
        }


@dataclass(frozen=True, slots=True)
class IntegrationsSnapshot:
    scope: Scope
    connections: tuple[ConnectionSnapshot, ...]
    conditions: tuple[ConditionObservation | ConditionUnavailable, ...]
    checked_at_ms: int

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "scope": self.scope.to_wire(),
            "connections": [item.to_wire() for item in self.connections],
            "conditions": [item.to_wire() for item in self.conditions],
            "checked_at_ms": self.checked_at_ms,
        }


@dataclass(frozen=True, slots=True)
class IntegrationActionResult:
    connection_id: str
    operation: IntegrationOperation
    state: ActionState
    error_code: str | None

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "connection_id": self.connection_id,
            "operation": self.operation,
            "state": self.state,
            "error_code": self.error_code,
        }


class IntegrationStatusFacade:
    def __init__(
        self,
        scope: Scope,
        *,
        lifecycle: object | None = None,
        module_supervisor: HypermidModuleSupervisor | None = None,
        mcp_bridge: McpBridgeStatusSource | None = None,
        condition_source: ConditionStatusSource | None = None,
    ) -> None:
        self.scope = scope
        self._lifecycle = lifecycle
        self._module_supervisor = module_supervisor or getattr(
            lifecycle, "module_supervisor", None
        )
        self._mcp_bridge = mcp_bridge
        self._condition_source = condition_source

    @property
    def condition_source(self) -> ConditionStatusSource | None:
        return self._condition_source

    @classmethod
    def from_runtime(
        cls,
        runtime: object,
        *,
        mcp_bridge: McpBridgeStatusSource | None = None,
        condition_source: ConditionStatusSource | None = None,
    ) -> IntegrationStatusFacade:
        lifecycle = getattr(runtime, "hypermid", None)
        adapter = getattr(lifecycle, "adapter", None)
        client = getattr(adapter, "client", None)
        scope = getattr(client, "scope", None)
        if not isinstance(scope, Scope):
            raise IntegrationStatusError("runtime has no scoped Hypermid lifecycle")
        return cls(
            scope,
            lifecycle=lifecycle,
            mcp_bridge=mcp_bridge,
            condition_source=condition_source,
        )

    async def snapshot(
        self, scope: Scope, session_key: str | None = None
    ) -> IntegrationsSnapshot:
        self._require_scope(scope)
        if session_key is not None:
            _session_key(session_key)
        now_ms = _now_ms()
        connections = [self._host_snapshot(), *self._module_snapshots(session_key)]
        connections.extend(await self._mcp_snapshots())
        conditions = await self._condition_snapshots(scope, session_key, now_ms)
        return IntegrationsSnapshot(
            scope,
            tuple(connections),
            conditions,
            now_ms,
        )

    async def act(
        self,
        scope: Scope,
        session_key: str | None,
        connection_id: str,
        operation: IntegrationOperation,
    ) -> IntegrationActionResult:
        self._require_scope(scope)
        if session_key is not None:
            _session_key(session_key)
        if operation not in ("enable", "disable", "reconnect", "stop"):
            return IntegrationActionResult(
                connection_id, operation, "unsupported", "operation_unsupported"
            )
        try:
            if connection_id == "hypermid-host":
                return await self._act_host(operation)
            if connection_id == "stdio-supervisor":
                return await self._act_supervisor(operation)
            if connection_id == "mcp-bridge":
                return await self._act_mcp(operation)
            known = {
                item.connection_id
                for item in (await self.snapshot(scope, session_key)).connections
            }
            code = "operation_unsupported" if connection_id in known else "connection_unknown"
            return IntegrationActionResult(connection_id, operation, "unsupported", code)
        except Exception:
            return IntegrationActionResult(
                connection_id, operation, "failed", "lifecycle_failed"
            )

    def _host_snapshot(self) -> ConnectionSnapshot:
        lifecycle = self._lifecycle
        adapter = getattr(lifecycle, "adapter", None)
        if lifecycle is None or adapter is None:
            return ConnectionSnapshot(
                "hypermid-host",
                "host",
                "full_host",
                "daemon",
                "unavailable",
                False,
                "Hypermid host",
                (),
                (),
                failure_code="host_lifecycle_unavailable",
            )
        status = adapter.status()
        availability = str(getattr(status, "availability", "unavailable"))
        state: ConnectionState = (
            availability
            if availability in {"disabled", "stopped", "unavailable"}
            else "ready" if bool(getattr(status, "available", False))
            else "disconnected"
        )
        return ConnectionSnapshot(
            "hypermid-host",
            "host",
            "full_host",
            "daemon",
            state,
            bool(getattr(status, "available", False)),
            "Hypermid host",
            tuple(getattr(status, "capabilities", ()) or ()),
            ("enable", "disable", "reconnect", "stop"),
            failure_code=getattr(status, "failure_code", None),
        )

    def _module_snapshots(self, session_key: str | None) -> list[ConnectionSnapshot]:
        supervisor = self._module_supervisor
        if supervisor is None:
            return [
                ConnectionSnapshot(
                    "stdio-supervisor",
                    "module_supervisor",
                    "tool_bridge",
                    "stdio",
                    "unavailable",
                    False,
                    "Supervised stdio modules",
                    (),
                    (),
                    failure_code="module_supervisor_unavailable",
                )
            ]
        result = [
            ConnectionSnapshot(
                "stdio-supervisor",
                "module_supervisor",
                "tool_bridge",
                "stdio",
                "ready" if supervisor.enabled else "disabled",
                supervisor.enabled,
                "Supervised stdio modules",
                ("catalog", "invoke", "cancel", "health"),
                ("enable", "disable", "stop"),
            )
        ]
        if session_key is None:
            return result
        digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
        for health in supervisor.health():
            if health.session_key_digest != digest:
                continue
            result.append(
                ConnectionSnapshot(
                    f"stdio:{health.module_id}",
                    "stdio_module",
                    "tool_bridge",
                    "stdio",
                    "connected" if health.connected else "disconnected",
                    health.connected,
                    health.provider_name,
                    ("catalog", "invoke", "cancel", "health"),
                    (),
                    module_id=health.module_id,
                    catalog_generation=health.catalog_generation,
                    active_calls=health.active_calls,
                    process_ready=health.connected,
                )
            )
        return result

    async def _mcp_snapshots(self) -> list[ConnectionSnapshot]:
        source = self._mcp_bridge
        if source is None:
            return [
                ConnectionSnapshot(
                    "mcp-bridge",
                    "mcp_bridge",
                    "tool_bridge",
                    "stdio",
                    "unavailable",
                    False,
                    "Daemon MCP bridge",
                    (),
                    (),
                    failure_code="mcp_bridge_unavailable",
                )
            ]
        snapshots = list(await source.health())
        result = [
            ConnectionSnapshot(
                "mcp-bridge",
                "mcp_bridge",
                "tool_bridge",
                "stdio",
                "ready",
                True,
                "Daemon MCP bridge",
                ("catalog", "invoke", "cancel", "health"),
                ("stop",),
            )
        ]
        for value in snapshots:
            module_id = _identifier(getattr(value, "module_id", ""), "MCP module id")
            ready = bool(getattr(value, "process_ready", False))
            sessions = _count(getattr(value, "connected_sessions", 0))
            result.append(
                ConnectionSnapshot(
                    f"mcp:{module_id}",
                    "mcp_module",
                    "tool_bridge",
                    "stdio",
                    "connected" if sessions else "ready" if ready else "disconnected",
                    ready,
                    module_id,
                    ("catalog", "invoke", "cancel", "health"),
                    (),
                    module_id=module_id,
                    connected_sessions=sessions,
                    catalog_generation=_count(
                        getattr(value, "catalog_generation", 0)
                    ),
                    active_calls=_count(getattr(value, "active_calls", 0)),
                    process_ready=ready,
                )
            )
        return result

    async def _condition_snapshots(
        self, scope: Scope, session_key: str | None, now_ms: int
    ) -> tuple[ConditionObservation | ConditionUnavailable, ...]:
        source = self._condition_source
        if session_key is None:
            return (ConditionUnavailable("condition_session_unavailable", now_ms),)
        if source is None:
            return (ConditionUnavailable("condition_source_unavailable", now_ms),)
        try:
            value = source.snapshot(scope, session_key)
            observations = await value if inspect.isawaitable(value) else value
        except IntegrationScopeError:
            raise
        except Exception:
            return (ConditionUnavailable("condition_source_failed", now_ms),)
        bounded = tuple(observations)
        if len(bounded) > 32:
            raise IntegrationStatusError("condition snapshot exceeds its bound")
        return bounded or (ConditionUnavailable("condition_snapshot_empty", now_ms),)

    async def _act_host(
        self, operation: IntegrationOperation
    ) -> IntegrationActionResult:
        lifecycle = self._lifecycle
        if lifecycle is None:
            return IntegrationActionResult(
                "hypermid-host", operation, "unavailable", "host_lifecycle_unavailable"
            )
        if operation == "enable":
            await lifecycle.start()
        elif operation in ("disable", "stop"):
            await lifecycle.stop()
        elif operation == "reconnect":
            adapter = lifecycle.adapter
            await adapter.stop()
            await lifecycle.start()
        return IntegrationActionResult("hypermid-host", operation, "applied", None)

    async def _act_supervisor(
        self, operation: IntegrationOperation
    ) -> IntegrationActionResult:
        supervisor = self._module_supervisor
        if supervisor is None:
            return IntegrationActionResult(
                "stdio-supervisor",
                operation,
                "unavailable",
                "module_supervisor_unavailable",
            )
        if operation == "enable":
            supervisor.enable()
        elif operation in ("disable", "stop"):
            await supervisor.disable()
        else:
            return IntegrationActionResult(
                "stdio-supervisor", operation, "unsupported", "operation_unsupported"
            )
        return IntegrationActionResult("stdio-supervisor", operation, "applied", None)

    async def _act_mcp(
        self, operation: IntegrationOperation
    ) -> IntegrationActionResult:
        source = self._mcp_bridge
        if source is None:
            return IntegrationActionResult(
                "mcp-bridge", operation, "unavailable", "mcp_bridge_unavailable"
            )
        if operation != "stop":
            return IntegrationActionResult(
                "mcp-bridge", operation, "unsupported", "operation_unsupported"
            )
        await source.shutdown(_now_ms())
        return IntegrationActionResult("mcp-bridge", operation, "applied", None)

    def _require_scope(self, scope: Scope) -> None:
        if scope != self.scope:
            raise IntegrationScopeError("integration scope does not match runtime scope")


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise IntegrationStatusError(f"{name} is invalid")
    if any(not character.isprintable() for character in value):
        raise IntegrationStatusError(f"{name} is invalid")
    return value


def _session_key(value: object) -> str:
    return _identifier(value, "session key")


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
        raise IntegrationStatusError("integration count is invalid")
    return value


def _timestamp(value: object) -> int:
    return _count(value)


__all__ = [
    "ConditionObservation",
    "ConditionStatusSource",
    "ConditionUnavailable",
    "ConnectionSnapshot",
    "IntegrationActionResult",
    "IntegrationScopeError",
    "IntegrationStatusError",
    "IntegrationStatusFacade",
    "IntegrationsSnapshot",
    "McpBridgeStatusSource",
    "ObservedFact",
    "SmartNoteConditionSource",
]
