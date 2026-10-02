from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from checks.hypermid.test_memory_release import _access, _draft, _mutation, _open_memory
from checks.hypermid.test_security_operations import (
    SecurityDaemon,
    _database_snapshot_digest,
    _start_security_daemon,
)
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.contracts import GrantOperation, MemoryOperation, RecordKind
from gideon.hypermid.foundation import Digest, Id, Scope
from gideon.hypermid.operator_lifecycle import HypermidOperatorLifecycle


def _restart(daemon: SecurityDaemon) -> SecurityDaemon:
    command = list(daemon.process.args)
    daemon.stop()
    daemon.record.unlink(missing_ok=True)
    (daemon.record.parent / "hypermid.sock").unlink(missing_ok=True)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not daemon.record.is_file():
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(
                f"hypermid-daemon exited before restart readiness "
                f"({process.returncode}): {stderr}"
            )
        if time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise AssertionError("hypermid-daemon restart timed out")
        time.sleep(0.01)
    return SecurityDaemon(process, daemon.record, daemon.database)


@pytest.mark.asyncio
async def test_lifecycle_migration_stages_and_reconciles_after_restart(
    tmp_path: Path,
) -> None:
    scope = Scope(Id("migration-owner"), Id("migration-project"), Id("migration-workspace"))
    capability_id = Id("migration-capability")
    record_id = Id("migration-record")
    artifact_id = "migration-snapshot"
    daemon = _start_security_daemon(
        tmp_path,
        "migration-daemon",
        scope,
        capability_id,
        {record_id, Id("memory-portability")},
    )
    client: HypermidClient | None = None
    try:
        client, memory = await _open_memory(daemon, scope, capability_id)
        created = await memory.create(
            _mutation(scope, MemoryOperation.CREATE, record_id, "migration-create"),
            _draft(scope, record_id, RecordKind.FACT, "migration authority survives restart"),
            now_ms=1,
        )
        assert created.record is not None
        source_cursor = created.cursor

        lifecycle = HypermidOperatorLifecycle(client)
        export_plan = await lifecycle.plan("export", destination=artifact_id)
        exported = await lifecycle.apply(
            export_plan,
            reviewed_digest=export_plan.plan_digest,
        )
        assert exported.state == "committed"
        manifest_digest = exported.receipt.artifact_digest
        assert manifest_digest is not None
        active_before = _database_snapshot_digest(daemon.database)

        with pytest.raises(HypermidRemoteError) as invalid:
            await lifecycle.plan(
                "migrate",
                source=artifact_id,
                params={"expected_manifest_digest": str(Digest.sha256(b"wrong manifest"))},
            )
        assert invalid.value.error.code == "RECOVERY_FAILED"
        assert _database_snapshot_digest(daemon.database) == active_before

        plan = await lifecycle.plan(
            "migrate",
            source=artifact_id,
            params={"expected_manifest_digest": manifest_digest},
        )
        assert plan.plan.restart_required
        assert plan.source_digest == manifest_digest
        assert plan.staging_id is not None
        assert tuple(step.id for step in plan.plan.steps) == (
            "verify-recovery-artifact",
            "queue-startup-restore",
        )
        assert _database_snapshot_digest(daemon.database) == active_before

        queued = await lifecycle.apply(
            plan,
            reviewed_digest=plan.plan_digest,
            confirm_destructive=True,
        )
        assert queued.state == "running"
        assert _database_snapshot_digest(daemon.database) == active_before
        job_id = queued.receipt.job_id
        assert (await lifecycle.status(job_id)).state == "running"
        assert (await lifecycle.recover(job_id)).recovery_state == "resumable"

        await client.close()
        client = None
        daemon = _restart(daemon)
        client, memory = await _open_memory(daemon, scope, capability_id)
        lifecycle = HypermidOperatorLifecycle(client)
        recovered = await lifecycle.recover(job_id)
        assert recovered.recovery_state == "committed"
        assert recovered.receipt.state == "committed"
        assert recovered.receipt.receipt.artifact_digest == manifest_digest
        assert recovered.receipt.receipt.cursor == source_cursor
        assert (await lifecycle.status(job_id)) == recovered.receipt

        record, cursor = await memory.get(
            _access(scope, GrantOperation.READ, record_id, "migration-read")
        )
        assert record is not None
        assert record.current.content == "migration authority survives restart"
        assert cursor == source_cursor
    finally:
        if client is not None:
            await client.close()
        daemon.stop()
