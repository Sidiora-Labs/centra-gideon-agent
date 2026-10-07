"""Authenticated asynchronous client for the Hypermid v1 local daemon."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import secrets
import ssl
import stat
import struct
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from .models import (
    MAX_FRAME_BYTES,
    MAX_SAFE_INTEGER,
    PROTOCOL,
    Cursor,
    EffectState,
    EffectStatus,
    Envelope,
    Error,
    Id,
    JsonValue,
    Scope,
    ServerDescription,
    SessionAccepted,
    SubscriptionSnapshot,
    Trace,
)

if TYPE_CHECKING:
    from .subscriptions import DurableSubscription, ResumeStore

_LENGTH = struct.Struct(">I")
_CONTROL_ROUTE = "control"
_CONTROL_EPOCH = 1
_DEFAULT_TIMEOUT_SECONDS = 30.0


class HypermidClientError(Exception):
    """Base error raised by the Hypermid Python client."""


class HypermidProtocolError(HypermidClientError):
    """The peer sent a malformed or incompatible protocol value."""


class HypermidConnectionError(HypermidClientError):
    """A connection ended before a query produced a response."""


class HypermidRemoteError(HypermidClientError):
    """A typed Error returned by the daemon."""

    def __init__(self, error: Error) -> None:
        super().__init__(f"{error.code}: {error.message}")
        self.error = error


class HypermidOutcomeUnknown(HypermidRemoteError):
    """A mutation may have been accepted before transport loss."""

    def __init__(
        self, message: str = "connection ended after mutation dispatch"
    ) -> None:
        super().__init__(
            Error(
                code="OUTCOME_UNKNOWN",
                message=message,
                retryable=False,
                effect_state=EffectState.UNKNOWN,
            )
        )


class ConnectionRecord:
    """Owner-protected endpoint metadata plus an intentionally opaque credential."""

    __slots__ = (
        "credential_id",
        "endpoint_kind",
        "host",
        "path",
        "port",
        "protocol",
        "server_name",
        "tls_ca",
        "tls_cert",
        "tls_key",
        "_secret",
    )

    def __init__(
        self,
        *,
        endpoint_kind: Literal["unix", "tcp"],
        secret: bytes,
        path: str | None = None,
        host: str | None = None,
        port: int | None = None,
        protocol: str = PROTOCOL,
        credential_id: str = "local",
        server_name: str | None = None,
        tls_ca: str | None = None,
        tls_cert: str | None = None,
        tls_key: str | None = None,
    ) -> None:
        if len(secret) != 32:
            raise HypermidProtocolError("connection secret must contain 32 bytes")
        if protocol != PROTOCOL:
            raise HypermidProtocolError(f"unsupported connection protocol {protocol!r}")
        if endpoint_kind == "unix" and not path:
            raise HypermidProtocolError("Unix endpoint is missing its socket path")
        if endpoint_kind == "tcp" and (not host or not isinstance(port, int)):
            raise HypermidProtocolError("TCP endpoint is incomplete")
        self.endpoint_kind = endpoint_kind
        self.path = path
        self.host = host
        self.port = port
        self.protocol = protocol
        self.credential_id = credential_id
        self.server_name = server_name
        self.tls_ca = tls_ca
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self._secret = secret

    def __repr__(self) -> str:
        endpoint = (
            self.path if self.endpoint_kind == "unix" else f"{self.host}:{self.port}"
        )
        return (
            f"ConnectionRecord(endpoint_kind={self.endpoint_kind!r}, "
            f"endpoint={endpoint!r}, protocol={self.protocol!r}, "
            f"credential_id={self.credential_id!r}, credential=<redacted>)"
        )

    def __getstate__(self) -> object:
        raise TypeError("connection credentials cannot be serialized")

    def credential_bytes(self) -> bytes:
        return self._secret

    @classmethod
    def load(cls, path: str | Path) -> ConnectionRecord:
        record_path = Path(path)
        file_stat = record_path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise PermissionError("Hypermid connection record must be a regular file")
        if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
            raise PermissionError("Hypermid connection record has the wrong owner")
        mode = stat.S_IMODE(file_stat.st_mode)
        if mode != 0o600:
            raise PermissionError("Hypermid connection record must have mode 0600")
        try:
            raw = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HypermidProtocolError("invalid Hypermid connection record") from exc
        if not isinstance(raw, dict):
            raise HypermidProtocolError("Hypermid connection record must be an object")

        allowed = {
            "endpoint",
            "protocol",
            "credential_id",
            "secret_b64",
            "server_name",
            "ca_certificate",
            "client_certificate",
            "client_private_key",
            "expires_ms",
        }
        if set(raw) != allowed:
            raise HypermidProtocolError(
                "connection record fields do not match hypermid.v1"
            )
        endpoint = raw.get("endpoint")
        endpoint_kind: str | None = None
        socket_path: str | None = None
        host: str | None = None
        port: int | None = None
        if isinstance(endpoint, str):
            if endpoint.startswith("unix://"):
                endpoint_kind, socket_path = "unix", endpoint[7:]
            elif endpoint.startswith("tcp://"):
                endpoint_kind = "tcp"
                address = endpoint[6:]
                host, separator, port_text = address.rpartition(":")
                if separator:
                    try:
                        port = int(port_text)
                    except ValueError as exc:
                        raise HypermidProtocolError(
                            "invalid TCP endpoint port"
                        ) from exc
            elif endpoint.startswith("/"):
                endpoint_kind, socket_path = "unix", endpoint
        if endpoint_kind not in ("unix", "tcp"):
            raise HypermidProtocolError("unsupported connection endpoint")

        encoded_secret = raw.get("secret_b64")
        if not isinstance(encoded_secret, str):
            raise HypermidProtocolError("connection record is missing its credential")
        try:
            secret = base64.b64decode(encoded_secret, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HypermidProtocolError("connection credential is malformed") from exc

        expires_ms = raw.get("expires_ms")
        if (
            not isinstance(expires_ms, int)
            or isinstance(expires_ms, bool)
            or expires_ms <= int(time.time() * 1000)
        ):
            raise HypermidProtocolError("connection record has expired")
        return cls(
            endpoint_kind=cast(Literal["unix", "tcp"], endpoint_kind),
            secret=secret,
            path=socket_path,
            host=host,
            port=port,
            protocol=raw.get("protocol", PROTOCOL),
            credential_id=str(raw.get("credential_id", "local")),
            server_name=raw.get("server_name"),
            tls_ca=raw.get("ca_certificate"),
            tls_cert=raw.get("client_certificate"),
            tls_key=raw.get("client_private_key"),
        )


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    _reject_non_integer_numbers(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HypermidProtocolError("wire value is not canonical JSON") from exc


def encode_frame(value: Mapping[str, Any]) -> bytes:
    body = canonical_json_bytes(value)
    if not body or len(body) > MAX_FRAME_BYTES:
        raise HypermidProtocolError("frame length is outside the Hypermid v1 bounds")
    return _LENGTH.pack(len(body)) + body


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HypermidProtocolError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def decode_json_body(body: bytes) -> dict[str, Any]:
    if not body or len(body) > MAX_FRAME_BYTES:
        raise HypermidProtocolError("frame length is outside the Hypermid v1 bounds")
    try:
        decoded = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                HypermidProtocolError(f"non-finite JSON number {value}")
            ),
        )
    except HypermidProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HypermidProtocolError("frame body is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise HypermidProtocolError("frame body must be a JSON object")
    _reject_non_integer_numbers(decoded)
    return decoded


def _reject_non_integer_numbers(value: object) -> None:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER
    ):
        raise HypermidProtocolError("wire JSON integer exceeds the safe range")
    if isinstance(value, float) and not math.isfinite(value):
        raise HypermidProtocolError("wire JSON numbers must be finite")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_integer_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_integer_numbers(item)


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    try:
        header = await reader.readexactly(_LENGTH.size)
    except asyncio.IncompleteReadError as exc:
        raise HypermidConnectionError("connection ended before frame header") from exc
    (length,) = _LENGTH.unpack(header)
    if length == 0 or length > MAX_FRAME_BYTES:
        raise HypermidProtocolError("peer declared an invalid frame length")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise HypermidConnectionError("connection ended before frame body") from exc
    return decode_json_body(body)


async def write_frame(writer: asyncio.StreamWriter, value: Mapping[str, Any]) -> None:
    writer.write(encode_frame(value))
    await writer.drain()


def authentication_proof(
    secret: bytes,
    *,
    client_nonce: bytes,
    server_nonce: bytes,
    protocol: str,
    daemon_instance_id: str,
    connection_class: str,
    scope: Scope,
) -> str:
    fields = (
        client_nonce,
        server_nonce,
        protocol.encode("utf-8"),
        daemon_instance_id.encode("utf-8"),
        connection_class.encode("utf-8"),
        scope.owner_id.encode("utf-8"),
        scope.project_id.encode("utf-8"),
        (scope.workspace_id or "").encode("utf-8"),
    )
    transcript = b"hypermid-auth-v1\0" + b"".join(
        _LENGTH.pack(len(field)) + field for field in fields
    )
    digest = hmac.new(secret, transcript, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class _Pending:
    future: asyncio.Future[Envelope]
    effect_kind: Literal["query", "idempotent", "durable"]
    written: bool = False


class EventSubscription(AsyncIterator[Envelope]):
    def __init__(self, client: HypermidClient, snapshot: SubscriptionSnapshot) -> None:
        self.client = client
        self.snapshot = snapshot
        self._queue: asyncio.Queue[Envelope | BaseException | None] = asyncio.Queue(
            maxsize=256
        )
        self._closed = False
        self._send_sequence = 1
        self._receive_sequence = 1

    @property
    def cursor(self) -> Cursor:
        return self.snapshot.cursor

    def __aiter__(self) -> EventSubscription:
        return self

    async def __anext__(self) -> Envelope:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        payload = item.payload
        if isinstance(payload, dict) and payload.get("cursor") is not None:
            cursor = Cursor.from_wire(payload["cursor"])
            self.snapshot = SubscriptionSnapshot(
                subscription_id=self.snapshot.subscription_id,
                cursor=cursor,
                recovery_cursor=self.snapshot.recovery_cursor,
            )
        return item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.client._subscriptions.pop(self.snapshot.subscription_id, None)
        await self._queue.put(None)

    def _put(self, item: Envelope | BaseException | None) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._closed = True
            self.client._subscriptions.pop(self.snapshot.subscription_id, None)
            while not self._queue.empty():
                self._queue.get_nowait()
            self._queue.put_nowait(
                HypermidProtocolError("event subscription exceeded its bounded queue")
            )


class HypermidClient:
    def __init__(
        self,
        connection_record: str | Path,
        *,
        scope: Scope,
        connection_class: Literal["client", "module", "device", "service"] = "client",
    ) -> None:
        self.connection_record = Path(connection_record)
        self.scope = scope
        self.connection_class = connection_class
        self.session: SessionAccepted | None = None
        self.daemon_instance_id: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._pending: dict[str, _Pending] = {}
        self._subscriptions: dict[str, EventSubscription] = {}
        self._closed = False

    @property
    def connected(self) -> bool:
        return (
            self.session is not None
            and self._writer is not None
            and not self._writer.is_closing()
        )

    async def __aenter__(self) -> HypermidClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def connect(self) -> SessionAccepted:
        async with self._connect_lock:
            if self.connected and self.session is not None:
                return self.session
            self._closed = False
            record = ConnectionRecord.load(self.connection_record)
            reader, writer = await self._open(record)
            try:
                session, daemon_id = await self._handshake(reader, writer, record)
            except BaseException:
                writer.close()
                await writer.wait_closed()
                raise
            previous_daemon = self.daemon_instance_id
            self._reader = reader
            self._writer = writer
            self.session = session
            self.daemon_instance_id = daemon_id
            self._send_sequence = 1
            self._receive_sequence = 1
            if previous_daemon is not None and previous_daemon != daemon_id:
                self._invalidate_subscriptions(
                    HypermidProtocolError(
                        "daemon epoch changed; obtain a fresh snapshot before resuming"
                    )
                )
            self._reader_task = asyncio.create_task(
                self._reader_loop(), name="hypermid-client-reader"
            )
            return session

    async def reconnect(self) -> SessionAccepted:
        await self.close()
        self._closed = False
        return await self.connect()

    async def close(self) -> None:
        self._closed = True
        task, self._reader_task = self._reader_task, None
        writer, self._writer = self._writer, None
        self._reader = None
        self.session = None
        if writer is not None:
            writer.close()
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if writer is not None:
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self._fail_pending(HypermidConnectionError("Hypermid client closed"))
        self._invalidate_subscriptions(
            HypermidConnectionError("Hypermid client closed")
        )

    async def request(
        self,
        operation: str,
        payload: JsonValue,
        *,
        trace: Trace | None = None,
        deadline_ms: int | None = None,
        effect_kind: Literal["query", "idempotent", "durable"] = "query",
        scope: Scope | None = None,
    ) -> JsonValue:
        if scope is not None and scope != self.scope:
            raise HypermidClientError(
                "request scope does not match the authenticated host scope"
            )
        if not self.connected:
            await self.connect()
        if deadline_ms is None:
            deadline_ms = int((time.time() + _DEFAULT_TIMEOUT_SECONDS) * 1000)
        remaining = (deadline_ms - int(time.time() * 1000)) / 1000
        if remaining <= 0:
            raise TimeoutError("Hypermid request deadline has elapsed")
        message_id = Id(secrets.token_hex(16))
        trace = trace or Trace(
            trace_id=Id(secrets.token_hex(16)), request_id=message_id
        )
        envelope = Envelope(
            kind="request",
            message_id=message_id,
            sequence=1,
            route_id=Id(_CONTROL_ROUTE),
            route_epoch=_CONTROL_EPOCH,
            operation=operation,
            scope=self.scope,
            trace=trace,
            deadline_ms=deadline_ms,
            payload=payload,
        )
        loop = asyncio.get_running_loop()
        pending = _Pending(loop.create_future(), effect_kind)
        self._pending[message_id] = pending
        try:
            await self._send(envelope)
            pending.written = True
            response = await asyncio.wait_for(pending.future, timeout=remaining)
        except asyncio.CancelledError:
            if pending.written:
                try:
                    await self.cancel(message_id)
                except HypermidClientError:
                    pass
            raise
        except TimeoutError as exc:
            if pending.written:
                try:
                    await self.cancel(message_id)
                except HypermidClientError:
                    pass
                if effect_kind != "query":
                    raise HypermidOutcomeUnknown(
                        "mutation deadline elapsed after dispatch"
                    ) from exc
            raise
        except (
            ConnectionError,
            HypermidConnectionError,
            asyncio.IncompleteReadError,
        ) as exc:
            if pending.written and effect_kind != "query":
                raise HypermidOutcomeUnknown() from exc
            raise HypermidConnectionError("Hypermid request connection ended") from exc
        finally:
            self._pending.pop(message_id, None)
        if response.error is not None:
            if response.error.effect_state == "unknown":
                raise HypermidOutcomeUnknown(response.error.message)
            raise HypermidRemoteError(response.error)
        return response.payload

    async def passthrough(
        self, payload: JsonValue, *, trace: Trace | None = None
    ) -> JsonValue:
        response = await self.request("passthrough", payload, trace=trace)
        if not isinstance(response, dict) or response.get("mode") != "passthrough":
            raise HypermidProtocolError(
                "daemon returned an invalid pass-through response"
            )
        if "payload" not in response:
            raise HypermidProtocolError(
                "daemon pass-through response is missing its payload"
            )
        return response["payload"]

    async def describe(self) -> JsonValue:
        return await self.request("server.describe", {})

    async def describe_typed(self) -> ServerDescription:
        return ServerDescription.from_wire(await self.describe())

    async def negotiate(
        self, required_capabilities: tuple[str, ...] | list[str]
    ) -> ServerDescription:
        description = await self.describe_typed()
        missing = sorted(set(required_capabilities) - set(description.capabilities))
        if missing:
            raise HypermidProtocolError(
                "daemon does not implement required capabilities: " + ", ".join(missing)
            )
        return description

    async def cancel(self, message_id: str) -> None:
        envelope = Envelope(
            kind="cancel",
            message_id=Id(secrets.token_hex(16)),
            sequence=1,
            reply_to=Id(message_id),
            scope=self.scope,
        )
        await self._send(envelope)

    async def effect_status(self, effect_id: str) -> EffectStatus:
        payload = await self.request("effects.status", {"effect_id": effect_id})
        return EffectStatus.from_wire(payload)

    async def reconcile_effect(self, effect_id: str) -> EffectStatus:
        return await self.effect_status(effect_id)

    async def subscribe_events(
        self,
        *,
        consumer_id: Id,
        scope: Scope,
        topic_filter: str,
        after: Cursor | None = None,
        resume_store: ResumeStore | None = None,
    ) -> DurableSubscription:
        from .subscriptions import SubscriptionClient

        return await SubscriptionClient(self).subscribe(
            consumer_id=consumer_id,
            scope=scope,
            topic_filter=topic_filter,
            resume_store=resume_store,
            after_cursor=after,
        )

    async def resume_events(
        self,
        *,
        consumer_id: Id,
        scope: Scope,
        topic_filter: str,
        after: Cursor,
        resume_store: ResumeStore | None = None,
    ) -> DurableSubscription:
        return await self.subscribe_events(
            consumer_id=consumer_id,
            scope=scope,
            topic_filter=topic_filter,
            after=after,
            resume_store=resume_store,
        )

    async def _open(
        self, record: ConnectionRecord
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if record.endpoint_kind == "unix":
            context = self._ssl_context(record)
            return await asyncio.open_unix_connection(
                record.path,
                ssl=context,
                server_hostname=record.server_name if context is not None else None,
            )
        context = self._ssl_context(record)
        return await asyncio.open_connection(
            record.host,
            record.port,
            ssl=context,
            server_hostname=record.server_name if context is not None else None,
        )

    def _ssl_context(self, record: ConnectionRecord) -> ssl.SSLContext:
        if (
            not record.server_name
            or not record.tls_ca
            or not record.tls_cert
            or not record.tls_key
        ):
            raise HypermidProtocolError("mutual TLS connection material is incomplete")
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH, cafile=record.tls_ca
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(record.tls_cert, record.tls_key)
        return context

    async def _handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        record: ConnectionRecord,
    ) -> tuple[SessionAccepted, str]:
        client_nonce = secrets.token_bytes(32)
        await write_frame(
            writer,
            {
                "kind": "hello",
                "protocols": [PROTOCOL],
                "client_nonce": client_nonce.hex(),
                "connection_class": self.connection_class,
                "scope": self.scope.to_wire(),
            },
        )
        challenge = await read_frame(reader)
        if challenge.get("kind") != "challenge":
            raise self._handshake_error(challenge, "expected authentication challenge")
        if challenge.get("protocol") != PROTOCOL:
            raise HypermidProtocolError("daemon selected an unsupported protocol")
        if challenge.get("auth_method") != "hmac_sha256":
            raise HypermidProtocolError(
                "daemon selected an unsupported authentication method"
            )
        try:
            server_nonce = bytes.fromhex(str(challenge["server_nonce"]))
        except (KeyError, ValueError) as exc:
            raise HypermidProtocolError("daemon challenge nonce is invalid") from exc
        if len(server_nonce) != 32:
            raise HypermidProtocolError("daemon challenge nonce is invalid")
        daemon_id = str(challenge.get("daemon_instance_id", ""))
        proof = authentication_proof(
            record.credential_bytes(),
            client_nonce=client_nonce,
            server_nonce=server_nonce,
            protocol=PROTOCOL,
            daemon_instance_id=daemon_id,
            connection_class=self.connection_class,
            scope=self.scope,
        )
        await write_frame(writer, {"kind": "authenticate", "proof": proof})
        accepted = await read_frame(reader)
        if accepted.get("kind") != "accepted":
            raise self._handshake_error(accepted, "authentication refused")
        return SessionAccepted.from_wire(accepted), daemon_id

    @staticmethod
    def _handshake_error(
        value: Mapping[str, Any], fallback: str
    ) -> HypermidClientError:
        if value.get("error") is not None:
            try:
                return HypermidRemoteError(Error.from_wire(value["error"]))
            except ValueError:
                pass
        return HypermidProtocolError(fallback)

    async def _send(self, envelope: Envelope) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise HypermidConnectionError("Hypermid client is not connected")
        async with self._write_lock:
            sequenced = replace(envelope, sequence=self._take_send_sequence())
            await write_frame(writer, sequenced.to_wire())

    async def _reader_loop(self) -> None:
        reader = self._reader
        if reader is None:
            return
        failure: BaseException = HypermidConnectionError("Hypermid daemon disconnected")
        try:
            while True:
                envelope = Envelope.from_wire(await read_frame(reader))
                if envelope.sequence != self._receive_sequence:
                    raise HypermidProtocolError(
                        "daemon envelope sequence was replayed or reordered"
                    )
                self._receive_sequence += 1
                if envelope.kind == "response" and envelope.reply_to is not None:
                    pending = self._pending.get(envelope.reply_to)
                    if pending is not None and not pending.future.done():
                        pending.future.set_result(envelope)
                elif envelope.kind == "event":
                    self._dispatch_event(envelope)
                elif envelope.kind == "close":
                    if envelope.error is not None:
                        failure = HypermidRemoteError(envelope.error)
                    break
        except asyncio.CancelledError:
            return
        except BaseException as exc:
            failure = exc
        finally:
            self.session = None
            writer = self._writer
            self._writer = None
            self._reader = None
            if writer is not None:
                writer.close()
            self._fail_pending(failure)
            self._invalidate_subscriptions(failure)

    def _dispatch_event(self, envelope: Envelope) -> None:
        payload = envelope.payload
        subscription_id = (
            payload.get("subscription_id") if isinstance(payload, dict) else None
        )
        if isinstance(subscription_id, str):
            subscription = self._subscriptions.get(subscription_id)
            if subscription is not None:
                subscription._put(envelope)
            return
        for subscription in tuple(self._subscriptions.values()):
            subscription._put(envelope)

    def _fail_pending(self, failure: BaseException) -> None:
        for pending in tuple(self._pending.values()):
            if pending.future.done():
                continue
            if pending.written and pending.effect_kind != "query":
                pending.future.set_exception(HypermidOutcomeUnknown())
            else:
                pending.future.set_exception(failure)

    def _take_send_sequence(self) -> int:
        sequence = self._send_sequence
        if sequence > MAX_SAFE_INTEGER:
            raise HypermidProtocolError("session envelope sequence is exhausted")
        self._send_sequence += 1
        return sequence

    def _invalidate_subscriptions(self, failure: BaseException) -> None:
        subscriptions = tuple(self._subscriptions.values())
        self._subscriptions.clear()
        for subscription in subscriptions:
            subscription._put(failure)


__all__ = [
    "ConnectionRecord",
    "EventSubscription",
    "HypermidClient",
    "HypermidClientError",
    "HypermidConnectionError",
    "HypermidOutcomeUnknown",
    "HypermidProtocolError",
    "HypermidRemoteError",
    "authentication_proof",
    "canonical_json_bytes",
    "decode_json_body",
    "encode_frame",
    "read_frame",
    "write_frame",
]
