from __future__ import annotations

import base64
import binascii
import json
import math
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .foundation import Cursor, Digest, EffectState, Error, Id, Scope, Trace
from .models import Envelope, JsonValue, Principal

MEMORY_PROTOCOL = "memory.v1"
MAX_MEMORY_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_VECTOR_DIMENSIONS = 65_536


class MemoryTransportViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.error = Error(code, message, False, effect_state=EffectState.NOT_STARTED)


class MemoryOperation(str, Enum):
    RECORD_READ = "memory.record.read"
    SEARCH = "memory.search"
    EMBEDDING_QUERY = "memory.embedding.query"
    CREATE = "memory.record.create"
    UPDATE = "memory.record.update"
    ARCHIVE = "memory.record.archive"
    RESTORE = "memory.record.restore"
    MERGE = "memory.record.merge"
    SPLIT = "memory.record.split"
    RELOCATE = "memory.record.relocate"
    DELETE = "memory.record.delete"
    PURGE = "memory.record.purge"
    VERIFY = "memory.record.verify"
    EMBED = "memory.embedding.publish"
    INDEX = "memory.index.enqueue"
    SUMMARIZE = "memory.summary.enqueue"
    IMPORT = "memory.import.prepare"
    EXPORT = "memory.export.prepare"
    MAINTENANCE = "memory.maintenance"
    DIAGNOSTICS = "memory.diagnostics"

    @property
    def mutates(self) -> bool:
        return self not in {
            self.RECORD_READ,
            self.SEARCH,
            self.EMBEDDING_QUERY,
            self.DIAGNOSTICS,
        }

    @property
    def mutation_event_name(self) -> str | None:
        return {
            self.CREATE: "create",
            self.UPDATE: "update",
            self.ARCHIVE: "archive",
            self.RESTORE: "restore",
            self.MERGE: "merge",
            self.SPLIT: "split",
            self.RELOCATE: "relocate",
            self.DELETE: "delete",
            self.PURGE: "purge",
            self.VERIFY: "verify",
            self.EMBED: "embed",
            self.INDEX: "index",
            self.SUMMARIZE: "summarize",
            self.IMPORT: "import",
            self.EXPORT: "export",
        }.get(self)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MemoryTransportViolation(
            "MEMORY_PAYLOAD_INVALID", "the memory payload cannot be encoded"
        ) from exc


