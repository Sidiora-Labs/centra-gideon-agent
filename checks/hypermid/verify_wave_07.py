from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
import platform
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from aiohttp import ClientSession, web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_service import MemoryService
from gideon.cognition.vector_memory import SemanticArchive
from gideon.hypermid.authority_operations import DaemonWriterLeaseAuthority
from gideon.hypermid.background_coordinator import quiesce_summary_work, resume_summary_work
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.context import (
    ConversationContextBridge,
    ConversationLogSourceAdapter,
    session_id_for_key,
)
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    MemoryOperation,
    MutationRequest,
    RevisionPrecondition,
)
from gideon.hypermid.export import RustMemoryPortability, load_memory_bundle
from gideon.hypermid.foundation import Cursor, Digest, Id, Scope, Trace
from gideon.hypermid.handlers import HypermidHandlers
from gideon.hypermid.lifecycle_security import BackupKey, decrypt_memory_bundle
from gideon.hypermid.memory import (
    HypermidMemoryProvider,
    install_as_memory_authority,
    uninstall_memory_authority,
)
from gideon.hypermid.memory_client import MemoryClient
from gideon.hypermid.network_policy import SecretVault
from gideon.hypermid.migration import (
    GideonMemorySource,
    MigrationCoordinator,
    MigrationError,
    MigrationStore,
    RustLegacyImport,
    negotiate_versions,
)
from gideon.hypermid.portability import (
    ContextExportBuilder,
    ContextPortabilityEntryKind,
    ContextSessionBinding,
)
from gideon.hypermid.operations import HypermidOperations
from gideon.hypermid.operator_lifecycle import HypermidOperatorLifecycle
from gideon.hypermid.security_operations import SecurityOperations
from gideon.hypermid.writer import GideonCutoverHooks, WriterCoordinator
from migration_observed_matrix import (
    MigrationObservedMatrix,
    future_store_observation,
    migration_observation_writer,
    migration_store_state,
    probe_digest_mismatch,
)


CAPABILITY_ID = Id("capability-wave-07")
SESSION_KEY = "session-wave-07"


