from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from threading import Lock
from typing import Mapping, Protocol

from .foundation import Cursor, Digest, Id, Scope, Trace
from .models import MAX_FRAME_BYTES, Envelope, Principal
from .transport import (
    BoundMemoryRequest,
    MemoryEventBatch,
    MemoryRequest,
    MemoryResponse,
    MemoryTransportViolation,
    bind_authenticated_request,
)


class BusViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BusMessage:
    subject: str
    id: Id
    digest: Digest
    headers: Mapping[str, str]
    scope: Scope
    trace: Trace

    def __post_init__(self) -> None:
        if not 1 <= len(self.subject) <= 512:
            raise BusViolation("subject length is outside 1..=512")
        if len(self.headers) > 64 or any(not key or len(key) > 128 or len(value) > 4096 for key, value in self.headers.items()):
            raise BusViolation("headers exceed the bounded wire contract")

    def to_wire(self) -> dict[str, object]:
        return {"subject": self.subject, "id": str(self.id), "digest": str(self.digest), "headers": dict(self.headers), "scope": self.scope.to_wire(), "trace": self.trace.to_wire()}


@dataclass(frozen=True, slots=True)
class Delivery:
    cursor: Cursor
    delivery_count: int
    message: BusMessage


class BackendProperty(str, Enum):
    PROCESS_PERSISTENCE = "process_persistence"
    LEAF_TOPOLOGY = "leaf_topology"
    SERVER_GRANTS = "server_grants"
    SYSTEM_EVENTS = "system_events"


@dataclass(frozen=True, slots=True)
class PropertyClassification:
    applicable: bool
    reason: str | None = None


def deterministic_classifications() -> dict[BackendProperty, PropertyClassification]:
    reason = "requires a provisioned NATS server boundary"
    return {property: PropertyClassification(False, reason) for property in BackendProperty}


@dataclass(slots=True)
class CensusGuard:
    revision: int | None = None
    present: set[str] = field(default_factory=set)

    def snapshot(self, revision: int, keys: set[str]) -> None:
        self.revision, self.present = revision, set(keys)

    def deleted(self, revision: int, key: str) -> None:
        self.revision = revision
        self.present.discard(key)

    def disconnect(self) -> None:
        self.revision = None
        self.present.clear()

    def proven_absent(self, key: str) -> bool:
        return self.revision is not None and key not in self.present


class MemoryEndpoint(Protocol):
    def acknowledged(self, request: BoundMemoryRequest) -> MemoryResponse | None: ...

    def execute(self, request: BoundMemoryRequest) -> MemoryResponse: ...

    def resume(
        self, scope: Scope, after: Cursor, maximum_events: int
    ) -> MemoryEventBatch: ...


@dataclass(frozen=True, slots=True)
class MemoryBusLimits:
    max_inflight: int
    max_request_bytes: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_inflight <= 65_535:
            raise MemoryTransportViolation(
                "MEMORY_BUS_LIMIT_INVALID", "the memory bus inflight limit is invalid"
            )
        if not 1 <= self.max_request_bytes <= MAX_FRAME_BYTES:
            raise MemoryTransportViolation(
                "MEMORY_BUS_LIMIT_INVALID", "the memory bus byte limit is invalid"
            )


def _execute_once(endpoint: MemoryEndpoint, request: BoundMemoryRequest) -> MemoryResponse:
    if request.request.operation.mutates:
        acknowledged = endpoint.acknowledged(request)
        if acknowledged is not None:
            return acknowledged.replay()
    return endpoint.execute(request)


def dispatch_memory_in_process(
    endpoint: MemoryEndpoint,
    principal: Principal,
    authenticated_scope: Scope,
    request: MemoryRequest,
) -> MemoryResponse:
    bound = bind_authenticated_request(principal, authenticated_scope, request.envelope())
    return _execute_once(endpoint, bound)


class AuthenticatedMemoryBus:
    def __init__(
        self,
        endpoint: MemoryEndpoint,
        principal: Principal,
        authenticated_scope: Scope,
        limits: MemoryBusLimits,
    ) -> None:
        self._endpoint = endpoint
        self._principal = principal
        self._authenticated_scope = authenticated_scope
        self._limits = limits
        self._inflight = 0
        self._cancelled: set[Id] = set()
        self._connected = True
        self._lock = Lock()

    def cancel(self, message_id: Id) -> bool:
        with self._lock:
            before = len(self._cancelled)
            self._cancelled.add(Id(message_id))
            return len(self._cancelled) != before

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._cancelled.clear()

    def reconnect(self) -> None:
        with self._lock:
            self._connected = True

    def dispatch(self, envelope: Envelope) -> MemoryResponse:
        encoded = json.dumps(
            envelope.to_wire(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self._limits.max_request_bytes:
            raise MemoryTransportViolation(
                "MEMORY_BACKPRESSURE", "the memory request exceeds its byte credit"
            )
        decoded = Envelope.from_wire(json.loads(encoded))
        with self._lock:
            if not self._connected:
                raise MemoryTransportViolation(
                    "MEMORY_BUS_DISCONNECTED", "the memory bus is disconnected"
                )
            if decoded.message_id in self._cancelled:
                self._cancelled.remove(decoded.message_id)
                raise MemoryTransportViolation(
                    "MEMORY_REQUEST_CANCELLED",
                    "the memory request was cancelled before dispatch",
                )
            if self._inflight >= self._limits.max_inflight:
                raise MemoryTransportViolation(
                    "MEMORY_BACKPRESSURE", "the memory bus has no request credit"
                )
            self._inflight += 1
        try:
            bound = bind_authenticated_request(
                self._principal, self._authenticated_scope, decoded
            )
            return _execute_once(self._endpoint, bound)
        finally:
            with self._lock:
                self._inflight -= 1

    def resume(self, after: Cursor, maximum_events: int) -> MemoryEventBatch:
        if not 1 <= maximum_events <= self._limits.max_inflight:
            raise MemoryTransportViolation(
                "MEMORY_BACKPRESSURE",
                "the requested event credit exceeds the memory bus limit",
            )
        with self._lock:
            if not self._connected:
                raise MemoryTransportViolation(
                    "MEMORY_BUS_DISCONNECTED", "the memory bus is disconnected"
                )
        batch = self._endpoint.resume(
            self._authenticated_scope, after, maximum_events
        )
        batch.validate(maximum_events)
        return batch
