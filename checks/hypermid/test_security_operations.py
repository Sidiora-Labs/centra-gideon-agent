from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from gideon.hypermid.contracts import GrantOperation, MemoryOperation, RecordKind
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.credential_authority import (
    attach_security_operations,
    local_backup_credential_name,
)
from gideon.hypermid.foundation import Digest, Id, Scope
from gideon.hypermid.handlers import HypermidHandlerError, HypermidHandlers, service_for
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.lifecycle_security import BackupKey, decrypt_memory_bundle
from gideon.hypermid.network_policy import SecretVault
from gideon.hypermid.operations import HypermidOperations
from gideon.hypermid.operator_lifecycle import HypermidOperatorLifecycle
from gideon.hypermid.security_operations import SecurityOperations
from gideon.integrations.llm.credentials import CredentialStore

from checks.hypermid.test_memory_release import (
    _access,
    _daemon_binary,
    _draft,
    _mutation,
    _open_memory,
)


@dataclass(slots=True)
class SecurityDaemon:
    process: subprocess.Popen[str]
    record: Path
    database: Path

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def _start_security_daemon(
    root: Path, name: str, scope: Scope, capability_id: Id, resources: set[Id]
) -> SecurityDaemon:
    home = root / name
    home.mkdir(mode=0o700)
    socket = home / "hypermid.sock"
    record = home / "connection.json"
    command = [
        str(_daemon_binary()),
        "--socket",
        str(socket),
        "--connection-record",
        str(record),
        "--local-credential-id",
        f"{name}-credential",
        "--local-owner-id",
        str(scope.owner_id),
        "--local-project-id",
        str(scope.project_id),
        "--local-capability-id",
        str(capability_id),
    ]
    if scope.workspace_id is not None:
        command.extend(("--local-workspace-id", str(scope.workspace_id)))
    for operation in ("append", "export", "read"):
        command.extend(("--local-capability-operation", operation))
    for resource in sorted(resources):
        command.extend(("--local-capability-resource", str(resource)))
    command.extend(
        (
            "--local-capability-expires-ms",
            str(int(time.time() * 1000) + 600_000),
        )
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not record.is_file():
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
    database = home / "state" / "memory.sqlite3"
    if not database.is_file():
        process.terminate()
        process.wait(timeout=5)
        raise AssertionError("hypermid-daemon did not create its memory store")
    return SecurityDaemon(process, record, database)


def _database_snapshot_digest(path: Path) -> str:
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
        return hashlib.sha256(snapshot.serialize()).hexdigest()
    finally:
        snapshot.close()
        source.close()


def _runtime_handlers(
    daemon: SecurityDaemon,
    client: object,
    scope: Scope,
    capability_id: Id,
    resources: set[Id],
    credential_store: CredentialStore,
    name: str,
) -> tuple[HypermidLifecycle, HypermidHandlers]:
    lifecycle = HypermidLifecycle(
        HypermidAdapter(client),
        object(),
        connection_record=daemon.record,
        enrollment=LocalEnrollment(
            scope=scope,
            credential_id=Id(f"{name}-credential"),
            capability_id=capability_id,
            operations=("read", "append", "export"),
            resources=tuple(sorted(resources)),
            expires_ms=int(time.time() * 1000) + 600_000,
        ),
    )
    service = attach_security_operations(lifecycle, credential_store=credential_store)
    assert isinstance(service, SecurityOperations)
    return lifecycle, service_for(lifecycle)


@pytest.mark.asyncio
async def test_runtime_credential_authority_backs_real_handler_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gideon.core.config import loader

    credential_home = tmp_path / "gideon-home"
    monkeypatch.setattr(loader, "config_dir", lambda: credential_home)
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    scope = Scope(Id("runtime-owner"), Id("runtime-project"), Id("runtime-workspace"))
    capability_id = Id("runtime-capability")
    record_id = Id("runtime-record")
    resources = {record_id, Id("memory-portability")}
    daemon = _start_security_daemon(
        tmp_path, "runtime-source", scope, capability_id, resources
    )
    client = None
    try:
        client, memory = await _open_memory(daemon, scope, capability_id)
        created = await memory.create(
            _mutation(scope, MemoryOperation.CREATE, record_id, "runtime-create"),
            _draft(scope, record_id, RecordKind.FACT, "credential-backed backup"),
            now_ms=1,
        )
        assert created.record is not None

        key = hashlib.sha256(b"configured runtime backup key").digest()
        store = CredentialStore(credential_home)
        credential_name = local_backup_credential_name(scope)
        store.put(
            credential_name,
            {
                "type": "static_token",
                "value": base64.b64encode(key).decode("ascii"),
            },
        )
        lifecycle = HypermidLifecycle(
            HypermidAdapter(client),
            object(),
            connection_record=daemon.record,
            enrollment=LocalEnrollment(
                scope=scope,
                credential_id=Id("runtime-source-credential"),
                capability_id=capability_id,
                operations=("read", "append", "export"),
                resources=tuple(sorted(resources)),
                expires_ms=int(time.time() * 1000) + 600_000,
            ),
        )
        service = attach_security_operations(lifecycle, credential_store=store)
        assert isinstance(service, SecurityOperations)
        handlers = service_for(lifecycle)
        handles = lifecycle.security_credential_handles
        assert handles["backup"] != handles["restore"]
        assert credential_name not in json.dumps(dict(handles))

        credentials = await handlers.dispatch("security.credentials", {})
        assert credentials == {
            "credentials": [
                {
                    "credential_ref": "local-backup",
                    "label": "Local backup key",
                    "purposes": ["backup", "restore"],
                    "configured": True,
                }
            ]
        }

        artifact = tmp_path / "runtime-backup.hmbk"
        with pytest.raises(HypermidHandlerError) as client_handle:
            await handlers.dispatch(
                "security.backup.plan",
                {
                    "destination": str(artifact),
                    "credential_handle": handles["backup"],
                    "export_id": "runtime-wrong-purpose",
                },
            )
        assert client_handle.value.code == "INVALID_REQUEST"
        plan = await handlers.dispatch(
            "security.backup.plan",
            {
                "destination": str(artifact),
                "credential_ref": "local-backup",
                "export_id": "runtime-export",
            },
        )
        assert plan["credential_ref"] == "local-backup"
        assert handles["backup"] not in json.dumps(plan)
        receipt = await handlers.dispatch(
            "security.backup.apply",
            {"plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"]},
        )
        assert receipt["state"] == "committed"
        assert receipt["credential_ref"] == "local-backup"
        assert artifact.is_file()
        bundle, encrypted = decrypt_memory_bundle(
            artifact,
            scope,
            BackupKey(key),
            expected_artifact_digest=Digest(receipt["artifact_digest"]),
        )
        assert encrypted.export_id == "runtime-export"
        assert bundle.manifest.record_count == 1
        assert key not in artifact.read_bytes()
    finally:
        if client is not None:
            await client.close()
        daemon.stop()


