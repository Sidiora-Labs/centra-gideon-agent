from __future__ import annotations

import asyncio
import base64
import json
import os
import pickle
import secrets
import sqlite3
import ssl
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from checks.hypermid.evidence import ObservationWriter
from gideon.hypermid.client import (
    ConnectionRecord,
    HypermidClient,
    HypermidConnectionError,
    HypermidProtocolError,
    canonical_json_bytes,
    encode_frame,
    read_frame,
    write_frame,
)
from gideon.hypermid.models import (
    MAX_FRAME_BYTES,
    PROTOCOL,
    Envelope,
    EventChunk,
    EventReassembler,
    Id,
    Scope,
)
from gideon.hypermid.subscriptions import SubscriptionClient

ROOT = Path(__file__).resolve().parents[2]


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise AssertionError(
            "HYPERMID_DAEMON_BINARY must name an executable daemon file"
        )
    target = Path(
        os.environ.get(
            "CARGO_TARGET_DIR",
            str(ROOT / "target"),
        )
    )
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    raise AssertionError("focused gate requires the built hypermid-daemon binary")


@dataclass
class LiveDaemon:
    process: subprocess.Popen[str]
    socket: Path
    record: Path
    remote_record: Path | None = None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    @property
    def tls_directory(self) -> Path:
        raw = json.loads(self.record.read_text(encoding="utf-8"))
        return Path(raw["ca_certificate"]).parent


