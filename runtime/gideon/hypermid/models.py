"""Typed values shared by the Hypermid Python wire client."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, TypeAlias

from .foundation import (
    MAX_SAFE_INTEGER,
    Cursor,
    Digest,
    EffectState,
    Error,
    Id,
    Scope,
    Trace,
)

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
EnvelopeKind = Literal[
    "request", "response", "event", "credit", "cancel", "ping", "pong", "close"
]

PROTOCOL = "hypermid.v1"
MAX_FRAME_BYTES = 8 * 1024 * 1024
EVENT_CHUNK_BYTES = 64 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _string(value: object, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string at most {maximum} characters")
    return value


def _identifier(value: object, name: str) -> Id:
    result = _string(value, name, maximum=160)
    if _ID_RE.fullmatch(result) is None:
        raise ValueError(f"{name} is not a valid Hypermid identifier")
    return Id(result)


def _integer(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} is outside the supported range")
    return value


@dataclass(frozen=True, slots=True)
class Principal:
    id: Id
    kind: Literal["local_user", "supervised_module", "device", "service"]
    scopes: tuple[str, ...]
    module_id: Id | None = None
    spawn_generation: int | None = None

    @classmethod
    def from_wire(cls, value: object) -> Principal:
        raw = _mapping(value, "principal")
        kind = raw.get("kind")
        if kind not in ("local_user", "supervised_module", "device", "service"):
            raise ValueError("principal.kind is invalid")
        scopes = raw.get("scopes")
        if not isinstance(scopes, list) or len(scopes) > 256:
            raise ValueError("principal.scopes must be a bounded array")
        parsed_scopes = tuple(_string(item, "principal scope", maximum=160) for item in scopes)
        if len(set(parsed_scopes)) != len(parsed_scopes):
            raise ValueError("principal.scopes must be unique")
        module_id = raw.get("module_id")
        generation = raw.get("spawn_generation")
        return cls(
            id=_identifier(raw.get("id"), "principal.id"),
            kind=kind,
            scopes=parsed_scopes,
            module_id=(
                _identifier(module_id, "principal.module_id")
                if module_id is not None
                else None
            ),
            spawn_generation=(
                _integer(
                    generation,
                    "principal.spawn_generation",
                    minimum=1,
                    maximum=MAX_SAFE_INTEGER,
                )
                if generation is not None
                else None
            ),
        )

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "id": self.id,
            "kind": self.kind,
            "scopes": list(self.scopes),
        }
        if self.module_id is not None:
            result["module_id"] = self.module_id
        if self.spawn_generation is not None:
            result["spawn_generation"] = self.spawn_generation
        return result


@dataclass(frozen=True, slots=True)
class ConnectionLimits:
    max_frame_bytes: int
    event_chunk_bytes: int
    max_routes: int
    max_inflight_requests: int

    def __post_init__(self) -> None:
        if self.max_frame_bytes != MAX_FRAME_BYTES:
            raise ValueError("unsupported maximum frame size")
        if self.event_chunk_bytes != EVENT_CHUNK_BYTES:
            raise ValueError("unsupported event chunk size")
        _integer(self.max_routes, "max_routes", minimum=1, maximum=65_535)
        _integer(
            self.max_inflight_requests,
            "max_inflight_requests",
            minimum=1,
            maximum=65_535,
        )

    @classmethod
    def from_wire(cls, value: object) -> ConnectionLimits:
        raw = _mapping(value, "limits")
        return cls(
            max_frame_bytes=_integer(
                raw.get("max_frame_bytes"),
                "max_frame_bytes",
                minimum=1,
                maximum=MAX_FRAME_BYTES,
            ),
            event_chunk_bytes=_integer(
                raw.get("event_chunk_bytes"),
                "event_chunk_bytes",
                minimum=1,
                maximum=EVENT_CHUNK_BYTES,
            ),
            max_routes=_integer(
                raw.get("max_routes"), "max_routes", minimum=1, maximum=65_535
            ),
            max_inflight_requests=_integer(
                raw.get("max_inflight_requests"),
                "max_inflight_requests",
                minimum=1,
                maximum=65_535,
            ),
        )


@dataclass(frozen=True, slots=True)
class SessionAccepted:
    session_id: Id
    principal: Principal
    limits: ConnectionLimits
    server_time_ms: int

    @classmethod
    def from_wire(cls, value: object) -> SessionAccepted:
        raw = _mapping(value, "accepted session")
        if raw.get("kind") != "accepted" or raw.get("protocol") != PROTOCOL:
            raise ValueError("server did not accept hypermid.v1")
        return cls(
            session_id=_identifier(raw.get("session_id"), "session_id"),
            principal=Principal.from_wire(raw.get("principal")),
            limits=ConnectionLimits.from_wire(raw.get("limits")),
            server_time_ms=_integer(
                raw.get("server_time_ms"),
                "server_time_ms",
                minimum=0,
                maximum=MAX_SAFE_INTEGER,
            ),
        )


@dataclass(frozen=True, slots=True)
class Envelope:
    kind: EnvelopeKind
    message_id: Id
    sequence: int
    reply_to: Id | None = None
    route_id: Id | None = None
    route_epoch: int | None = None
    operation: str | None = None
    scope: Scope | None = None
    trace: Trace | None = None
    deadline_ms: int | None = None
    payload: JsonValue = None
    error: Error | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _identifier(self.message_id, "message_id"))
        _integer(
            self.sequence,
            "sequence",
            minimum=1,
            maximum=MAX_SAFE_INTEGER,
        )
        if self.kind not in (
            "request",
            "response",
            "event",
            "credit",
            "cancel",
            "ping",
            "pong",
            "close",
        ):
            raise ValueError("envelope kind is invalid")
        if self.kind == "request":
            if self.route_id is None or self.route_epoch is None:
                raise ValueError("request requires a route and route epoch")
            if self.operation is None or self.deadline_ms is None:
                raise ValueError("request requires operation and deadline_ms")
        if self.kind in ("response", "cancel") and self.reply_to is None:
            raise ValueError(f"{self.kind} requires reply_to")
        if self.reply_to is not None:
            object.__setattr__(self, "reply_to", _identifier(self.reply_to, "reply_to"))
        if self.route_id is not None:
            object.__setattr__(self, "route_id", _identifier(self.route_id, "route_id"))
        if self.route_epoch is not None:
            _integer(
                self.route_epoch,
                "route_epoch",
                minimum=1,
                maximum=MAX_SAFE_INTEGER,
            )
        if self.operation is not None:
            if len(self.operation) > 160 or _OPERATION_RE.fullmatch(self.operation) is None:
                raise ValueError("operation is invalid")
        if self.deadline_ms is not None:
            _integer(
                self.deadline_ms,
                "deadline_ms",
                minimum=0,
                maximum=MAX_SAFE_INTEGER,
            )

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "protocol": PROTOCOL,
            "kind": self.kind,
            "message_id": self.message_id,
            "sequence": self.sequence,
        }
        for key in ("reply_to", "route_id", "route_epoch", "operation", "deadline_ms"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        if self.scope is not None:
            result["scope"] = self.scope.to_wire()
        if self.trace is not None:
            result["trace"] = self.trace.to_wire()
        if self.payload is not None:
            result["payload"] = self.payload
        if self.error is not None:
            result["error"] = self.error.to_wire()
        return result

    @classmethod
    def from_wire(cls, value: object) -> Envelope:
        raw = _mapping(value, "envelope")
        if raw.get("protocol") != PROTOCOL:
            raise ValueError("unsupported envelope protocol")
        kind = raw.get("kind")
        if kind not in (
            "request",
            "response",
            "event",
            "credit",
            "cancel",
            "ping",
            "pong",
            "close",
        ):
            raise ValueError("envelope kind is invalid")
        route_epoch = raw.get("route_epoch")
        deadline_ms = raw.get("deadline_ms")
        return cls(
            kind=kind,
            message_id=_identifier(raw.get("message_id"), "message_id"),
            sequence=_integer(
                raw.get("sequence"),
                "sequence",
                minimum=1,
                maximum=MAX_SAFE_INTEGER,
            ),
            reply_to=(
                _identifier(raw.get("reply_to"), "reply_to")
                if raw.get("reply_to") is not None
                else None
            ),
            route_id=(
                _identifier(raw.get("route_id"), "route_id")
                if raw.get("route_id") is not None
                else None
            ),
            route_epoch=(
                _integer(
                    route_epoch,
                    "route_epoch",
                    minimum=1,
                    maximum=MAX_SAFE_INTEGER,
                )
                if route_epoch is not None
                else None
            ),
            operation=(
                _string(raw.get("operation"), "operation", maximum=160)
                if raw.get("operation") is not None
                else None
            ),
            scope=(
                Scope.from_wire(raw.get("scope"))
                if raw.get("scope") is not None
                else None
            ),
            trace=(
                Trace.from_wire(raw.get("trace"))
                if raw.get("trace") is not None
                else None
            ),
            deadline_ms=(
                _integer(
                    deadline_ms,
                    "deadline_ms",
                    minimum=0,
                    maximum=MAX_SAFE_INTEGER,
                )
                if deadline_ms is not None
                else None
            ),
            payload=raw.get("payload"),
            error=(
                Error.from_wire(raw.get("error"))
                if raw.get("error") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class EffectStatus:
    effect_id: Id
    state: EffectState
    result_digest: Digest | None = None

    @classmethod
    def from_wire(cls, value: object) -> EffectStatus:
        raw = _mapping(value, "effect status")
        state = raw.get("state")
        if state not in ("not_started", "committed", "unknown"):
            raise ValueError("effect status state is invalid")
        digest = raw.get("result_digest")
        if digest is not None and (
            not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise ValueError("result_digest is invalid")
        return cls(
            effect_id=_identifier(raw.get("effect_id"), "effect_id"),
            state=EffectState(state),
            result_digest=Digest(digest) if digest is not None else None,
        )


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshot:
    subscription_id: Id
    cursor: Cursor
    recovery_cursor: Cursor | None = None

    @classmethod
    def from_wire(cls, value: object) -> SubscriptionSnapshot:
        raw = _mapping(value, "subscription snapshot")
        cursor_value = raw.get("cursor", raw.get("snapshot_cursor"))
        recovery = raw.get("recovery_cursor")
        return cls(
            subscription_id=_identifier(
                raw.get("subscription_id", "events"), "subscription_id"
            ),
            cursor=Cursor.from_wire(cursor_value),
            recovery_cursor=(Cursor.from_wire(recovery) if recovery is not None else None),
        )


@dataclass(frozen=True, slots=True)
class EventChunk:
    event_id: Id
    chunk_index: int
    chunk_count: int
    digest: Digest
    data: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        _integer(
            self.chunk_index,
            "chunk_index",
            minimum=0,
            maximum=MAX_SAFE_INTEGER,
        )
        _integer(self.chunk_count, "chunk_count", minimum=1, maximum=131_072)
        if self.chunk_index >= self.chunk_count:
            raise ValueError("chunk_index must be lower than chunk_count")
        if _DIGEST_RE.fullmatch(self.digest) is None:
            raise ValueError("event chunk digest is invalid")
        object.__setattr__(self, "digest", Digest(self.digest))
        if not isinstance(self.data, bytes) or len(self.data) > EVENT_CHUNK_BYTES:
            raise ValueError("event chunk exceeds 64 KiB")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "event_id": self.event_id,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "digest": self.digest,
            "data": base64.b64encode(self.data).decode("ascii"),
        }

    @classmethod
    def from_wire(cls, value: object) -> EventChunk:
        raw = _mapping(value, "event chunk")
        encoded = raw.get("data")
        if not isinstance(encoded, str):
            raise ValueError("event chunk data must be base64")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("event chunk data must be canonical base64") from exc
        return cls(
            event_id=_identifier(raw.get("event_id"), "event_id"),
            chunk_index=_integer(
                raw.get("chunk_index"),
                "chunk_index",
                minimum=0,
                maximum=MAX_SAFE_INTEGER,
            ),
            chunk_count=_integer(
                raw.get("chunk_count"),
                "chunk_count",
                minimum=1,
                maximum=131_072,
            ),
            digest=_string(raw.get("digest"), "digest", maximum=64),
            data=data,
        )


class EventReassembler:
    __slots__ = ("_chunks", "_count", "_digest", "_event_id")

    def __init__(self) -> None:
        self._event_id: str | None = None
        self._digest: str | None = None
        self._count: int | None = None
        self._chunks: list[bytes] = []

    def push(self, chunk: EventChunk) -> bytes | None:
        expected_index = len(self._chunks)
        if chunk.chunk_index != expected_index:
            raise ValueError("event chunk is reordered or replayed")
        if expected_index == 0:
            self._event_id = chunk.event_id
            self._digest = chunk.digest
            self._count = chunk.chunk_count
        elif (
            chunk.event_id != self._event_id
            or chunk.digest != self._digest
            or chunk.chunk_count != self._count
        ):
            raise ValueError("event chunk transfer metadata changed")
        self._chunks.append(chunk.data)
        if len(self._chunks) != self._count:
            return None
        body = b"".join(self._chunks)
        if hashlib.sha256(body).hexdigest() != self._digest:
            raise ValueError("event chunk digest mismatch")
        self.__init__()
        return body


@dataclass(frozen=True, slots=True)
class ServerDescription:
    daemon_instance_id: Id
    protocol: str
    operations: tuple[str, ...]
    registry_generation: int
    started_ms: int
    server_time_ms: int
    health: Mapping[str, Any]
    build_version: str | None = None
    storage_version: str | None = None

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.operations

    @classmethod
    def from_wire(cls, value: object) -> ServerDescription:
        raw = _mapping(value, "server description")
        operations = raw.get("operations", raw.get("capabilities"))
        if not isinstance(operations, list) or not all(
            isinstance(item, str) and 0 < len(item) <= 160 for item in operations
        ):
            raise ValueError("server operations must be a bounded string array")
        if len(set(operations)) != len(operations):
            raise ValueError("server operations must be unique")
        protocol = raw.get("protocol")
        if protocol != PROTOCOL:
            raise ValueError("server description protocol is incompatible")
        build = raw.get("build_version")
        storage = raw.get("storage_version")
        health = raw.get("health")
        if not isinstance(health, Mapping):
            raise ValueError("server health must be an object")
        return cls(
            daemon_instance_id=_identifier(
                raw.get("daemon_instance_id"), "daemon_instance_id"
            ),
            protocol=protocol,
            operations=tuple(operations),
            registry_generation=_integer(
                raw.get("registry_generation"),
                "registry_generation",
                minimum=0,
                maximum=MAX_SAFE_INTEGER,
            ),
            started_ms=_integer(
                raw.get("started_ms"),
                "started_ms",
                minimum=0,
                maximum=MAX_SAFE_INTEGER,
            ),
            server_time_ms=_integer(
                raw.get("server_time_ms"),
                "server_time_ms",
                minimum=0,
                maximum=MAX_SAFE_INTEGER,
            ),
            health=health,
            build_version=build if isinstance(build, str) else None,
            storage_version=storage if isinstance(storage, str) else None,
        )
