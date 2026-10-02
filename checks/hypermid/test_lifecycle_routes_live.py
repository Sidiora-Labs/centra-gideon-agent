from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from checks.hypermid.test_memory_release import _draft, _mutation, _open_memory
from checks.hypermid.test_security_operations import _start_security_daemon
from gideon.hypermid.contracts import MemoryOperation, RecordKind
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.operator_lifecycle import HypermidOperatorLifecycle


@pytest.mark.asyncio
async def test_lifecycle_export_uses_live_memory_authority(tmp_path: Path) -> None:
    scope = Scope(Id("lifecycle-owner"), Id("lifecycle-project"), Id("lifecycle-workspace"))
    capability_id = Id("lifecycle-capability")
    record_id = Id("lifecycle-record")
    artifact_id = "lifecycle-focused-snapshot"
    daemon = _start_security_daemon(
        tmp_path,
        "lifecycle-source",
        scope,
        capability_id,
        {record_id, Id("memory-portability")},
    )
    client = None
    try:
        client, memory = await _open_memory(daemon, scope, capability_id)
        created = await memory.create(
            _mutation(scope, MemoryOperation.CREATE, record_id, "lifecycle-create"),
            _draft(scope, record_id, RecordKind.FACT, "lifecycle snapshot authority"),
            now_ms=1,
        )
        assert created.record is not None

        lifecycle = HypermidOperatorLifecycle(client)
        plan = await lifecycle.plan("export", destination=artifact_id)
        assert plan.plan.operation == "lifecycle.export.apply"
        assert plan.destination == artifact_id
        assert plan.estimated_items == 1
        assert plan.source_digest is not None
        assert not plan.plan.blockers

        receipt = await lifecycle.apply(plan, reviewed_digest=plan.plan_digest)
        assert receipt.state == "committed"
        assert receipt.receipt.plan_digest == plan.plan_digest
        assert receipt.receipt.artifact_digest is not None
        assert receipt.artifact_bytes is not None and receipt.artifact_bytes > 0
        assert receipt.artifact_path is not None

        manifest = Path(receipt.artifact_path)
        expected_artifact = daemon.database.parent / "recovery" / artifact_id
        assert manifest == expected_artifact / "manifest.json"
        assert manifest.is_file() and not manifest.is_symlink()
        contents = manifest.read_bytes()
        assert len(contents) == receipt.artifact_bytes
        assert hashlib.sha256(contents).hexdigest() == receipt.receipt.artifact_digest
        assert (expected_artifact / "store.sqlite3").is_file()

        status = await lifecycle.status(receipt.receipt.job_id)
        assert status == receipt
    finally:
        if client is not None:
            await client.close()
        daemon.stop()