@pytest.mark.asyncio
async def test_real_handler_encrypts_and_imports_canonical_memory_bundle(
    tmp_path: Path,
) -> None:
    scope = Scope(Id("security-owner"), Id("security-project"), Id("security-workspace"))
    capability_id = Id("security-capability")
    record_id = Id("security-record")
    resources = {record_id, Id("memory-portability")}
    source_daemon = _start_security_daemon(
        tmp_path, "security-source", scope, capability_id, resources
    )
    target_daemon = _start_security_daemon(
        tmp_path, "security-target", scope, capability_id, resources
    )
    source_client = None
    target_client = None
    try:
        source_client, source_memory = await _open_memory(
            source_daemon, scope, capability_id
        )
        target_client, target_memory = await _open_memory(
            target_daemon, scope, capability_id
        )
        content = "canonical encrypted memory: alpha beta"
        created = await source_memory.create(
            _mutation(scope, MemoryOperation.CREATE, record_id, "security-create"),
            _draft(scope, record_id, RecordKind.FACT, content),
            now_ms=1,
        )
        assert created.record is not None

        key = hashlib.sha256(b"operator canonical backup key").digest()
        vault = SecretVault()
        backup_handle = vault.register("owner-session", "hypermid.backup", key)
        restore_handle = vault.register("owner-session", "hypermid.restore", key)
        wrong_handle = vault.register(
            "owner-session",
            "hypermid.restore",
            hashlib.sha256(b"wrong canonical backup key").digest(),
        )
        source_security = SecurityOperations(
            scope=scope,
            principal_id="owner-session",
            active_path=source_daemon.database,
            secret_vault=vault,
            supported_schema=1,
            memory_client=source_memory,
        )
        source_handlers = HypermidHandlers(
            HypermidOperations(source_client),
            HypermidOperatorLifecycle(source_client),
            security_operations=source_security,
        )
        artifact = tmp_path / "canonical-memory.hmbk"
        backup_plan = await source_handlers.dispatch(
            "security.backup.plan",
            {
                "destination": str(artifact),
                "credential_handle": backup_handle.identifier,
                "export_id": "security-export",
            },
        )
        assert backup_plan["export_id"] == "security-export"
        assert backup_plan["record_count"] == 1
        assert backup_plan["item_count"] > backup_plan["record_count"]
        assert backup_plan["cursor"]["sequence"] >= 1
        assert backup_handle.identifier not in json.dumps(backup_plan)
        backup = await source_handlers.dispatch(
            "security.backup.apply",
            {
                "plan_id": backup_plan["plan_id"],
                "plan_digest": backup_plan["plan_digest"],
            },
        )
        assert backup["state"] == "committed"
        assert backup["export_id"] == backup_plan["export_id"]
        assert backup["stream_digest"] == backup_plan["stream_digest"]
        assert "bundle_digest" not in backup_plan
        assert backup["item_count"] == backup_plan["item_count"]
        assert backup["record_count"] == backup_plan["record_count"]
        assert backup["cursor"] == backup_plan["cursor"]
        artifact_bytes = artifact.read_bytes()
        assert backup["artifact_digest"] == hashlib.sha256(artifact_bytes).hexdigest()
        assert content.encode() not in artifact_bytes
        decrypted, encrypted_receipt = decrypt_memory_bundle(
            artifact,
            scope,
            BackupKey(key),
            expected_artifact_digest=Digest(backup["artifact_digest"]),
        )
        assert hashlib.sha256(decrypted.to_jsonl()).hexdigest() == backup["source_digest"]
        assert encrypted_receipt.stream_digest == decrypted.manifest.stream_digest

        target_security = SecurityOperations(
            scope=scope,
            principal_id="owner-session",
            active_path=target_daemon.database,
            secret_vault=vault,
            supported_schema=1,
            memory_client=target_memory,
        )
        target_handlers = HypermidHandlers(
            HypermidOperations(target_client),
            HypermidOperatorLifecycle(target_client),
            security_operations=target_security,
        )
        with pytest.raises(HypermidHandlerError) as wrong_key:
            await target_handlers.dispatch(
                "security.restore.plan",
                {
                    "artifact_path": str(artifact),
                    "source_digest": backup["artifact_digest"],
                    "credential_handle": wrong_handle.identifier,
                },
            )
        assert wrong_key.value.code == "BACKUP_AUTHENTICATION_FAILED"
        with sqlite3.connect(target_daemon.database) as connection:
            assert connection.execute("SELECT count(*) FROM memory_records").fetchone() == (0,)

        restore_plan = await target_handlers.dispatch(
            "security.restore.plan",
            {
                "artifact_path": str(artifact),
                "source_digest": backup["artifact_digest"],
                "credential_handle": restore_handle.identifier,
            },
        )
        assert restore_plan["source_digest"] == backup["artifact_digest"]
        assert restore_plan["bundle_digest"] == backup["source_digest"]
        assert restore_plan["batch_source_digest"] == backup["source_digest"]
        assert restore_plan["export_id"] == backup["export_id"]
        assert restore_plan["stream_digest"] == backup["stream_digest"]
        assert restore_plan["item_count"] == backup["item_count"]
        assert restore_plan["record_count"] == backup["record_count"]
        assert restore_plan["cursor"] == backup["cursor"]
        assert restore_handle.identifier not in json.dumps(restore_plan)
        with sqlite3.connect(target_daemon.database) as connection:
            assert connection.execute("SELECT count(*) FROM memory_records").fetchone() == (0,)

        restored = await target_handlers.dispatch(
            "security.restore.apply",
            {
                "plan_id": restore_plan["plan_id"],
                "plan_digest": restore_plan["plan_digest"],
                "confirm_destructive": True,
            },
        )
        assert restored["state"] == "committed"
        assert restored["artifact_digest"] == backup["artifact_digest"]
        assert restored["source_digest"] == backup["source_digest"]
        assert restored["batch_source_digest"] == backup["source_digest"]
        assert restored["batch_state"] == "applied"
        assert restored["replayed"] is False
        assert restored["export_id"] == backup["export_id"]
        assert restored["stream_digest"] == backup["stream_digest"]
        assert restored["item_count"] == backup["item_count"]
        assert restored["record_count"] == backup["record_count"]
        assert restored["cursor"] == backup["cursor"]
        imported, _ = await target_memory.get(
            _access(scope, GrantOperation.READ, record_id, "security-get")
        )
        assert imported is not None
        assert imported.current.content == content
    finally:
        if source_client is not None:
            await source_client.close()
        if target_client is not None:
            await target_client.close()
        source_daemon.stop()
        target_daemon.stop()