def _register_hypermid_routes(app: web.Application) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/interfaces/dashboard/handlers/hypermid.py"
    )
    spec = importlib.util.spec_from_file_location("_wave07_hypermid_handlers", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("native Hypermid dashboard handler could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register_hypermid_routes(app)


def _daemon_binary() -> str:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError("focused journey requires a built hypermid-daemon")


async def _start_daemon(root: Path, scope: Scope) -> tuple[asyncio.subprocess.Process, Path]:
    record = root / "daemon" / "connection.json"
    socket = root / "daemon" / "hypermid.sock"
    record.unlink(missing_ok=True)
    socket.unlink(missing_ok=True)
    command = [
        _daemon_binary(), "--socket", str(socket), "--connection-record", str(record),
        "--local-credential-id", "credential-wave-07",
        "--local-owner-id", str(scope.owner_id),
        "--local-project-id", str(scope.project_id),
        "--local-workspace-id", str(scope.workspace_id),
        "--local-capability-id", str(CAPABILITY_ID),
        "--local-capability-operation", "append",
        "--local-capability-operation", "export",
        "--local-capability-operation", "read",
        "--local-capability-operation", "revise",
        "--local-capability-resource", "memory-portability",
        "--local-capability-resource", "memory-list",
        "--local-capability-resource", "memory-maintenance",
        "--local-capability-resource", "semantic:user.name",
        "--local-capability-expires-ms", str(time.time_ns() // 1_000_000 + 300_000),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    deadline = asyncio.get_running_loop().time() + 10
    while not record.is_file():
        if process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr is not None else b""
            raise RuntimeError(
                f"Hypermid daemon exited before readiness ({process.returncode}): "
                f"{stderr.decode(errors='replace')}"
            )
        if asyncio.get_running_loop().time() >= deadline:
            process.terminate()
            await process.wait()
            raise TimeoutError("Hypermid daemon connection record was not published")
        await asyncio.sleep(0.02)
    return process, record


async def _stop_daemon(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


def _platform() -> dict[str, str]:
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x86_64",
        "arm64": "aarch64",
    }.get(machine, machine)
    return {"os": platform.system().lower(), "arch": architecture}


def _signed_package(
    state_root: Path,
    *,
    signing_key: Ed25519PrivateKey,
    artifact_id: str,
    version: str,
    content: bytes,
) -> None:
    entrypoint = "bin/hypermid-wave-07"
    archive_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=archive_buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            info = tarfile.TarInfo(entrypoint)
            info.size = len(content)
            info.mode = 0o755
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    archive_bytes = archive_buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    manifest = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "version": version,
        "platform": _platform(),
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "publisher_id": "wave-07-release",
        "source_uri": f"file:///wave-07/{artifact_id}/{version}",
        "source_sha256": digest,
        "capabilities": [],
        "entrypoint": entrypoint,
        "files": [
            {
                "path": entrypoint,
                "kind": "regular",
                "sha256": digest,
                "size": len(content),
                "executable": True,
            }
        ],
    }
    canonical = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode()
    package_root = state_root / "lifecycle" / "packages" / artifact_id / version
    package_root.mkdir(parents=True, mode=0o700)
    (package_root / "archive.tar.gz").write_bytes(archive_bytes)
    (package_root / "manifest.json").write_text(
        json.dumps(
            {"manifest": manifest, "signature": signing_key.sign(canonical).hex()},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _prepare_release_packages(state_root: Path) -> None:
    signing_key = Ed25519PrivateKey.from_private_bytes(bytes([19]) * 32)
    trust_root = state_root / "lifecycle" / "trust"
    trust_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    verifying_key = signing_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    (trust_root / "wave-07-release.pub").write_text(
        verifying_key.hex(), encoding="ascii"
    )
    _signed_package(
        state_root,
        signing_key=signing_key,
        artifact_id="hypermid-wave-07",
        version="1.0.0",
        content=b"#!/bin/sh\necho hypermid-wave-07-v1\n",
    )
    _signed_package(
        state_root,
        signing_key=signing_key,
        artifact_id="hypermid-wave-07",
        version="2.0.0",
        content=b"#!/bin/sh\necho hypermid-wave-07-v2\n",
    )


async def _exercise_release_lifecycle(
    lifecycle: HypermidOperatorLifecycle,
    state_root: Path,
    enrollment_expires_ms: int,
) -> None:
    _prepare_release_packages(state_root)
    install = await lifecycle.plan(
        "install",
        target_version="1.0.0",
        params={
            "artifact_id": "hypermid-wave-07",
            "approved_capabilities": [],
            "local_enrollment": {
                "operations": ["append", "export", "read", "revise"],
                "resources": [
                    "memory-portability",
                    "memory-list",
                    "memory-maintenance",
                    "semantic:user.name",
                ],
                "expires_ms": enrollment_expires_ms,
            },
        },
    )
    assert install.current_version is None and install.target_version == "1.0.0"
    installed = await lifecycle.apply(install, reviewed_digest=install.plan_digest)
    assert installed.state == "committed" and installed.enrollment is not None

    update = await lifecycle.plan(
        "update",
        target_version="2.0.0",
        params={"artifact_id": "hypermid-wave-07", "approved_capabilities": []},
    )
    assert update.current_version == "1.0.0" and update.rollback_digest is not None
    updated = await lifecycle.apply(update, reviewed_digest=update.plan_digest)
    assert updated.state == "committed" and updated.receipt.rollback_available

    try:
        await lifecycle.plan(
            "update",
            target_version="1.0.0",
            params={"artifact_id": "hypermid-wave-07", "approved_capabilities": []},
        )
    except HypermidRemoteError as error:
        assert error.error.code == "DOWNGRADE_REFUSED"
    else:
        raise AssertionError("lifecycle accepted an unreviewed version downgrade")

    rollback = await lifecycle.plan("rollback", source=update.rollback_digest)
    assert rollback.rollback_digest == update.rollback_digest
    rolled_back = await lifecycle.apply(
        rollback,
        reviewed_digest=rollback.plan_digest,
        confirm_destructive=True,
    )
    assert rolled_back.state == "committed"
    after_rollback = await lifecycle.plan(
        "update",
        target_version="2.0.0",
        params={"artifact_id": "hypermid-wave-07", "approved_capabilities": []},
    )
    assert after_rollback.current_version == "1.0.0"


def _trace(name: str) -> Trace:
    return Trace(Id(f"trace-{name}"), Id(f"request-{name}"))


def _request(scope: Scope, operation: MemoryOperation, name: str) -> MutationRequest:
    return MutationRequest(
        operation=operation,
        actor_scope=scope,
        target_scope=scope,
        revision=RevisionPrecondition.must_not_exist(),
        trace=_trace(name),
    )


def _legacy_memory(scope: Scope, root: Path):
    journal = MemoryJournal(root / "gideon-home")
    journal.init()
    journal.write_preferences("# User Preferences\n- concise\n")
    journal.write_projects("Hypermid migration")
    archive = SemanticArchive(root / "legacy-memory.db")
    archive.init()
    archive.graph_enabled = True
    service = MemoryService(archive, vector_store=archive)
    assert service.set_semantic("user.name", "Tony", 1.0, "user_explicit") is None
    assert service.write_episodic(
        "Completed a copy-validate-cutover migration.",
        conversation_id="wave-07",
        tags=["migration"],
        source="user_explicit",
    )
    assert service.write_lesson("Validate before cutover", category="operations")
    assert service.slot_append("decisions", "ConversationLog remains authoritative")
    entity_id = service.graph_add_entity("Hypermid", "project")
    assert entity_id
    assert archive.graph.add_link(
        from_kind="semantic",
        from_ref="user.name",
        link_type="mentions",
        to_entity=entity_id,
        source="user_explicit",
    )
    assert archive.append_event(
        event_type="migration_probe",
        memory_type="semantic",
        memory_key="user.name",
        old_value=None,
        new_value="Tony",
        source="user_explicit",
    ) > 0
    conversation_root = root / "conversations"
    log = ConversationLog(base_dir=conversation_root)
    log.append(SESSION_KEY, "user", "retain these authoritative bytes")
    transcript = log._path(SESSION_KEY)
    transcript_before = transcript.read_bytes()
    snapshot = GideonMemorySource(scope, service, journal).snapshot(conversation_root)
    return archive, journal, log, snapshot, transcript, transcript_before


def _context_bundle(scope: Scope, log: ConversationLog):
    events = log.source_events(SESSION_KEY)
    assert len(events) == 1
    event = events[0]
    session_id = session_id_for_key(SESSION_KEY)
    binding = ContextSessionBinding(scope, session_id, Cursor(1, 1))
    builder = ContextExportBuilder(Id("manifest-wave-07"), binding, "2026-10-02T00:00:00Z")
    builder.add(
        Id("source-reference-wave-07"),
        ContextPortabilityEntryKind.SOURCE_REFERENCE,
        {
            "scope": scope.to_wire(),
            "session_id": str(session_id),
            "item_id": "item-wave-07",
            "source_event_id": event.source_event_id,
            "source_digest": event.source_digest,
            "cursor": Cursor(1, 1).to_wire(),
        },
    )
    builder.add(
        Id("summary-wave-07"),
        ContextPortabilityEntryKind.SUMMARY,
        {
            "scope": scope.to_wire(),
            "session_id": str(session_id),
            "summary": "Transcript authority remains external.",
            "covered_cursor": Cursor(1, 1).to_wire(),
        },
    )
    return builder.build(), ConversationLogSourceAdapter(
        log, scope=scope, session_key=SESSION_KEY
    )


async def _cli(
    connection_record: Path,
    scope: Scope,
    *arguments: str,
) -> dict[str, object]:
    command = [
        sys.executable,
        "-c",
        "from gideon.interfaces.cli.main import main; main()",
        "hypermid",
        "--json",
        "--connection-record",
        str(connection_record),
        "--owner",
        str(scope.owner_id),
        "--project",
        str(scope.project_id),
    ]
    if scope.workspace_id is not None:
        command.extend(("--workspace", str(scope.workspace_id)))
    command.extend(arguments)
    environment = os.environ.copy()
    cli_home = connection_record.parent.parent / "cli-home"
    cli_home.mkdir(mode=0o700, exist_ok=True)
    environment["GIDEON_HOME"] = str(cli_home)
    process = await asyncio.create_subprocess_exec(
        *command,
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise AssertionError(
            f"Hypermid CLI failed ({process.returncode}): "
            f"{stderr.decode(errors='replace')}"
        )
    value = json.loads(stdout)
    if not isinstance(value, dict):
        raise AssertionError("Hypermid CLI did not emit a JSON object")
    return value


async def _exercise_native_operations(
    handlers: HypermidHandlers,
    connection_record: Path,
    scope: Scope,
    *,
    backup_credential_handle: str,
    restore_credential_handle: str,
) -> None:
    app = web.Application()
    app["state"] = SimpleNamespace(
        hypermid=SimpleNamespace(
            handlers=handlers,
            security_credential_handles={
                "backup": backup_credential_handle,
                "restore": restore_credential_handle,
            },
        )
    )
    _register_hypermid_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        server = site._server
        if server is None or not server.sockets:
            raise AssertionError("dashboard handler server did not bind")
        port = server.sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        async with ClientSession() as session:
            async with session.get(
                f"{base_url}/api/hypermid/diagnostics?refresh=true"
            ) as response:
                assert response.status == 200
                diagnostics = await response.json()
            assert diagnostics["scope"] == scope.to_wire()
            assert diagnostics["cached"] is False
            assert diagnostics["checks"]
            async with session.get(
                f"{base_url}/api/hypermid/diagnostics"
            ) as response:
                assert response.status == 200
                cached_diagnostics = await response.json()
            assert cached_diagnostics["cached"] is True
            assert cached_diagnostics["cursor"] == diagnostics["cursor"]

            async with session.post(
                f"{base_url}/api/hypermid/operations/maintenance/plan",
                json={"action": "integrity_check", "params": {}},
            ) as response:
                assert response.status == 200
                plan = await response.json()
            assert plan["operation"] == "integrity_check"
            assert not plan["blockers"]
            async with session.post(
                f"{base_url}/api/hypermid/operations/maintenance/apply",
                json={
                    "plan_id": plan["plan_id"],
                    "plan_digest": plan["plan_digest"],
                    "confirm_destructive": False,
                },
            ) as response:
                assert response.status == 200
                receipt = await response.json()
            deadline = asyncio.get_running_loop().time() + 10
            while receipt["state"] == "running":
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("maintenance job did not reach a terminal state")
                await asyncio.sleep(0.02)
                async with session.get(
                    f"{base_url}/api/hypermid/operations/maintenance/jobs/{receipt['job_id']}"
                ) as response:
                    assert response.status == 200
                    receipt = await response.json()
            assert receipt["state"] == "committed"
            assert receipt["plan_digest"] == plan["plan_digest"]
            async with session.post(
                f"{base_url}/api/hypermid/operations/maintenance/jobs/{receipt['job_id']}/cancel"
            ) as response:
                assert response.status == 200
                cancelled = await response.json()
            assert cancelled == receipt

        status = await _cli(connection_record, scope, "status")
        assert status["status"] in {"healthy", "degraded", "failing", "unknown"}
        assert status["daemon_instance_id"]
        cli_diagnostics = await _cli(
            connection_record,
            scope,
            "doctor",
            "--refresh",
        )
        assert cli_diagnostics["scope"] == scope.to_wire()
        assert cli_diagnostics["cached"] is False
        assert cli_diagnostics["checks"]
        cli_plan = await _cli(
            connection_record,
            scope,
            "maintenance",
            "plan",
            "integrity_check",
        )
        assert cli_plan["operation"] == "integrity_check"
        assert cli_plan["scope"] == scope.to_wire()
    finally:
        await runner.cleanup()


async def _exercise_lifecycle_recovery_surface(
    handlers: HypermidHandlers,
    connection_record: Path,
    scope: Scope,
    job_id: str,
) -> None:
    app = web.Application()
    app["state"] = SimpleNamespace(hypermid=SimpleNamespace(handlers=handlers))
    _register_hypermid_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        server = site._server
        if server is None or not server.sockets:
            raise AssertionError("dashboard lifecycle handler server did not bind")
        port = server.sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        async with ClientSession() as session:
            async with session.get(
                f"{base_url}/api/hypermid/operations/lifecycle/jobs/{job_id}"
            ) as response:
                assert response.status == 200
                status = await response.json()
            assert status["state"] == "running" and status["finished_at"] is None
            async with session.post(
                f"{base_url}/api/hypermid/operations/lifecycle/jobs/{job_id}/recover"
            ) as response:
                assert response.status == 200
                recovered = await response.json()
            assert recovered["recovery_state"] == "resumable"
            assert recovered["receipt"]["job_id"] == job_id
            async with session.post(
                f"{base_url}/api/hypermid/operations/lifecycle/jobs/{job_id}/resume",
                json={},
            ) as response:
                assert response.status == 200
                resumed = await response.json()
            assert resumed["state"] == "running" and resumed["job_id"] == job_id

        cli_recovery = await _cli(
            connection_record,
            scope,
            "lifecycle",
            "recover",
            job_id,
        )
        assert cli_recovery["recovery_state"] == "resumable"
        assert cli_recovery["receipt"]["receipt"]["job_id"] == job_id
    finally:
        await runner.cleanup()


async def run_wave_07_journey() -> None:
    with tempfile.TemporaryDirectory(prefix="hypermid-wave-07-") as temporary:
        root = Path(temporary)
        scope = Scope(Id("owner-wave-07"), Id("project-wave-07"), Id("workspace-wave-07"))
        archive, journal, log, source, transcript, transcript_before = _legacy_memory(scope, root)
        context_bundle, source_adapter = _context_bundle(scope, log)
        migration_matrix = MigrationObservedMatrix()
        migration_matrix.observe_fixture(source)
        migration_matrix.observe_digest_mismatch(
            probe_digest_mismatch(
                root / "migration" / "digest-mismatch.sqlite3", scope, source
            )
        )
        migration_writer = migration_observation_writer()
        previous_home = os.environ.get("GIDEON_HOME")
        os.environ["GIDEON_HOME"] = str(root / "gideon-home-isolated")
        process: asyncio.subprocess.Process | None = None
        client: HypermidClient | None = None
        provider: HypermidMemoryProvider | None = None
        installed_service: object | None = None
        try:
            process, connection_record = await _start_daemon(root, scope)
            connection_record_before = connection_record.read_bytes()
            connection_identity = json.loads(connection_record_before)
            daemon_secret = connection_identity["secret_b64"].encode()
            daemon_secret_raw = base64.b64decode(connection_identity["secret_b64"])
            client = HypermidClient(connection_record, scope=scope)
            await client.connect()
            memory = MemoryClient(client, capability_id=CAPABILITY_ID)
            credential = b"wave-07-host-credential-preserved"
            vault = SecretVault()
            credential_handle = vault.register(
                "owner-session", "host.credential", credential
            )
            backup_key = hashlib.sha256(b"wave-07-project-backup-key").digest()
            backup_handle = vault.register(
                "owner-session", "hypermid.backup", backup_key
            )
            restore_handle = vault.register(
                "owner-session", "hypermid.restore", backup_key
            )
            provider = HypermidMemoryProvider(connection_record, scope=scope, capability_id=CAPABILITY_ID)
            provider.init()
            progress_path = root / "migration" / "progress.sqlite3"
            with MigrationStore(progress_path, scope) as interrupted:
                partial = interrupted.import_snapshot(source)
                assert partial.imported == len(source.items)
            with MigrationStore(progress_path, scope) as store:
                resumed = store.import_snapshot(source)
                assert resumed.imported == 0 and resumed.existing == len(source.items)
                migration_matrix.observe_local_resume(partial, resumed)
                migration = MigrationCoordinator(store)
                local_validation = migration.stage(
                    source,
                    context_bundles=(context_bundle,),
                    source_adapter=source_adapter,
                )
                assert local_validation.counts["page"] == 2
                assert store.status()["authority"] == "gideon"
                destination = RustLegacyImport(
                    memory,
                    snapshot=source,
                    request=_request(scope, MemoryOperation.IMPORT, "legacy-import"),
                    batch_id=Id("legacy-batch-wave-07"),
                    verification_request=_request(scope, MemoryOperation.EXPORT, "legacy-validation-export"),
                    verification_export_id=Id("legacy-validation-wave-07"),
                )
                context_bridge = ConversationContextBridge(log, root / "context-journals", scope=scope)
                consolidator = HistoryConsolidator(log, journal)
                writer: WriterCoordinator | None = None

                def install_writer(_lease) -> None:
                    nonlocal installed_service
                    assert writer is not None and provider is not None
                    provider.bind_writer(writer)
                    installed_service = install_as_memory_authority(provider)

                def uninstall_writer() -> None:
                    nonlocal installed_service
                    if installed_service is not None:
                        uninstall_memory_authority(installed_service)
                        installed_service = None

                hooks = GideonCutoverHooks(
                    log=log,
                    context=context_bridge,
                    consolidator=consolidator,
                    memory_flush=destination.prepare,
                    summary_quiesce=quiesce_summary_work,
                    summary_resume=resume_summary_work,
                    install_writer=install_writer,
                    uninstall_writer=uninstall_writer,
                    session_keys=lambda: (SESSION_KEY,),
                )
                writer = WriterCoordinator(
                    mode="primary",
                    scope=scope,
                    authority=DaemonWriterLeaseAuthority(client),
                    hooks=hooks,
                )
                cutover = await migration.cutover(
                    expected_authority_digest=str(store.authority_digest()),
                    destination=destination,
                    writer=writer,
                )
                assert cutover.authority == "hypermid"
                assert writer.snapshot().owns_writes
                destination_validation = destination.require_validation()
                assert destination_validation.record_count == len(source.items)
                assert store.status()["authority"] == "hypermid"
                assert migration.context_imports.visible(scope, context_bundle.binding.session_id) is not None
                assert all(
                    checkpoint.authority.value == "hypermid"
                    for checkpoint in migration.context_checkpoints()
                )
                replay = await memory.import_legacy(
                    destination.request,
                    batch_id=destination.batch_id,
                    snapshot=source.to_contract(),
                )
                assert replay.batch.replayed is True
                assert replay.destination_digest == destination_validation.destination_digest
                record, _cursor = await memory.get(
                    AccessRequest(
                        operation=GrantOperation.READ,
                        actor_scope=scope,
                        target_scope=scope,
                        resource_id=Id("semantic:user.name"),
                        trace=_trace("read-migrated-record"),
                    )
                )
                assert record is not None and record.current.content == "Tony"
                assert record.current.metadata["legacy_source_identity"] == "SemanticArchive:user.name"
                store.record_effect("unknown-wave-07", "unknown")
                try:
                    await migration.rollback(
                        expected_authority_digest=str(store.authority_digest()), writer=writer
                    )
                except MigrationError as error:
                    assert error.code == "OUTCOME_UNKNOWN"
                else:
                    raise AssertionError("rollback accepted an unknown effect")
                assert writer.snapshot().owns_writes
                store.record_effect("unknown-wave-07", "committed")
                rollback = await migration.rollback(
                    expected_authority_digest=str(store.authority_digest()), writer=writer
                )
                assert rollback.authority == "gideon"
                assert not writer.snapshot().owns_writes
                assert all(
                    checkpoint.authority.value == "previous"
                    for checkpoint in migration.context_checkpoints()
                )
                assert vault.dispatch_with_secret(
                    credential_handle,
                    principal_id="owner-session",
                    operation="host.credential",
                    dispatch=bytes,
                ) == credential
                portability = RustMemoryPortability(memory)
                try:
                    await memory.export_scope(
                        _request(scope, MemoryOperation.EXPORT, "grant-export-refusal"),
                        export_id=Id("grant-export-refusal-wave-07"),
                        include_grants=True,
                    )
                except HypermidRemoteError as error:
                    assert error.error.code == "AUTHORIZATION_EXPORT_DENIED"
                except ValueError as error:
                    assert str(error) == "memory export does not permit reusable grant material"
                else:
                    raise AssertionError("memory export included reusable grants")
                artifact_path = root / "exports" / "memory.json"
                artifact = await portability.export_to_file(
                    _request(scope, MemoryOperation.EXPORT, "artifact-export"),
                    export_id="artifact-wave-07",
                    destination=artifact_path,
                )
                bundle, loaded = load_memory_bundle(
                    artifact_path, expected_digest=str(artifact.artifact_digest)
                )
                assert loaded.stream_digest == destination_validation.destination_digest
                restored = await portability.restore(
                    _request(scope, MemoryOperation.IMPORT, "artifact-restore"),
                    batch_id="restore-batch-wave-07",
                    bundle=bundle,
                    target_scope=scope,
                    scope_mapping={},
                    verification_request=_request(scope, MemoryOperation.EXPORT, "restore-validation"),
                    verification_export_id="restore-validation-wave-07",
                )
                assert restored.destination_stream_digest == loaded.stream_digest
                database = connection_record.parent / "state" / "memory.sqlite3"
                security = SecurityOperations(
                    scope=scope,
                    principal_id="owner-session",
                    active_path=database,
                    secret_vault=vault,
                    supported_schema=1,
                    memory_client=memory,
                )
                operator_lifecycle = HypermidOperatorLifecycle(client)
                handlers = HypermidHandlers(
                    HypermidOperations(client),
                    operator_lifecycle,
                    security_operations=security,
                )
                await _exercise_native_operations(
                    handlers,
                    connection_record,
                    scope,
                    backup_credential_handle=backup_handle.identifier,
                    restore_credential_handle=restore_handle.identifier,
                )
                await _exercise_release_lifecycle(
                    operator_lifecycle,
                    database.parent,
                    time.time_ns() // 1_000_000 + 300_000,
                )
                encrypted_artifact = root / "exports" / "project.hmbk"
                backup_plan = await handlers.dispatch(
                    "security.backup.plan",
                    {
                        "destination": str(encrypted_artifact),
                        "export_id": "wave-07-project-backup",
                    },
                )
                backup = await handlers.dispatch(
                    "security.backup.apply",
                    {
                        "plan_id": backup_plan["plan_id"],
                        "plan_digest": backup_plan["plan_digest"],
                    },
                )
                assert backup["state"] == "committed"
                assert backup["stream_digest"] == backup_plan["stream_digest"]
                assert backup["cursor"] == backup_plan["cursor"]
                assert backup["record_count"] == backup_plan["record_count"]
                assert backup["item_count"] == backup_plan["item_count"]
                decrypted, encrypted_receipt = decrypt_memory_bundle(
                    encrypted_artifact,
                    scope,
                    BackupKey(backup_key),
                    expected_artifact_digest=Digest(backup["artifact_digest"]),
                )
                assert (
                    hashlib.sha256(decrypted.to_jsonl()).hexdigest()
                    == backup["source_digest"]
                )
                assert (
                    encrypted_receipt.stream_digest
                    == decrypted.manifest.stream_digest
                )
                restore_plan = await handlers.dispatch(
                    "security.restore.plan",
                    {
                        "artifact_path": str(encrypted_artifact),
                        "source_digest": backup["artifact_digest"],
                    },
                )
                restored_project = await handlers.dispatch(
                    "security.restore.apply",
                    {
                        "plan_id": restore_plan["plan_id"],
                        "plan_digest": restore_plan["plan_digest"],
                        "confirm_destructive": True,
                    },
                )
                assert restored_project["state"] == "committed"
                assert restored_project["artifact_digest"] == backup["artifact_digest"]
                assert restored_project["source_digest"] == backup["source_digest"]
                assert restored_project["stream_digest"] == backup["stream_digest"]
                lifecycle_export_plan = await operator_lifecycle.plan(
                    "export", destination="wave07-migration"
                )
                lifecycle_export = await operator_lifecycle.apply(
                    lifecycle_export_plan,
                    reviewed_digest=lifecycle_export_plan.plan_digest,
                )
                assert lifecycle_export.state == "committed"
                assert lifecycle_export.receipt.artifact_digest is not None
                assert lifecycle_export.artifact_path is not None
                encoded_portability = json.dumps(
                    {
                        "backup_plan": backup_plan,
                        "backup": backup,
                        "restore_plan": restore_plan,
                        "restore": restored_project,
                    },
                    sort_keys=True,
                ).encode()
                assert credential not in encoded_portability
                assert backup_key not in encoded_portability
                assert daemon_secret not in encoded_portability
                assert daemon_secret_raw not in encoded_portability
                assert backup_handle.identifier.encode() not in encoded_portability
                assert restore_handle.identifier.encode() not in encoded_portability
                encrypted_bytes = encrypted_artifact.read_bytes()
                assert credential not in encrypted_bytes
                assert backup_key not in encrypted_bytes
                assert daemon_secret not in encrypted_bytes
                assert daemon_secret_raw not in encrypted_bytes
                with sqlite3.connect(database) as connection:
                    revision_digest = connection.execute(
                        "SELECT current_revision_digest FROM memory_records WHERE record_id=?",
                        ("page:preferences",),
                    ).fetchone()[0]
                    assert connection.execute(
                        "SELECT COUNT(*) FROM memory_fts_rows WHERE record_id=?",
                        ("page:preferences",),
                    ).fetchone()[0] == 1
                deleted_at = time.time_ns() // 1_000_000
                tombstone_plan = await handlers.dispatch(
                    "security.tombstone.plan",
                    {
                        "record_id": "page:preferences",
                        "revision_digest": revision_digest,
                        "now_ms": deleted_at,
                        "retention_until_ms": deleted_at + 1_000,
                    },
                )
                tombstone = await handlers.dispatch(
                    "security.tombstone.apply",
                    {
                        "plan_id": tombstone_plan["plan_id"],
                        "plan_digest": tombstone_plan["plan_digest"],
                        "confirm_destructive": True,
                    },
                )
                encoded_security = json.dumps(
                    {"plan": tombstone_plan, "receipt": tombstone}, sort_keys=True
                ).encode()
                assert credential not in encoded_security
                assert credential_handle.identifier.encode() not in encoded_security
                with sqlite3.connect(database) as connection:
                    for table in (
                        "memory_fts_rows",
                        "memory_embeddings",
                        "memory_retrieval_stats",
                    ):
                        if connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchone():
                            assert connection.execute(
                                f"SELECT COUNT(*) FROM {table} WHERE record_id=?",
                                ("page:preferences",),
                            ).fetchone()[0] == 0
                    assert connection.execute(
                        "SELECT COUNT(*) FROM memory_fts "
                        "JOIN memory_fts_rows f ON f.rowid=memory_fts.rowid "
                        "WHERE memory_fts MATCH ? AND f.record_id=?",
                        ("concise", "page:preferences"),
                    ).fetchone()[0] == 0
                purge_plan = await handlers.dispatch(
                    "security.purge.plan",
                    {
                        "record_id": "page:preferences",
                        "now_ms": deleted_at + 1_001,
                    },
                )
                purge = await handlers.dispatch(
                    "security.purge.apply",
                    {
                        "plan_id": purge_plan["plan_id"],
                        "plan_digest": purge_plan["plan_digest"],
                        "confirm_destructive": True,
                        "confirm_purge": True,
                    },
                )
                assert purge["state"] == "committed"
                with sqlite3.connect(database) as connection:
                    for table in ("memory_records", "memory_revisions", "memory_provenance"):
                        assert connection.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE record_id=?",
                            ("page:preferences",),
                        ).fetchone()[0] == 0
                    assert connection.execute(
                        "SELECT purged_at_ms FROM hypermid_lifecycle_tombstones WHERE record_id=?",
                        ("page:preferences",),
                    ).fetchone()[0] == deleted_at + 1_001
                artifact_bytes = artifact_path.read_bytes()
                assert credential not in artifact_bytes
                assert daemon_secret not in artifact_bytes
                assert daemon_secret_raw not in artifact_bytes
                assert credential_handle.identifier.encode() not in artifact_bytes
                recovery_artifact = database.parent / "recovery" / "wave07-migration"
                for exported_file in recovery_artifact.iterdir():
                    if exported_file.is_file():
                        exported_bytes = exported_file.read_bytes()
                        assert credential not in exported_bytes
                        assert backup_key not in exported_bytes
                        assert daemon_secret not in exported_bytes
                        assert daemon_secret_raw not in exported_bytes
                        assert b"credential-wave-07" not in exported_bytes
                assert vault.dispatch_with_secret(
                    credential_handle,
                    principal_id="owner-session",
                    operation="host.credential",
                    dispatch=bytes,
                ) == credential
                assert vault.dispatch_with_secret(
                    backup_handle,
                    principal_id="owner-session",
                    operation="hypermid.backup",
                    dispatch=bytes,
                ) == backup_key
                assert vault.dispatch_with_secret(
                    restore_handle,
                    principal_id="owner-session",
                    operation="hypermid.restore",
                    dispatch=bytes,
                ) == backup_key
                assert progress_path.is_file()
                assert transcript.read_bytes() == transcript_before
                assert connection_record.read_bytes() == connection_record_before
                migrate_plan = await operator_lifecycle.plan(
                    "migrate",
                    source="wave07-migration",
                    params={
                        "expected_manifest_digest": lifecycle_export.receipt.artifact_digest
                    },
                )
                assert migrate_plan.plan.restart_required
                queued_migration = await operator_lifecycle.apply(
                    migrate_plan,
                    reviewed_digest=migrate_plan.plan_digest,
                    confirm_destructive=True,
                )
                assert queued_migration.state == "running"
                migration_job_id = queued_migration.receipt.job_id
                interrupted_status = await operator_lifecycle.status(migration_job_id)
                assert interrupted_status.state == "running"
                interrupted_recovery = await operator_lifecycle.recover(migration_job_id)
                assert interrupted_recovery.recovery_state == "resumable"
                resumed_migration = await operator_lifecycle.resume(migration_job_id)
                assert resumed_migration.state == "running"
                await _exercise_lifecycle_recovery_surface(
                    handlers,
                    connection_record,
                    scope,
                    migration_job_id,
                )

                provider.close()
                provider = None
                await client.close()
                client = None
                await _stop_daemon(process)
                process = None

                process, connection_record = await _start_daemon(root, scope)
                restarted_record = json.loads(connection_record.read_bytes())
                assert restarted_record["credential_id"] == "credential-wave-07"
                client = HypermidClient(connection_record, scope=scope)
                await client.connect()
                memory = MemoryClient(client, capability_id=CAPABILITY_ID)
                operator_lifecycle = HypermidOperatorLifecycle(client)
                completed_migration = await operator_lifecycle.recover(migration_job_id)
                assert completed_migration.recovery_state == "committed"
                assert completed_migration.receipt.state == "committed"
                assert (
                    completed_migration.receipt.receipt.artifact_digest
                    == lifecycle_export.receipt.artifact_digest
                )
                assert completed_migration.receipt.artifact_bytes
                assert (await operator_lifecycle.status(migration_job_id)).state == "committed"
                migration_matrix.observe_lifecycle_interruption(
                    before_restart=interrupted_status.state,
                    recovery_before_restart=interrupted_recovery.recovery_state,
                    resumed_before_restart=resumed_migration.state,
                    recovery_after_restart=completed_migration.recovery_state,
                    completed_state=completed_migration.receipt.state,
                )
                with sqlite3.connect(database) as connection:
                    assert connection.execute(
                        "SELECT COUNT(*) FROM memory_records WHERE record_id=?",
                        ("page:preferences",),
                    ).fetchone() == (1,)
                    assert connection.execute(
                        "SELECT COUNT(*) FROM memory_fts "
                        "JOIN memory_fts_rows f ON f.rowid=memory_fts.rowid "
                        "WHERE memory_fts MATCH ? AND f.record_id=?",
                        ("concise", "page:preferences"),
                    ).fetchone() == (1,)
                record, _cursor = await memory.get(
                    AccessRequest(
                        operation=GrantOperation.READ,
                        actor_scope=scope,
                        target_scope=scope,
                        resource_id=Id("semantic:user.name"),
                        trace=_trace("read-after-startup-migration"),
                    )
                )
                assert record is not None and record.current.content == "Tony"
                assert vault.dispatch_with_secret(
                    credential_handle,
                    principal_id="owner-session",
                    operation="host.credential",
                    dispatch=bytes,
                ) == credential
            current = negotiate_versions(
                client_current=7, server_min=6, server_max=7, storage_version=1
            )
            previous = negotiate_versions(
                client_current=7, server_min=6, server_max=6, storage_version=0
            )
            assert current.protocol_version == 7 and current.storage_mode == "ready"
            assert previous.protocol_version == 6
            assert previous.storage_mode == "read_only_migration_required"
            future_path = root / "migration" / "future.sqlite3"
            with MigrationStore(future_path, scope):
                pass
            with sqlite3.connect(future_path) as future:
                future.execute(
                    "UPDATE migration_meta SET schema_version=2 WHERE singleton=1"
                )
            schema_before, state_digest_before = migration_store_state(future_path)
            future_error_code = ""
            try:
                MigrationStore(future_path, scope)
            except MigrationError as error:
                assert error.code == "STORE_AHEAD"
                future_error_code = error.code
            else:
                raise AssertionError("newer storage was opened or downgraded")
            schema_after, state_digest_after = migration_store_state(future_path)
            assert schema_after == 2
            migration_matrix.observe_future_store(
                future_store_observation(
                    schema_before=schema_before,
                    state_digest_before=state_digest_before,
                    schema_after=schema_after,
                    state_digest_after=state_digest_after,
                    error_code=future_error_code,
                )
            )
            migration_matrix.emit_observation(
                migration_writer, root / "migration-matrix.json"
            )
        finally:
            if installed_service is not None:
                uninstall_memory_authority(installed_service)
            if provider is not None:
                provider.close()
            if client is not None:
                await client.close()
            await _stop_daemon(process)
            if previous_home is None:
                os.environ.pop("GIDEON_HOME", None)
            else:
                os.environ["GIDEON_HOME"] = previous_home
            archive.close()


if __name__ == "__main__":
    asyncio.run(run_wave_07_journey())
    print("wave 07 live migration journey: passed")
