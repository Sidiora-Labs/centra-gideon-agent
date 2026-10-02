from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread

import pytest

from gideon.hypermid.bus import (
    AuthenticatedMemoryBus,
    MemoryBusLimits,
    dispatch_memory_in_process,
)
from gideon.hypermid.foundation import Cursor, Digest, Id, Scope, Trace
from gideon.hypermid.models import Principal
from gideon.hypermid.transport import (
    BoundMemoryRequest,
    EmbeddingVectorWire,
    MemoryEvent,
    MemoryEventBatch,
    MemoryOperation,
    MemoryRequest,
    MemoryResponse,
    MemoryTransportViolation,
)


class _JournalEndpoint:
    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        self.cursor = Cursor(1, 0)
        self.receipts: dict[Id, MemoryResponse] = {}
        self.events: list[MemoryEvent] = []
        self.commits = 0
        self.entered: Event | None = None
        self.release: Event | None = None

    def acknowledged(self, request: BoundMemoryRequest) -> MemoryResponse | None:
        key = request.request.idempotency_key
        if key is None:
            return None
        response = self.receipts.get(key)
        if response is not None and response.payload["resource_id"] != request.request.resource_id:
            raise MemoryTransportViolation(
                "MEMORY_IDEMPOTENCY_CONFLICT",
                "the idempotency key was used for another memory resource",
            )
        return response

    def execute(self, bound: BoundMemoryRequest) -> MemoryResponse:
        if self.entered is not None and self.release is not None:
            self.entered.set()
            assert self.release.wait(2)
        request = bound.request
        if request.operation.mutates:
            self.commits += 1
            self.cursor = self.cursor.next()
            response = MemoryResponse(
                request.trace,
                self.cursor,
                {"resource_id": str(request.resource_id), "commits": self.commits},
            )
            assert request.idempotency_key is not None
            self.receipts[request.idempotency_key] = response
            self.events.append(
                MemoryEvent(
                    self.cursor,
                    request.trace,
                    request.operation,
                    request.resource_id,
                    response.payload,
                )
            )
            return response
        return MemoryResponse(
            request.trace,
            self.cursor,
            {"resource_id": str(request.resource_id), "commits": self.commits},
        )

    def resume(
        self, scope: Scope, after: Cursor, maximum_events: int
    ) -> MemoryEventBatch:
        if scope != self.scope or after.epoch != self.cursor.epoch:
            raise MemoryTransportViolation(
                "AUTHORIZATION_DENIED", "stream scope or epoch was refused"
            )
        events = tuple(
            event for event in self.events if event.cursor.sequence > after.sequence
        )[:maximum_events]
        next_cursor = events[-1].cursor if events else after
        return MemoryEventBatch(after, events, next_cursor, len(events) < maximum_events)


def _request(
    scope: Scope,
    request_id: str,
    *,
    operation: MemoryOperation = MemoryOperation.CREATE,
    resource_id: str = "record-1",
) -> MemoryRequest:
    trace = Trace(Id("trace-1"), Id(request_id))
    mutates = operation.mutates
    return MemoryRequest(
        operation=operation,
        actor_scope=scope,
        target_scope=scope,
        resource_id=Id(resource_id),
        capability_id=Id("capability-1"),
        trace=trace,
        expected_cursor=Cursor(1, 0) if mutates else None,
        idempotency_key=trace.request_id if mutates else None,
        payload={"content": "transport conformance"},
    )


def _code(error: pytest.ExceptionInfo[MemoryTransportViolation]) -> str:
    return error.value.error.code


def test_authenticated_memory_transport_conformance_and_replay() -> None:
    scope = Scope(Id("owner-1"), Id("project-1"), Id("workspace-1"))
    principal = Principal(
        Id("gideon-1"),
        "service",
        tuple(operation.value for operation in MemoryOperation),
    )
    request = _request(scope, "mutation-1")

    direct_endpoint = _JournalEndpoint(scope)
    direct = dispatch_memory_in_process(direct_endpoint, principal, scope, request)
    bus_endpoint = _JournalEndpoint(scope)
    bus = AuthenticatedMemoryBus(
        bus_endpoint,
        principal,
        scope,
        MemoryBusLimits(max_inflight=2, max_request_bytes=4096),
    )
    remote = bus.dispatch(request.envelope())
    assert remote == direct
    assert remote.trace == request.trace
    assert remote.cursor == Cursor(1, 1)

    bus.disconnect()
    with pytest.raises(MemoryTransportViolation) as disconnected:
        bus.dispatch(request.envelope())
    assert _code(disconnected) == "MEMORY_BUS_DISCONNECTED"
    bus.reconnect()
    replay = bus.dispatch(request.envelope())
    assert replay.replayed is True
    assert replay.cursor == remote.cursor
    assert bus_endpoint.commits == 1

    batch = bus.resume(Cursor(1, 0), 2)
    assert [event.cursor for event in batch.events] == [Cursor(1, 1)]
    assert batch.next_cursor == Cursor(1, 1)
    assert batch.terminated is True
    empty = bus.resume(batch.next_cursor, 2)
    assert empty.events == ()
    assert empty.next_cursor == batch.next_cursor

    forged_scope = Scope(Id("owner-2"), Id("project-2"))
    forged = replace(request, actor_scope=forged_scope, target_scope=forged_scope)
    with pytest.raises(MemoryTransportViolation) as denied:
        bus.dispatch(forged.envelope())
    assert _code(denied) == "AUTHORIZATION_DENIED"

    cancelled = _request(scope, "cancelled-1", operation=MemoryOperation.RECORD_READ)
    assert bus.cancel(cancelled.trace.request_id) is True
    with pytest.raises(MemoryTransportViolation) as cancellation:
        bus.dispatch(cancelled.envelope())
    assert _code(cancellation) == "MEMORY_REQUEST_CANCELLED"

    small_bus = AuthenticatedMemoryBus(
        _JournalEndpoint(scope),
        principal,
        scope,
        MemoryBusLimits(max_inflight=1, max_request_bytes=64),
    )
    with pytest.raises(MemoryTransportViolation) as byte_pressure:
        small_bus.dispatch(cancelled.envelope())
    assert _code(byte_pressure) == "MEMORY_BACKPRESSURE"

    blocking_endpoint = _JournalEndpoint(scope)
    blocking_endpoint.entered, blocking_endpoint.release = Event(), Event()
    concurrent_bus = AuthenticatedMemoryBus(
        blocking_endpoint,
        principal,
        scope,
        MemoryBusLimits(max_inflight=1, max_request_bytes=4096),
    )
    first = Thread(target=lambda: concurrent_bus.dispatch(cancelled.envelope()))
    first.start()
    assert blocking_endpoint.entered.wait(2)
    concurrent = _request(scope, "read-2", operation=MemoryOperation.RECORD_READ)
    with pytest.raises(MemoryTransportViolation) as inflight_pressure:
        concurrent_bus.dispatch(concurrent.envelope())
    assert _code(inflight_pressure) == "MEMORY_BACKPRESSURE"
    blocking_endpoint.release.set()
    first.join(2)
    assert not first.is_alive()

    with pytest.raises(MemoryTransportViolation) as secret:
        replace(request, payload={"api_key": "must-not-cross"})
    assert _code(secret) == "MEMORY_SECRET_FIELD_REFUSED"

    vector = EmbeddingVectorWire.encode(
        Id("registration-1"),
        Digest.sha256(b"registration"),
        Digest.sha256(b"input"),
        (0.25, -0.5, 0.75),
    )
    assert vector.decode() == pytest.approx((0.25, -0.5, 0.75))
