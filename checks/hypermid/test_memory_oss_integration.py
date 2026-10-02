from __future__ import annotations

from pathlib import Path

import pytest

from checks.hypermid.test_memory_release import (
    _access,
    _draft,
    _mutation,
    _open_memory,
    _scope_digest,
    _start_daemon,
    _trace,
)
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.contracts import (
    GrantOperation,
    MaintenanceJobSpec,
    MaintenanceKind,
    MemoryOperation,
    MutationRequest,
    ProvenanceSpan,
    RecordKind,
    RevisionPrecondition,
    SourceKind,
    SourceSnapshot,
)
from gideon.hypermid.foundation import Digest, Id, Scope
from gideon.hypermid.model_budget import ModelBudget


@pytest.mark.asyncio
async def test_clean_oss_profile_round_trips_authoritative_memory(tmp_path: Path) -> None:
    scope = Scope(Id("oss-release-owner"), Id("oss-release-project"))
    capability_id = Id("oss-release-capability")
    record_id = Id("oss-release-record")
    list_id = Id("oss-release-list")
    export_id = Id("oss-release-export")
    restored_export_id = Id("oss-release-restored-export")
    import_id = Id("oss-release-import")
    job_id = Id("oss-release-maintenance")
    resources = {
        record_id,
        list_id,
        export_id,
        restored_export_id,
        import_id,
        job_id,
        Id("memory-maintenance"),
        Id("memory-portability"),
        Id("memory-records"),
        Id("memory-service"),
    }

    source_daemon = _start_daemon(
        tmp_path, "oss-source", scope, capability_id, resources
    )
    source_client: HypermidClient | None = None
    try:
        source_client, memory = await _open_memory(
            source_daemon, scope, capability_id
        )
        health = await memory.health(trace=_trace("oss-health"))
        assert health.state == "ready"
        assert health.durable is True
        assert health.lexical_available is True

        source_bytes = "authoritative release source β".encode()
        source = SourceSnapshot(
            source_id=Id("oss-release-source"),
            owner_scope_digest=_scope_digest(scope),
            kind=SourceKind.MESSAGE,
            source_digest=Digest.sha256(source_bytes),
            locator="oss-release:source-1",
            captured_content=source_bytes.decode(),
            capture_method="release-gate",
            observed_at_ms=1,
        )
        draft = _draft(
            scope,
            record_id,
            RecordKind.FACT,
            "authoritative release fact",
            provenance=(
                ProvenanceSpan(
                    source.source_id,
                    quoted_digest=source.source_digest,
                ),
            ),
        )
        created = await memory.create(
            _mutation(
                scope,
                MemoryOperation.CREATE,
                record_id,
                "oss-create",
            ),
            draft,
            sources=(source,),
            now_ms=1,
        )
        assert created.record is not None

        fetched, fetched_cursor = await memory.get(
            _access(scope, GrantOperation.READ, record_id, "oss-get")
        )
        assert fetched == created.record
        assert fetched_cursor == created.cursor
        page = await memory.list(
            _access(scope, GrantOperation.READ, list_id, "oss-list"),
            category="release",
        )
        assert [record.id for record in page.records] == [record_id]

        maintenance_request = MutationRequest(
            operation=MemoryOperation.SUMMARIZE,
            actor_scope=scope,
            target_scope=scope,
            record_id=job_id,
            category=None,
            revision=RevisionPrecondition.must_not_exist(),
            trace=_trace("oss-enqueue"),
        )
        spec = MaintenanceJobSpec(
            id=job_id,
            kind=MaintenanceKind.REFRESH_SUMMARIES,
            target_scope=scope,
            actor_scope=scope,
            required_operation=MemoryOperation.SUMMARIZE,
            input_cursor=page.cursor,
            input_digest=Digest.sha256(b"oss-release-input"),
            config_digest=Digest.sha256(b"oss-release-config"),
            budget=ModelBudget(
                max_items=1,
                max_input_tokens=128,
                max_output_tokens=64,
                max_requests=1,
                max_cost_units=10,
                max_retries=0,
                max_wall_ms=10_000,
            ),
            available_at_ms=0,
            created_at_ms=1,
        )
        await memory.summary_enqueue(maintenance_request, spec)
        claim_request = MutationRequest(
            operation=MemoryOperation.SUMMARIZE,
            actor_scope=scope,
            target_scope=scope,
            record_id=None,
            category=None,
            revision=RevisionPrecondition.must_not_exist(),
            trace=_trace("oss-claim"),
        )
        claim = await memory.maintenance_claim(
            claim_request,
            worker_id=Id("oss-release-worker"),
            ttl_ms=5_000,
        )
        assert claim is not None and claim.job_id == job_id
        terminate_request = MutationRequest(
            operation=MemoryOperation.SUMMARIZE,
            actor_scope=scope,
            target_scope=scope,
            record_id=job_id,
            category=None,
            revision=RevisionPrecondition.must_not_exist(),
            trace=_trace("oss-terminate"),
        )
        await memory.maintenance_terminate(
            terminate_request,
            proof=claim.proof,
            state="abandoned",
            error_code="OSS_RELEASE_GATE_COMPLETE",
        )

        exported = await memory.export_scope(
            _mutation(scope, MemoryOperation.EXPORT, None, "oss-export"),
            export_id=export_id,
        )
        assert exported.manifest.record_count == 1
        assert any(
            entry.kind == "source"
            and entry.payload["captured_content"] == source_bytes.decode()
            for entry in exported.entries
        )
        await source_client.close()
        source_client = None
    finally:
        if source_client is not None:
            await source_client.close()
        source_daemon.stop()

    restored_daemon = _start_daemon(
        tmp_path, "oss-restored", scope, capability_id, resources
    )
    restored_client: HypermidClient | None = None
    try:
        restored_client, restored_memory = await _open_memory(
            restored_daemon, scope, capability_id
        )
        import_request = _mutation(scope, MemoryOperation.IMPORT, None, "oss-import")
        batch = await restored_memory.stage_import(
            import_request,
            batch_id=import_id,
            bundle=exported,
            target_scope=scope,
            scope_mapping={},
        )
        assert batch.state == "validated" and batch.replayed is False
        applied = await restored_memory.apply_import(
            import_request,
            batch=batch,
            bundle=exported,
        )
        assert applied.state == "applied" and applied.replayed is False

        restored, _ = await restored_memory.get(
            _access(scope, GrantOperation.READ, record_id, "oss-restored-get")
        )
        assert restored is not None
        assert restored.current.digest == created.record.current.digest
        assert restored.current.content == created.record.current.content
        restored_page = await restored_memory.list(
            _access(scope, GrantOperation.READ, list_id, "oss-restored-list"),
            category="release",
        )
        assert [record.id for record in restored_page.records] == [record_id]

        restored_export = await restored_memory.export_scope(
            _mutation(
                scope,
                MemoryOperation.EXPORT,
                None,
                "oss-restored-export",
            ),
            export_id=restored_export_id,
        )
        authoritative_kinds = {"record", "revision", "source", "provenance"}
        original_entries = [
            (entry.kind, entry.item_key, entry.payload)
            for entry in exported.entries
            if entry.kind in authoritative_kinds
        ]
        restored_entries = [
            (entry.kind, entry.item_key, entry.payload)
            for entry in restored_export.entries
            if entry.kind in authoritative_kinds
        ]
        assert restored_entries == original_entries

        drained = await restored_memory.drain(trace=_trace("oss-drain"))
        assert drained.state == "draining"
        await restored_client.close()
        restored_client = None
    finally:
        if restored_client is not None:
            await restored_client.close()
        restored_daemon.stop()