@dataclass
class AuthenticationGateObservations:
    captures: list[dict[str, Any]] = field(default_factory=list)
    invalid_sessions: list[dict[str, Any]] = field(default_factory=list)
    compatible_resume_passed: int = 0

    def record_capture(
        self, transport: str, captured: bytes, plaintext_needles: tuple[bytes, ...]
    ) -> None:
        plaintext_bytes = sum(
            len(needle) * captured.count(needle)
            for needle in plaintext_needles
            if needle
        )
        self.captures.append(
            {
                "transport": transport,
                "captured_bytes": len(captured),
                "plaintext_application_bytes": plaintext_bytes,
            }
        )

    def record_invalid_rejection(self, failure_class: str) -> None:
        self.invalid_sessions.append(
            {"failure_class": failure_class, "accepted": False}
        )

    def record_compatible_resume(self, passed: bool) -> None:
        assert passed
        self.compatible_resume_passed += int(passed)

    def finish(self, writer: ObservationWriter, artifact_path: Path) -> None:
        assert len(self.captures) >= 2
        assert self.invalid_sessions
        assert self.compatible_resume_passed > 0
        plaintext_bytes = sum(
            capture["plaintext_application_bytes"] for capture in self.captures
        )
        invalid_sessions_accepted = sum(
            int(item["accepted"]) for item in self.invalid_sessions
        )
        writer.measure(
            "plaintext-application-bytes",
            plaintext_bytes,
            "eq",
            0,
            "bytes",
        )
        writer.measure(
            "invalid-sessions-accepted",
            invalid_sessions_accepted,
            "eq",
            0,
            "sessions",
        )
        writer.measure(
            "compatible-resume-passed",
            self.compatible_resume_passed,
            "gte",
            1,
            "journeys",
        )
        artifact_path.write_text(
            json.dumps(
                {
                    "gate": "authentication",
                    "captures": self.captures,
                    "invalid_sessions": self.invalid_sessions,
                    "compatible_resume_passed": self.compatible_resume_passed,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        writer.artifact("transport-auth-matrix", artifact_path, "application/json")
        writer.finish()


@pytest.fixture(scope="session", autouse=True)
def authentication_observations(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[AuthenticationGateObservations]:
    observations = AuthenticationGateObservations()
    yield observations
    writer = ObservationWriter.from_env("authentication")
    if writer is None:
        return
    artifact_path = (
        tmp_path_factory.mktemp("authentication-observation")
        / "transport-auth-matrix.json"
    )
    observations.finish(writer, artifact_path)


def _start_daemon(
    root: Path,
    name: str,
    *,
    tls_directory: Path | None = None,
    client_crl: Path | None = None,
    remote_listen: str | None = None,
    remote_record: Path | None = None,
    remote_authorization: Path | None = None,
) -> LiveDaemon:
    root.mkdir(parents=True, exist_ok=True)
    socket = root / f"{name}.sock"
    record = root / f"{name}.json"
    expires_ms = int((time.time() + 600) * 1000)
    arguments = [
        str(_daemon_binary()),
        "--socket",
        str(socket),
        "--connection-record",
        str(record),
        "--local-credential-id",
        "credential-auth",
        "--local-owner-id",
        "owner-auth",
        "--local-project-id",
        "project-auth",
        "--local-capability-id",
        "capability-auth",
        "--local-capability-operation",
        "read",
        "--local-capability-resource",
        "resource-auth",
        "--local-capability-expires-ms",
        str(expires_ms),
    ]
    if tls_directory is not None:
        arguments.extend(("--tls-directory", str(tls_directory)))
    if client_crl is not None:
        arguments.extend(("--client-crl", str(client_crl)))
    remote_values = (remote_listen, remote_record, remote_authorization)
    if any(value is not None for value in remote_values):
        assert all(value is not None for value in remote_values)
        arguments.extend(
            (
                "--remote-listen",
                str(remote_listen),
                "--remote-connection-record",
                str(remote_record),
                "--remote-authorization",
                str(remote_authorization),
            )
        )
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not record.is_file() or (remote_record is not None and not remote_record.is_file()):
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(
                f"hypermid-daemon exited before readiness ({process.returncode}): {stderr}"
            )
        if time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise AssertionError("hypermid-daemon connection record timed out")
        time.sleep(0.01)
    return LiveDaemon(process, socket, record, remote_record)


@pytest.fixture
def live_daemon(tmp_path: Path) -> Iterator[LiveDaemon]:
    daemon = _start_daemon(tmp_path, "primary")
    try:
        yield daemon
    finally:
        daemon.stop()


def _record_variant(source: Path, target: Path, **changes: Any) -> Path:
    value = json.loads(source.read_text(encoding="utf-8"))
    value.update(changes)
    target.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    target.chmod(0o600)
    return target


def _durable_snapshot(root: Path) -> dict[str, tuple[str, ...]]:
    snapshot: dict[str, tuple[str, ...]] = {}
    for database in sorted(root.rglob("*.sqlite3")):
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            snapshot[str(database.relative_to(root))] = tuple(connection.iterdump())
        finally:
            connection.close()
    return snapshot


def _write_remote_authorization(path: Path, *, expires_at_ms: int) -> Path:
    path.write_text(
        json.dumps(
            {
                "principal_id": "remote-client",
                "scope": {"owner_id": "owner-remote", "project_id": "project-remote"},
                "operations": ["passthrough"],
                "expires_at_ms": expires_at_ms,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _load_ca(directory: Path) -> tuple[x509.Certificate, Any]:
    certificate = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
    private_key = serialization.load_pem_private_key(
        (directory / "ca-key.pem").read_bytes(), password=None
    )
    return certificate, private_key


def _issue_client(
    directory: Path,
    ca_directory: Path,
    name: str,
    *,
    not_before: datetime,
    not_after: datetime,
    issuer: tuple[x509.Certificate, Any] | None = None,
) -> tuple[Path, Path, x509.Certificate]:
    ca_certificate, ca_key = issuer or _load_ca(ca_directory)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = directory / f"{name}.pem"
    key_path = directory / f"{name}-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.chmod(0o600)
    key_path.chmod(0o600)
    return cert_path, key_path, certificate


def _write_crl(path: Path, ca_directory: Path, certificate: x509.Certificate) -> Path:
    ca_certificate, ca_key = _load_ca(ca_directory)
    now = datetime.now(timezone.utc)
    revoked = (
        x509.RevokedCertificateBuilder()
        .serial_number(certificate.serial_number)
        .revocation_date(now - timedelta(minutes=1))
        .build()
    )
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_certificate.subject)
        .last_update(now - timedelta(minutes=1))
        .next_update(now + timedelta(days=1))
        .add_extension(x509.CRLNumber(1), critical=False)
        .add_revoked_certificate(revoked)
        .sign(ca_key, hashes.SHA256())
    )
    path.write_bytes(crl.public_bytes(serialization.Encoding.PEM))
    path.chmod(0o600)
    return path


async def _connection_fails(
    record_path: Path,
    scope: Scope,
    observations: AuthenticationGateObservations,
    failure_class: str,
) -> None:
    client = HypermidClient(record_path, scope=scope)
    with pytest.raises(
        (
            ssl.SSLError,
            ConnectionError,
            OSError,
            HypermidConnectionError,
            HypermidProtocolError,
        )
    ):
        await asyncio.wait_for(client.connect(), timeout=5)
    await asyncio.wait_for(client.close(), timeout=5)
    observations.record_invalid_rejection(failure_class)


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=5)
    except (ssl.SSLError, ConnectionError, OSError, TimeoutError):
        pass


def _request(sequence: int, scope: Scope, message_id: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "kind": "request",
        "message_id": message_id,
        "sequence": sequence,
        "route_id": "control",
        "route_epoch": 1,
        "operation": "server.describe",
        "scope": scope.to_wire(),
        "deadline_ms": int((time.time() + 5) * 1000),
        "payload": {},
    }


async def _authenticated_stream(
    record_path: Path, scope: Scope
) -> tuple[HypermidClient, asyncio.StreamReader, asyncio.StreamWriter]:
    client = HypermidClient(record_path, scope=scope)
    record = ConnectionRecord.load(record_path)
    reader, writer = await asyncio.wait_for(client._open(record), timeout=5)
    await asyncio.wait_for(client._handshake(reader, writer, record), timeout=5)
    return client, reader, writer


class CaptureProxy:
    def __init__(self, path: Path, upstream: Path) -> None:
        self.path = path
        self.upstream = upstream
        self.client_bytes = bytearray()
        self.server_bytes = bytearray()
        self.tamper_client = False
        self.tampered = False
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._accept, path=self.path)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        tasks = tuple(self._connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._connections.add(task)
        try:
            await self._relay(reader, writer)
        finally:
            self._connections.discard(task)

    async def _relay(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        upstream_reader, upstream_writer = await asyncio.open_unix_connection(
            self.upstream
        )

        async def forward(
            source: asyncio.StreamReader,
            destination: asyncio.StreamWriter,
            capture: bytearray,
            *,
            client_direction: bool,
        ) -> None:
            try:
                while data := await source.read(65_536):
                    capture.extend(data)
                    if client_direction and self.tamper_client and not self.tampered:
                        changed = bytearray(data)
                        changed[-1] ^= 1
                        data = bytes(changed)
                        self.tampered = True
                    destination.write(data)
                    await destination.drain()
            finally:
                destination.close()

        await asyncio.gather(
            forward(reader, upstream_writer, self.client_bytes, client_direction=True),
            forward(upstream_reader, writer, self.server_bytes, client_direction=False),
            return_exceptions=True,
        )


class TcpCaptureProxy(CaptureProxy):
    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        super().__init__(Path(), Path())
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.listen_host = "127.0.0.1"
        self.listen_port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._accept, host=self.listen_host, port=0
        )
        socket = self._server.sockets[0]
        address = socket.getsockname()
        self.listen_port = int(address[1])

    async def _relay(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            self.upstream_host, self.upstream_port
        )

        async def forward(
            source: asyncio.StreamReader,
            destination: asyncio.StreamWriter,
            capture: bytearray,
            *,
            client_direction: bool,
        ) -> None:
            try:
                while data := await source.read(65_536):
                    capture.extend(data)
                    if client_direction and self.tamper_client and not self.tampered:
                        changed = bytearray(data)
                        changed[-1] ^= 1
                        data = bytes(changed)
                        self.tampered = True
                    destination.write(data)
                    await destination.drain()
            finally:
                destination.close()

        await asyncio.gather(
            forward(reader, upstream_writer, self.client_bytes, client_direction=True),
            forward(upstream_reader, writer, self.server_bytes, client_direction=False),
            return_exceptions=True,
        )


@asynccontextmanager
async def _proxy_record(
    daemon: LiveDaemon, root: Path, name: str
) -> AsyncIterator[tuple[Path, CaptureProxy]]:
    proxy = CaptureProxy(root / f"{name}.sock", daemon.socket)
    await proxy.start()
    record = _record_variant(
        daemon.record, root / f"{name}.json", endpoint=str(proxy.path)
    )
    try:
        yield record, proxy
    finally:
        await proxy.close()


@asynccontextmanager
async def _tcp_proxy_record(
    source_record: Path, root: Path, name: str
) -> AsyncIterator[tuple[Path, TcpCaptureProxy]]:
    raw = json.loads(source_record.read_text(encoding="utf-8"))
    endpoint = str(raw["endpoint"])
    assert endpoint.startswith("tcp://")
    host, port = endpoint.removeprefix("tcp://").rsplit(":", 1)
    proxy = TcpCaptureProxy(host, int(port))
    await proxy.start()
    record = _record_variant(
        source_record,
        root / f"{name}.json",
        endpoint=f"tcp://{proxy.listen_host}:{proxy.listen_port}",
    )
    try:
        yield record, proxy
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_real_tls_hmac_frames_and_ciphertext_capture(
    live_daemon: LiveDaemon,
    tmp_path: Path,
    authentication_observations: AuthenticationGateObservations,
) -> None:
    vectors = json.loads(
        (Path(__file__).parent / "vectors" / "client_v1.json").read_text(
            encoding="utf-8"
        )
    )
    for framed in vectors["frames"].values():
        assert canonical_json_bytes(framed["value"]).decode() == framed["canonical_json"]
        assert base64.b64encode(encode_frame(framed["value"])).decode() == framed[
            "frame_base64"
        ]
        assert Envelope.from_wire(framed["value"]).to_wire() == framed["value"]
    additive = Envelope.from_wire(vectors["additive_response"])
    assert additive.reply_to == "request-3"
    chunk = EventChunk.from_wire(vectors["chunk"]["value"])
    assert EventReassembler().push(chunk).decode() == vectors["chunk"]["decoded_utf8"]

    scope = Scope("owner-auth", "project-auth")
    marker = f"plaintext-marker-{secrets.token_hex(16)}"
    async with _proxy_record(live_daemon, tmp_path, "capture") as (record, proxy):
        client = HypermidClient(record, scope=scope)
        await asyncio.wait_for(client.connect(), timeout=5)
        assert client._writer is not None
        tls = client._writer.get_extra_info("ssl_object")
        assert tls is not None and tls.version() == "TLSv1.3"
        response = await asyncio.wait_for(
            client.passthrough({"marker": marker}), timeout=5
        )
        assert response == {"marker": marker}

        consumer_id = Id("auth-cursor-consumer")
        topic_filter = "auth.*"
        subscription = await asyncio.wait_for(
            client.subscribe_events(
                consumer_id=consumer_id,
                scope=scope,
                topic_filter=topic_filter,
            ),
            timeout=5,
        )
        published = await asyncio.wait_for(
            SubscriptionClient(client).publish(
                event_id=Id("auth-cursor-event"),
                topic="auth.cursor",
                scope=scope,
                at_ms=int(time.time() * 1000),
                schema_name="auth.cursor",
                schema_version=1,
                payload={"accepted": True},
            ),
            timeout=5,
        )
        delivered = await asyncio.wait_for(subscription.__anext__(), timeout=5)
        assert delivered.event_id == published.event_id
        await asyncio.wait_for(subscription.acknowledge(delivered), timeout=5)
        acknowledged_cursor = delivered.cursor
        await asyncio.wait_for(client.close(), timeout=5)

        resumed_client = HypermidClient(record, scope=scope)
        resumed = await asyncio.wait_for(
            resumed_client.resume_events(
                consumer_id=consumer_id,
                scope=scope,
                topic_filter=topic_filter,
                after=acknowledged_cursor,
            ),
            timeout=5,
        )
        assert resumed.point.cursor == acknowledged_cursor
        await asyncio.wait_for(resumed.close(), timeout=5)
        await asyncio.wait_for(resumed_client.close(), timeout=5)
        await asyncio.sleep(0)
        captured = bytes(proxy.client_bytes + proxy.server_bytes)
        assert captured
        assert marker.encode() not in captured
        assert canonical_json_bytes({"marker": marker}) not in captured
        authentication_observations.record_capture(
            "unix",
            captured,
            (marker.encode(), canonical_json_bytes({"marker": marker})),
        )
        authentication_observations.record_compatible_resume(
            resumed.point.cursor == acknowledged_cursor
        )

    record = ConnectionRecord.load(live_daemon.record)
    assert record.credential_bytes().hex() not in repr(record)
    with pytest.raises(TypeError):
        pickle.dumps(record)
    oversized = asyncio.StreamReader()
    oversized.feed_data((MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
    with pytest.raises(HypermidProtocolError):
        await read_frame(oversized)


@pytest.mark.asyncio
async def test_explicit_remote_tls_hmac_ciphertext_and_scope_binding(
    tmp_path: Path,
    authentication_observations: AuthenticationGateObservations,
) -> None:
    bootstrap_root = tmp_path / "bootstrap"
    bootstrap = _start_daemon(bootstrap_root, "bootstrap")
    tls_directory = bootstrap.tls_directory
    bootstrap.stop()

    run_root = tmp_path / "remote"
    run_root.mkdir(mode=0o700)
    remote_record = run_root / "remote.json"
    authorization = _write_remote_authorization(
        run_root / "remote-authorization.json",
        expires_at_ms=int((time.time() + 300) * 1000),
    )
    daemon = _start_daemon(
        run_root,
        "combined",
        tls_directory=tls_directory,
        remote_listen="127.0.0.1:0",
        remote_record=remote_record,
        remote_authorization=authorization,
    )
    try:
        scope = Scope("owner-remote", "project-remote")
        marker = f"remote-plaintext-marker-{secrets.token_hex(16)}"
        async with _tcp_proxy_record(remote_record, run_root, "remote-capture") as (
            proxy_record,
            proxy,
        ):
            client = HypermidClient(proxy_record, scope=scope)
            await asyncio.wait_for(client.connect(), timeout=5)
            assert client._writer is not None
            tls = client._writer.get_extra_info("ssl_object")
            assert tls is not None and tls.version() == "TLSv1.3"
            assert await asyncio.wait_for(
                client.passthrough({"marker": marker}), timeout=5
            ) == {"marker": marker}
            await asyncio.wait_for(client.close(), timeout=5)
            await asyncio.sleep(0)
            captured = bytes(proxy.client_bytes + proxy.server_bytes)
            assert captured
            assert marker.encode() not in captured
            assert canonical_json_bytes({"marker": marker}) not in captured
            authentication_observations.record_capture(
                "tcp",
                captured,
                (marker.encode(), canonical_json_bytes({"marker": marker})),
            )

        baseline = _durable_snapshot(run_root)
        await _connection_fails(
            remote_record,
            Scope("owner-remote", "another-project"),
            authentication_observations,
            "remote-cross-scope",
        )
        assert _durable_snapshot(run_root) == baseline
    finally:
        daemon.stop()


@pytest.mark.asyncio
async def test_invalid_peers_downgrade_and_stale_authorization_fail_closed(
    live_daemon: LiveDaemon,
    tmp_path: Path,
    authentication_observations: AuthenticationGateObservations,
) -> None:
    scope = Scope("owner-auth", "project-auth")
    client = HypermidClient(live_daemon.record, scope=scope)
    valid = await asyncio.wait_for(client.describe_typed(), timeout=5)
    await asyncio.wait_for(client.close(), timeout=5)
    generation = valid.registry_generation
    raw = json.loads(live_daemon.record.read_text(encoding="utf-8"))
    baseline = _durable_snapshot(tmp_path)

    await _connection_fails(
        live_daemon.record,
        Scope("owner-auth", "another-project"),
        authentication_observations,
        "local-cross-scope",
    )

    await _connection_fails(
        _record_variant(
            live_daemon.record,
            tmp_path / "wrong-name.json",
            server_name="wrong.hypermid.local",
        ),
        scope,
        authentication_observations,
        "wrong-server-name",
    )

    now = datetime.now(timezone.utc)
    expired_cert, expired_key, _ = _issue_client(
        tmp_path,
        live_daemon.tls_directory,
        "expired-client",
        not_before=now - timedelta(days=2),
        not_after=now - timedelta(days=1),
    )
    await _connection_fails(
        _record_variant(
            live_daemon.record,
            tmp_path / "expired.json",
            client_certificate=str(expired_cert),
            client_private_key=str(expired_key),
        ),
        scope,
        authentication_observations,
        "expired-client-certificate",
    )

    foreign_ca_key = ec.generate_private_key(ec.SECP256R1())
    foreign_ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Foreign Hypermid CA")]
    )
    foreign_ca = (
        x509.CertificateBuilder()
        .subject_name(foreign_ca_name)
        .issuer_name(foreign_ca_name)
        .public_key(foreign_ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(foreign_ca_key, hashes.SHA256())
    )
    mismatch_cert, mismatch_key, _ = _issue_client(
        tmp_path,
        live_daemon.tls_directory,
        "peer-mismatch",
        not_before=now - timedelta(minutes=1),
        not_after=now + timedelta(days=1),
        issuer=(foreign_ca, foreign_ca_key),
    )
    await _connection_fails(
        _record_variant(
            live_daemon.record,
            tmp_path / "peer-mismatch.json",
            client_certificate=str(mismatch_cert),
            client_private_key=str(mismatch_key),
        ),
        scope,
        authentication_observations,
        "peer-certificate-mismatch",
    )

    stale = _record_variant(
        live_daemon.record,
        tmp_path / "stale.json",
        expires_ms=int(time.time() * 1000) - 1,
    )
    with pytest.raises(HypermidProtocolError, match="expired"):
        ConnectionRecord.load(stale)

    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=raw["ca_certificate"])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(raw["client_certificate"], raw["client_private_key"])
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        await asyncio.wait_for(
            asyncio.open_unix_connection(
                live_daemon.socket,
                ssl=context,
                server_hostname=raw["server_name"],
            ),
            timeout=5,
        )
    authentication_observations.record_invalid_rejection("tls-downgrade")

    client = HypermidClient(live_daemon.record, scope=scope)
    after = await asyncio.wait_for(client.describe_typed(), timeout=5)
    await asyncio.wait_for(client.close(), timeout=5)
    assert after.registry_generation == generation
    assert _durable_snapshot(tmp_path) == baseline


@pytest.mark.asyncio
async def test_hmac_protocol_sequence_and_ciphertext_tamper_are_refused(
    live_daemon: LiveDaemon,
    tmp_path: Path,
    authentication_observations: AuthenticationGateObservations,
) -> None:
    scope = Scope("owner-auth", "project-auth")
    record = ConnectionRecord.load(live_daemon.record)
    wrong_hmac = ConnectionRecord(
        endpoint_kind=record.endpoint_kind,
        secret=b"\xff" * 32,
        path=record.path,
        host=record.host,
        port=record.port,
        protocol=record.protocol,
        credential_id=record.credential_id,
        server_name=record.server_name,
        tls_ca=record.tls_ca,
        tls_cert=record.tls_cert,
        tls_key=record.tls_key,
    )
    client = HypermidClient(live_daemon.record, scope=scope)
    reader, writer = await asyncio.wait_for(client._open(wrong_hmac), timeout=5)
    try:
        with pytest.raises((HypermidConnectionError, HypermidProtocolError)):
            await asyncio.wait_for(
                client._handshake(reader, writer, wrong_hmac), timeout=5
            )
        authentication_observations.record_invalid_rejection("wrong-hmac")
    finally:
        await _close_writer(writer)

    for protocols in (["hypermid.v0"], ["hypermid.v2"]):
        reader, writer = await asyncio.wait_for(client._open(record), timeout=5)
        try:
            await write_frame(
                writer,
                {
                    "kind": "hello",
                    "protocols": protocols,
                    "client_nonce": secrets.token_hex(32),
                    "connection_class": "client",
                    "scope": scope.to_wire(),
                },
            )
            refusal = await asyncio.wait_for(read_frame(reader), timeout=5)
            assert refusal["kind"] == "close"
            assert refusal["error"]["code"] == "UNSUPPORTED_PROTOCOL"
            assert refusal["supported_protocol_min"] == PROTOCOL
            assert refusal["supported_protocol_max"] == PROTOCOL
            authentication_observations.record_invalid_rejection(
                f"unsupported-{protocols[0]}"
            )
        finally:
            await _close_writer(writer)

    _client, reader, writer = await _authenticated_stream(live_daemon.record, scope)
    await write_frame(writer, _request(2, scope, "reordered-request"))
    with pytest.raises((HypermidConnectionError, ConnectionError, OSError)):
        await asyncio.wait_for(read_frame(reader), timeout=5)
    authentication_observations.record_invalid_rejection("reordered-sequence")
    await _close_writer(writer)

    _client, reader, writer = await _authenticated_stream(live_daemon.record, scope)
    replay = _request(1, scope, "replayed-request")
    await write_frame(writer, replay)
    assert (await asyncio.wait_for(read_frame(reader), timeout=5))["sequence"] == 1
    await write_frame(writer, replay)
    with pytest.raises((HypermidConnectionError, ConnectionError, OSError)):
        await asyncio.wait_for(read_frame(reader), timeout=5)
    authentication_observations.record_invalid_rejection("replayed-sequence")
    await _close_writer(writer)

    async with _proxy_record(live_daemon, tmp_path, "tamper") as (path, proxy):
        tampered_client = HypermidClient(path, scope=scope)
        tampered_record = ConnectionRecord.load(path)
        reader, writer = await asyncio.wait_for(
            tampered_client._open(tampered_record), timeout=5
        )
        proxy.tamper_client = True
        try:
            with pytest.raises(
                (
                    ssl.SSLError,
                    HypermidConnectionError,
                    HypermidProtocolError,
                    ConnectionError,
                    OSError,
                )
            ):
                await asyncio.wait_for(
                    tampered_client._handshake(reader, writer, tampered_record),
                    timeout=5,
                )
            assert proxy.tampered
            authentication_observations.record_invalid_rejection(
                "modified-ciphertext"
            )
        finally:
            await _close_writer(writer)


@pytest.mark.asyncio
async def test_revoked_certificate_is_rejected_by_real_daemon(
    tmp_path: Path,
    authentication_observations: AuthenticationGateObservations,
) -> None:
    initial = _start_daemon(tmp_path, "initial")
    tls_directory = initial.tls_directory
    initial.stop()
    now = datetime.now(timezone.utc)
    revoked_cert, revoked_key, certificate = _issue_client(
        tmp_path,
        tls_directory,
        "revoked-client",
        not_before=now - timedelta(minutes=1),
        not_after=now + timedelta(days=1),
    )
    crl = _write_crl(tmp_path / "clients.crl.pem", tls_directory, certificate)
    daemon = _start_daemon(
        tmp_path,
        "revocation",
        tls_directory=tls_directory,
        client_crl=crl,
    )
    try:
        scope = Scope("owner-auth", "project-auth")
        valid = HypermidClient(daemon.record, scope=scope)
        await asyncio.wait_for(valid.connect(), timeout=5)
        await asyncio.wait_for(valid.close(), timeout=5)
        baseline = _durable_snapshot(tmp_path)
        revoked_record = _record_variant(
            daemon.record,
            tmp_path / "revoked.json",
            client_certificate=str(revoked_cert),
            client_private_key=str(revoked_key),
        )
        await _connection_fails(
            revoked_record,
            scope,
            authentication_observations,
            "revoked-client-certificate",
        )
        assert _durable_snapshot(tmp_path) == baseline
    finally:
        daemon.stop()