@pytest.mark.asyncio
async def test_real_lifecycle_and_encrypted_security_handler_journey(
    tmp_path: Path,
) -> None:
    source_scope = Scope(Id("journey-owner"), Id("journey-project"), Id("journey-workspace"))
    foreign_scope = Scope(Id("foreign-owner"), Id("journey-project"), Id("journey-workspace"))
    capability_id = Id("journey-capability")
    record_id = Id("journey-record")
    resources = {record_id, Id("memory-portability")}
    source_daemon = _start_security_daemon(
        tmp_path, "journey-source", source_scope, capability_id, resources
    )
    interrupted_daemon = _start_security_daemon(
        tmp_path, "journey-interrupted", source_scope, capability_id, resources
    )
    restored_daemon = _start_security_daemon(
        tmp_path, "journey-restored", source_scope, capability_id, resources
    )
    foreign_daemon = _start_security_daemon(
        tmp_path, "journey-foreign", foreign_scope, capability_id, resources
    )
    clients: list[object] = []
    try:
        source_client, source_memory = await _open_memory(
            source_daemon, source_scope, capability_id
        )
        clients.append(source_client)
        interrupted_client, _ = await _open_memory(
            interrupted_daemon, source_scope, capability_id
        )
        clients.append(interrupted_client)
        restored_client, restored_memory = await _open_memory(
            restored_daemon, source_scope, capability_id
        )
        clients.append(restored_client)
        foreign_client, _ = await _open_memory(
            foreign_daemon, foreign_scope, capability_id
        )
        clients.append(foreign_client)
        content = "source-bound encrypted lifecycle journey"
        created = await source_memory.create(
            _mutation(source_scope, MemoryOperation.CREATE, record_id, "journey-create"),
            _draft(source_scope, record_id, RecordKind.FACT, content),
            now_ms=1,
        )
        assert created.record is not None

        credential_store = CredentialStore(tmp_path / "credentials")
        key = hashlib.sha256(b"journey configured backup key").digest()
        wrong_key = hashlib.sha256(b"journey wrong backup key").digest()
        source_credential = local_backup_credential_name(source_scope)
        foreign_credential = local_backup_credential_name(foreign_scope)
        credential_store.put(
            source_credential,
            {
                "type": "static_token",
                "value": base64.b64encode(key).decode("ascii"),
            },
        )
        credential_store.put(
            foreign_credential,
            {
                "type": "static_token",
                "value": base64.b64encode(key).decode("ascii"),
            },
        )
        _, source_handlers = _runtime_handlers(
            source_daemon,
            source_client,
            source_scope,
            capability_id,
            resources,
            credential_store,
            "journey-source",
        )
        _, interrupted_handlers = _runtime_handlers(
            interrupted_daemon,
            interrupted_client,
            source_scope,
            capability_id,
            resources,
            credential_store,
            "journey-interrupted",
        )
        _, restored_handlers = _runtime_handlers(
            restored_daemon,
            restored_client,
            source_scope,
            capability_id,
            resources,
            credential_store,
            "journey-restored",
        )
        _, foreign_handlers = _runtime_handlers(
            foreign_daemon,
            foreign_client,
            foreign_scope,
            capability_id,
            resources,
            credential_store,
            "journey-foreign",
        )

        credentials = await source_handlers.dispatch("security.credentials", {})
        assert credentials["credentials"] == [
            {
                "credential_ref": "local-backup",
                    "label": "Local backup key",
                "purposes": ["backup", "restore"],
                "configured": True,
            }
        ]
        lifecycle_plan = await source_handlers.dispatch(
            "lifecycle.export.plan", {"destination": "journey-snapshot"}
        )
        lifecycle_receipt = await source_handlers.dispatch(
            "lifecycle.export.apply",
            {
                "plan_id": lifecycle_plan["plan_id"],
                "plan_digest": lifecycle_plan["plan_digest"],
            },
        )
        assert lifecycle_receipt["state"] == "committed"
        assert lifecycle_receipt["artifact_digest"]

        artifact = tmp_path / "journey-memory.hmbk"
        backup_plan = await source_handlers.dispatch(
            "security.backup.plan",
            {
                "destination": str(artifact),
                "credential_ref": "local-backup",
                "export_id": "journey-export",
            },
        )
        journal_bytes = b"".join(
            path.read_bytes()
            for path in (source_daemon.database.parent / "security-operations").rglob("*.json")
        )
        assert content.encode() not in journal_bytes
        assert key not in journal_bytes
        _, source_apply_handlers = _runtime_handlers(
            source_daemon,
            source_client,
            source_scope,
            capability_id,
            resources,
            credential_store,
            "journey-source",
        )
        backup = await source_apply_handlers.dispatch(
            "security.backup.apply",
            {
                "plan_id": backup_plan["plan_id"],
                "plan_digest": backup_plan["plan_digest"],
            },
        )
        assert backup["state"] == "committed"
        assert backup["credential_ref"] == "local-backup"

        interrupted_before = _database_snapshot_digest(interrupted_daemon.database)
        credential_store.put(
            source_credential,
            {
                "type": "static_token",
                "value": base64.b64encode(wrong_key).decode("ascii"),
            },
        )
        with pytest.raises(HypermidHandlerError) as wrong_key_error:
            await interrupted_handlers.dispatch(
                "security.restore.plan",
                {
                    "artifact_path": str(artifact),
                    "source_digest": backup["artifact_digest"],
                },
            )
        assert wrong_key_error.value.code == "BACKUP_AUTHENTICATION_FAILED"
        assert _database_snapshot_digest(interrupted_daemon.database) == interrupted_before
        credential_store.put(
            source_credential,
            {
                "type": "static_token",
                "value": base64.b64encode(key).decode("ascii"),
            },
        )

        foreign_before = _database_snapshot_digest(foreign_daemon.database)
        with pytest.raises(HypermidHandlerError) as foreign_error:
            await foreign_handlers.dispatch(
                "security.restore.plan",
                {
                    "artifact_path": str(artifact),
                    "source_digest": backup["artifact_digest"],
                },
            )
        assert foreign_error.value.code == "SCOPE_MISMATCH"
        assert _database_snapshot_digest(foreign_daemon.database) == foreign_before

        interrupted_plan = await interrupted_handlers.dispatch(
            "security.restore.plan",
            {
                "artifact_path": str(artifact),
                "source_digest": backup["artifact_digest"],
            },
        )
        _, interrupted_apply_handlers = _runtime_handlers(
            interrupted_daemon,
            interrupted_client,
            source_scope,
            capability_id,
            resources,
            credential_store,
            "journey-interrupted",
        )
        original_send = interrupted_client._send
        import_dispatched = asyncio.Event()

        async def kill_after_import_dispatch(envelope: object) -> None:
            await original_send(envelope)
            if getattr(envelope, "operation", None) == "memory.import.apply":
                def kill_daemon() -> None:
                    interrupted_daemon.process.kill()
                    import_dispatched.set()

                asyncio.get_running_loop().call_soon(kill_daemon)

        interrupted_client._send = kill_after_import_dispatch
        uncertain = await interrupted_apply_handlers.dispatch(
            "security.restore.apply",
            {
                "plan_id": interrupted_plan["plan_id"],
                "plan_digest": interrupted_plan["plan_digest"],
                "confirm_destructive": True,
            },
        )
        assert import_dispatched.is_set()
        interrupted_daemon.process.wait(timeout=5)
        assert uncertain["state"] == "outcome_unknown"
        assert uncertain["effect_state"] == "unknown"
        assert uncertain["artifact_digest"] == backup["artifact_digest"]
        assert uncertain["source_digest"] == backup["source_digest"]
        assert uncertain["stream_digest"] == backup["stream_digest"]
        assert uncertain["export_id"] == backup["export_id"]
        assert uncertain["item_count"] == backup["item_count"]
        assert uncertain["record_count"] == backup["record_count"]
        assert uncertain["cursor"] == backup["cursor"]
        assert uncertain["batch_source_digest"] == backup["source_digest"]
        assert uncertain["credential_ref"] == "local-backup"
        assert uncertain["batch_id"] == interrupted_plan["staging_id"]
        _, interrupted_recovery_handlers = _runtime_handlers(
            interrupted_daemon,
            interrupted_client,
            source_scope,
            capability_id,
            resources,
            credential_store,
            "journey-interrupted",
        )
        assert await interrupted_recovery_handlers.dispatch(
            "security.recover", {"job_id": uncertain["job_id"]}
        ) == uncertain

        restored_plan = await restored_handlers.dispatch(
            "security.restore.plan",
            {
                "artifact_path": str(artifact),
                "source_digest": backup["artifact_digest"],
            },
        )
        _, restored_apply_handlers = _runtime_handlers(
            restored_daemon,
            restored_client,
            source_scope,
            capability_id,
            resources,
            credential_store,
            "journey-restored",
        )
        restored = await restored_apply_handlers.dispatch(
            "security.restore.apply",
            {
                "plan_id": restored_plan["plan_id"],
                "plan_digest": restored_plan["plan_digest"],
                "confirm_destructive": True,
            },
        )
        assert restored["state"] == "committed"
        imported, _ = await restored_memory.get(
            _access(source_scope, GrantOperation.READ, record_id, "journey-read")
        )
        assert imported is not None
        assert imported.current.content == content
    finally:
        for client in clients:
            await client.close()
        source_daemon.stop()
        interrupted_daemon.stop()
        restored_daemon.stop()
        foreign_daemon.stop()