def _reject_secrets(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in {
                "secret",
                "password",
                "api_key",
                "credential",
                "access_token",
            } or normalized.endswith(("_secret", "_password")):
                raise MemoryTransportViolation(
                    "MEMORY_SECRET_FIELD_REFUSED",
                    "secret-bearing fields cannot cross the memory transport",
                )
            _reject_secrets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secrets(child)


def _reject_non_integer_numbers(value: object) -> None:
    if isinstance(value, float):
        raise MemoryTransportViolation(
            "MEMORY_PAYLOAD_INVALID",
            "memory transport numbers must be canonical integers",
        )
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_non_integer_numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_non_integer_numbers(child)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryTransportViolation(
            "MEMORY_REQUEST_INVALID", f"{name} must be an object"
        )
    return value


@dataclass(frozen=True, slots=True)
class MemoryRequest:
    operation: MemoryOperation
    actor_scope: Scope
    target_scope: Scope
    resource_id: Id
    capability_id: Id
    trace: Trace
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    expected_cursor: Cursor | None = None
    idempotency_key: Id | None = None
    version: str = MEMORY_PROTOCOL

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", MemoryOperation(self.operation))
        object.__setattr__(self, "resource_id", Id(self.resource_id))
        object.__setattr__(self, "capability_id", Id(self.capability_id))
        if self.idempotency_key is not None:
            object.__setattr__(self, "idempotency_key", Id(self.idempotency_key))
        if self.version != MEMORY_PROTOCOL:
            raise MemoryTransportViolation(
                "MEMORY_PROTOCOL_UNSUPPORTED",
                "the memory protocol version is unsupported",
            )
        if self.operation.mutates and (
            self.expected_cursor is None or self.idempotency_key is None
        ):
            raise MemoryTransportViolation(
                "MEMORY_MUTATION_FIELDS_REQUIRED",
                "memory mutations require an expected cursor and idempotency key",
            )
        if self.operation.mutates and self.idempotency_key != self.trace.request_id:
            raise MemoryTransportViolation(
                "MEMORY_IDEMPOTENCY_MISMATCH",
                "the mutation idempotency key must equal the trace request id",
            )
        payload_bytes = _canonical_bytes(self.payload)
        if len(payload_bytes) > MAX_MEMORY_PAYLOAD_BYTES:
            raise MemoryTransportViolation(
                "MEMORY_PAYLOAD_TOO_LARGE", "the memory payload exceeds four MiB"
            )
        _reject_non_integer_numbers(self.payload)
        _reject_secrets(self.payload)

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "version": self.version,
            "operation": self.operation.name.lower(),
            "actor_scope": self.actor_scope.to_wire(),
            "target_scope": self.target_scope.to_wire(),
            "resource_id": str(self.resource_id),
            "capability_id": str(self.capability_id),
            "trace": self.trace.to_wire(),
            "payload": dict(self.payload),
        }
        if self.expected_cursor is not None:
            result["expected_cursor"] = self.expected_cursor.to_wire()
        if self.idempotency_key is not None:
            result["idempotency_key"] = str(self.idempotency_key)
        return result

    @classmethod
    def from_wire(cls, value: object) -> MemoryRequest:
        raw = _mapping(value, "memory request")
        allowed = {
            "version",
            "operation",
            "actor_scope",
            "target_scope",
            "resource_id",
            "capability_id",
            "trace",
            "payload",
            "expected_cursor",
            "idempotency_key",
        }
        if set(raw) - allowed:
            raise MemoryTransportViolation(
                "MEMORY_REQUEST_INVALID", "the memory request has unknown fields"
            )
        try:
            return cls(
                version=raw["version"],
                operation=MemoryOperation[raw["operation"].upper()],
                actor_scope=Scope.from_wire(
                    _mapping(raw["actor_scope"], "actor_scope")
                ),
                target_scope=Scope.from_wire(
                    _mapping(raw["target_scope"], "target_scope")
                ),
                resource_id=Id(raw["resource_id"]),
                capability_id=Id(raw["capability_id"]),
                trace=Trace.from_wire(_mapping(raw["trace"], "trace")),
                payload=_mapping(raw.get("payload", {}), "payload"),
                expected_cursor=(
                    Cursor.from_wire(
                        _mapping(raw["expected_cursor"], "expected_cursor")
                    )
                    if raw.get("expected_cursor") is not None
                    else None
                ),
                idempotency_key=(
                    Id(raw["idempotency_key"])
                    if raw.get("idempotency_key") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, MemoryTransportViolation):
                raise
            raise MemoryTransportViolation(
                "MEMORY_REQUEST_INVALID", "the memory request document is invalid"
            ) from exc

    def envelope(self) -> Envelope:
        return Envelope(
            kind="request",
            message_id=self.trace.request_id,
            sequence=1,
            route_id=Id("memory"),
            route_epoch=1,
            operation=self.operation.value,
            scope=self.actor_scope,
            trace=self.trace,
            deadline_ms=30_000,
            payload=self.to_wire(),
        )


@dataclass(frozen=True, slots=True)
class BoundMemoryRequest:
    principal: Principal
    authenticated_scope: Scope
    request: MemoryRequest


def bind_authenticated_request(
    principal: Principal,
    authenticated_scope: Scope,
    envelope: Envelope,
) -> BoundMemoryRequest:
    if envelope.kind != "request" or envelope.payload is None:
        raise MemoryTransportViolation(
            "MEMORY_ENVELOPE_INVALID", "memory calls require a request envelope"
        )
    request = MemoryRequest.from_wire(envelope.payload)
    if (
        request.actor_scope != authenticated_scope
        or envelope.scope != authenticated_scope
        or envelope.trace != request.trace
        or envelope.message_id != request.trace.request_id
        or envelope.operation != request.operation.value
        or request.operation.value not in principal.scopes
    ):
        raise MemoryTransportViolation(
            "AUTHORIZATION_DENIED",
            "the authenticated transport identity does not authorize this memory request",
        )
    return BoundMemoryRequest(principal, authenticated_scope, request)


@dataclass(frozen=True, slots=True)
class MemoryResponse:
    trace: Trace
    cursor: Cursor
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    replayed: bool = False
    version: str = MEMORY_PROTOCOL

    def replay(self) -> MemoryResponse:
        return MemoryResponse(self.trace, self.cursor, self.payload, True, self.version)


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    cursor: Cursor
    trace: Trace
    operation: MemoryOperation
    resource_id: Id
    payload: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryEventBatch:
    resumed_after: Cursor
    events: Sequence[MemoryEvent]
    next_cursor: Cursor
    terminated: bool
    version: str = MEMORY_PROTOCOL

    def validate(self, maximum_events: int) -> None:
        if (
            self.version != MEMORY_PROTOCOL
            or not 1 <= maximum_events
            or len(self.events) > maximum_events
        ):
            raise MemoryTransportViolation(
                "MEMORY_STREAM_INVALID",
                "the memory stream version or event bound is invalid",
            )
        previous = self.resumed_after
        for event in self.events:
            if (
                event.cursor.epoch != previous.epoch
                or event.cursor.sequence <= previous.sequence
            ):
                raise MemoryTransportViolation(
                    "MEMORY_STREAM_INVALID",
                    "memory stream cursors are replayed, reordered, or cross epochs",
                )
            previous = event.cursor
        if self.next_cursor != previous:
            raise MemoryTransportViolation(
                "MEMORY_STREAM_INVALID",
                "the memory stream continuation cursor is invalid",
            )


@dataclass(frozen=True, slots=True)
class EmbeddingVectorWire:
    registration_id: Id
    fingerprint: Digest
    input_digest: Digest
    dimensions: int
    vector_base64: str
    encoding: str = "f32le-base64"

    @classmethod
    def encode(
        cls,
        registration_id: Id,
        fingerprint: Digest,
        input_digest: Digest,
        vector: Sequence[float],
    ) -> EmbeddingVectorWire:
        values = tuple(float(value) for value in vector)
        if not values or len(values) > MAX_VECTOR_DIMENSIONS:
            raise MemoryTransportViolation(
                "MEMORY_VECTOR_INVALID", "invalid vector dimensions"
            )
        payload = struct.pack(f"<{len(values)}f", *values)
        result = cls(
            Id(registration_id),
            Digest(fingerprint),
            Digest(input_digest),
            len(values),
            base64.b64encode(payload).decode("ascii"),
        )
        result.decode()
        return result

    def decode(self) -> tuple[float, ...]:
        if (
            self.encoding != "f32le-base64"
            or not 1 <= self.dimensions <= MAX_VECTOR_DIMENSIONS
        ):
            raise MemoryTransportViolation(
                "MEMORY_VECTOR_INVALID",
                "the embedding vector encoding or dimensions are invalid",
            )
        try:
            payload = base64.b64decode(self.vector_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MemoryTransportViolation(
                "MEMORY_VECTOR_INVALID", "the embedding vector is not canonical base64"
            ) from exc
        if len(payload) != self.dimensions * 4:
            raise MemoryTransportViolation(
                "MEMORY_VECTOR_INVALID",
                "the embedding vector byte length does not match",
            )
        values = struct.unpack(f"<{self.dimensions}f", payload)
        if (
            any(not math.isfinite(value) for value in values)
            or math.fsum(value * value for value in values) <= 0
        ):
            raise MemoryTransportViolation(
                "MEMORY_VECTOR_INVALID",
                "the embedding vector is non-finite or has zero norm",
            )
        return values
