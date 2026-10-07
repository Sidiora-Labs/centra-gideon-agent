from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .foundation import Cursor, Digest, Id, Scope, Trace

PROTOCOL_VERSION = "1.0"
MAX_ENVELOPE_BYTES = 8 * 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024
MAX_CHUNKS = 131_072

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")


class ProtocolViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ProtocolViolation("INVALID_ID", f"{field} is not a valid identifier")
    return Id(value)


def _require_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ProtocolViolation("INVALID_DIGEST", f"{field} is not a sha256 digest")
    return Digest(value)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolViolation("INVALID_ENVELOPE", f"{field} must be an object")
    return value


@dataclass(frozen=True, slots=True)
class WriterLease:
    lease_id: str
    session_id: str
    scope: Scope
    fence_token: str
    acquired_at: str
    expires_at: str
    cursor: Cursor

    def __post_init__(self) -> None:
        _require_id(self.lease_id, "writer_lease.lease_id")
        _require_id(self.session_id, "writer_lease.session_id")
        _require_id(self.fence_token, "writer_lease.fence_token")
        if not self.acquired_at or not self.expires_at:
            raise ProtocolViolation("INVALID_LEASE", "lease timestamps are required")

    @classmethod
    def from_mapping(cls, value: object) -> WriterLease:
        data = _mapping(value, "writer_lease")
        required = {
            "lease_id",
            "session_id",
            "scope",
            "fence_token",
            "acquired_at",
            "expires_at",
            "cursor",
        }
        if set(data) != required:
            raise ProtocolViolation(
                "INVALID_LEASE", "writer lease fields are incomplete"
            )
        return cls(
            lease_id=data["lease_id"],
            session_id=data["session_id"],
            scope=Scope.from_wire(_mapping(data["scope"], "writer_lease.scope")),
            fence_token=data["fence_token"],
            acquired_at=data["acquired_at"],
            expires_at=data["expires_at"],
            cursor=Cursor.from_wire(_mapping(data["cursor"], "writer_lease.cursor")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "session_id": self.session_id,
            "scope": self.scope.to_wire(),
            "fence_token": self.fence_token,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "cursor": self.cursor.to_wire(),
        }


class Operation(str, Enum):
    BIND = "bind"
    REBIND = "rebind"
    INGEST = "ingest"
    PROJECT = "project"
    REDUCE = "reduce"
    EXPAND = "expand"
    SUMMARY_CLAIM = "summary_claim"
    SUMMARY_PUBLISH = "summary_publish"
    RECOVER = "recover"
    CONFIGURE = "configure"
    SUBAGENT_SPAWN = "subagent_spawn"
    SUBAGENT_CONTRIBUTE = "subagent_contribute"
    EXPORT = "export"
    IMPORT = "import"
    DIAGNOSE = "diagnose"


class RenderMode(str, Enum):
    HOST_SERIALIZED = "host_serialized"


MUTATING_OPERATIONS = frozenset(
    {
        Operation.REBIND,
        Operation.INGEST,
        Operation.REDUCE,
        Operation.SUMMARY_PUBLISH,
        Operation.CONFIGURE,
        Operation.SUBAGENT_SPAWN,
        Operation.SUBAGENT_CONTRIBUTE,
        Operation.IMPORT,
    }
)


@dataclass(frozen=True, slots=True)
class ProtocolRequest:
    operation: Operation
    scope: Scope
    trace: Trace
    session_id: str
    payload: Mapping[str, Any]
    protocol_version: str = PROTOCOL_VERSION
    render_mode: RenderMode = RenderMode.HOST_SERIALIZED
    expected_cursor: Cursor | None = None
    writer_lease: WriterLease | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolViolation(
                "UNSUPPORTED_VERSION", "unsupported protocol version"
            )
        _require_id(self.session_id, "session_id")
        if not isinstance(self.payload, Mapping):
            raise ProtocolViolation("INVALID_PAYLOAD", "payload must be an object")
        needs_mutation_fields = (
            self.operation in MUTATING_OPERATIONS or self.operation is Operation.PROJECT
        )
        needs_writer = self.operation in MUTATING_OPERATIONS or (
            self.operation is Operation.PROJECT
            and self.payload.get("mode") == "primary"
        )
        if needs_mutation_fields and (
            self.expected_cursor is None or self.idempotency_key is None
        ):
            raise ProtocolViolation(
                "MUTATION_FIELDS_REQUIRED",
                "mutation requires expected_cursor and idempotency_key",
            )
        if needs_writer and self.writer_lease is None:
            raise ProtocolViolation("WRITER_LEASE_REQUIRED", "writer lease is required")
        if self.idempotency_key is not None:
            _require_id(self.idempotency_key, "idempotency_key")

    @classmethod
    def from_mapping(cls, value: object) -> ProtocolRequest:
        data = _mapping(value, "request")
        allowed = {
            "protocol_version",
            "operation",
            "scope",
            "trace",
            "session_id",
            "render_mode",
            "expected_cursor",
            "writer_lease",
            "idempotency_key",
            "payload",
        }
        if set(data) - allowed:
            raise ProtocolViolation(
                "INVALID_ENVELOPE", "request contains unknown fields"
            )
        try:
            return cls(
                protocol_version=data["protocol_version"],
                operation=Operation(data["operation"]),
                scope=Scope.from_wire(_mapping(data["scope"], "scope")),
                trace=Trace.from_wire(_mapping(data["trace"], "trace")),
                session_id=data["session_id"],
                render_mode=RenderMode(data["render_mode"]),
                expected_cursor=(
                    Cursor.from_wire(
                        _mapping(data["expected_cursor"], "expected_cursor")
                    )
                    if "expected_cursor" in data
                    else None
                ),
                writer_lease=(
                    WriterLease.from_mapping(data["writer_lease"])
                    if "writer_lease" in data
                    else None
                ),
                idempotency_key=data.get("idempotency_key"),
                payload=_mapping(data["payload"], "payload"),
            )
        except KeyError as exc:
            raise ProtocolViolation(
                "INVALID_ENVELOPE", f"missing {exc.args[0]}"
            ) from exc
        except ValueError as exc:
            if isinstance(exc, ProtocolViolation):
                raise
            raise ProtocolViolation("INVALID_ENUM", str(exc)) from exc

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "operation": self.operation.value,
            "scope": self.scope.to_wire(),
            "trace": self.trace.to_wire(),
            "session_id": self.session_id,
            "render_mode": self.render_mode.value,
            "payload": dict(self.payload),
        }
        if self.expected_cursor is not None:
            result["expected_cursor"] = self.expected_cursor.to_wire()
        if self.writer_lease is not None:
            result["writer_lease"] = self.writer_lease.to_mapping()
        if self.idempotency_key is not None:
            result["idempotency_key"] = self.idempotency_key
        return result


def canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation("INVALID_JSON", str(exc)) from exc


def encode_frame(envelope: ProtocolRequest | Mapping[str, Any]) -> bytes:
    value = envelope.to_mapping() if isinstance(envelope, ProtocolRequest) else envelope
    body = canonical_json(_mapping(value, "envelope"))
    if len(body) > MAX_ENVELOPE_BYTES:
        raise ProtocolViolation("ENVELOPE_TOO_LARGE", "envelope exceeds eight MiB")
    return struct.pack(">I", len(body)) + body


class FrameDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        frames: list[dict[str, Any]] = []
        while len(self._buffer) >= 4:
            length = struct.unpack(">I", self._buffer[:4])[0]
            if length > MAX_ENVELOPE_BYTES:
                self._buffer.clear()
                raise ProtocolViolation(
                    "ENVELOPE_TOO_LARGE", "declared envelope exceeds eight MiB"
                )
            if len(self._buffer) < length + 4:
                break
            body = bytes(self._buffer[4 : length + 4])
            del self._buffer[: length + 4]
            try:
                decoded = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolViolation("INVALID_JSON", str(exc)) from exc
            if not isinstance(decoded, dict):
                raise ProtocolViolation(
                    "INVALID_ENVELOPE", "envelope must be an object"
                )
            frames.append(decoded)
        return frames

    def finish(self) -> None:
        if self._buffer:
            raise ProtocolViolation("INCOMPLETE_FRAME", "stream ended within a frame")


@dataclass(frozen=True, slots=True)
class TransferChunk:
    transfer_id: str
    ordinal: int
    total: int
    chunk_digest: str
    full_digest: str
    payload_base64: str
    trace: Trace
    protocol_version: str = PROTOCOL_VERSION

    def payload(self) -> bytes:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolViolation(
                "UNSUPPORTED_VERSION", "unsupported protocol version"
            )
        _require_id(self.transfer_id, "transfer_id")
        _require_digest(self.chunk_digest, "chunk_digest")
        _require_digest(self.full_digest, "full_digest")
        if not 1 <= self.total <= MAX_CHUNKS or not 0 <= self.ordinal < self.total:
            raise ProtocolViolation(
                "INVALID_CHUNK_ORDER", "chunk ordinal is out of range"
            )
        if len(self.payload_base64) > 87_384:
            raise ProtocolViolation("CHUNK_TOO_LARGE", "chunk exceeds 64 KiB")
        try:
            decoded = base64.b64decode(self.payload_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProtocolViolation(
                "INVALID_CHUNK", "payload is not valid base64"
            ) from exc
        if len(decoded) > MAX_CHUNK_BYTES:
            raise ProtocolViolation("CHUNK_TOO_LARGE", "chunk exceeds 64 KiB")
        if hashlib.sha256(decoded).hexdigest() != self.chunk_digest:
            raise ProtocolViolation(
                "CHUNK_DIGEST_MISMATCH", "chunk digest does not match"
            )
        return decoded


class ChunkAssembler:
    def __init__(self) -> None:
        self._transfers: dict[str, tuple[int, str, list[bytes]]] = {}

    def add(self, chunk: TransferChunk) -> bytes | None:
        payload = chunk.payload()
        total, full_digest, parts = self._transfers.setdefault(
            chunk.transfer_id, (chunk.total, chunk.full_digest, [])
        )
        if total != chunk.total or full_digest != chunk.full_digest:
            self._transfers.pop(chunk.transfer_id, None)
            raise ProtocolViolation("CHUNK_CONFLICT", "transfer metadata changed")
        if chunk.ordinal != len(parts):
            raise ProtocolViolation(
                "INVALID_CHUNK_ORDER", "chunks must arrive exactly once in order"
            )
        parts.append(payload)
        if len(parts) != total:
            return None
        assembled = b"".join(parts)
        self._transfers.pop(chunk.transfer_id, None)
        if hashlib.sha256(assembled).hexdigest() != full_digest:
            raise ProtocolViolation(
                "FULL_DIGEST_MISMATCH", "transfer digest does not match"
            )
        return assembled

    def finish(self, transfer_id: str) -> None:
        if transfer_id in self._transfers:
            self._transfers.pop(transfer_id, None)
            raise ProtocolViolation(
                "INCOMPLETE_TRANSFER", "transfer ended before all chunks"
            )
