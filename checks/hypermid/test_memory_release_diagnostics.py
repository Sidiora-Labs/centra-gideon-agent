from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from checks.hypermid.test_memory_release import (
    _access,
    _draft,
    _open_memory,
    _start_daemon,
    _trace,
)
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.contracts import (
    GrantOperation,
    MaintenanceJobSpec,
    MaintenanceKind,
    MemoryOperation,
    MemoryContractError,
    MemoryDiagnostics,
    MutationRequest,
    RecordKind,
    RevisionPrecondition,
)
from gideon.hypermid.foundation import Digest, Id, Scope
from gideon.hypermid.model_budget import ModelBudget


def test_release_diagnostics_old_wire_is_conservative_and_strict() -> None:
    legacy = {
        "schema_version": 2,
        "record_count": 0,
        "stale_record_count": 0,
        "embedding_count": 0,
        "queued_job_count": 0,
        "active_lease_count": 0,
    }
    parsed = MemoryDiagnostics.from_wire(legacy)
    assert parsed.memory_fts_count is None
    assert parsed.source_fts_count is None
    assert parsed.budget_job_count is None
    assert parsed.budget_state == "disabled"
    assert parsed.recovery_state == "degraded"

    with pytest.raises(MemoryContractError, match="unexpected fields"):
        MemoryDiagnostics.from_wire({**legacy, "content": "must not cross diagnostics"})


def _maintenance_request(
    scope: Scope,
    job_id: Id | None,
    suffix: str,
) -> MutationRequest:
    return MutationRequest(
        operation=MemoryOperation.SUMMARIZE,
        actor_scope=scope,
        target_scope=scope,
        record_id=job_id,
        category=None,
        revision=RevisionPrecondition.must_not_exist(),
        trace=_trace(suffix),
    )


@pytest.mark.asyncio
async def test_release_diagnostics_report_real_scoped_state_without_secrets(
    tmp_path: Path,
) -> None:
    scope = Scope(Id("diagnostic-owner"), Id("diagnostic-project"))
    capability_id = Id("diagnostic-capability")
    record_id = Id("diagnostic-record")
    diagnostics_id = Id("diagnostic-read")
    job_id = Id("diagnostic-job")
    secret_content = "private memory content diagnostic-canary"
    secret_credential = "diagnostic-credential-canary"
    resources = {
        record_id,
        diagnostics_id,
        job_id,
        Id("memory-maintenance"),
        Id("memory-records"),
        Id("memory-service"),
    }
    daemon = _start_daemon(
        tmp_path,
        "diagnostics",
        scope,
        capability_id,
        resources,
    )
    client: HypermidClient | None = None
    try:
        client, memory = await _open_memory(daemon, scope, capability_id)
        created = await memory.create(
            MutationRequest(
                operation=MemoryOperation.CREATE,
                actor_scope=scope,
                target_scope=scope,
                record_id=record_id,
                category="release",
                revision=RevisionPrecondition.must_not_exist(),
                trace=_trace("diagnostic-create"),
            ),
            _draft(
                scope,
                record_id,
                RecordKind.FACT,
                secret_content,
                metadata={"credential_reference": secret_credential},
            ),
            now_ms=1,
        )
        assert created.record is not None

        access = _access(
            scope,
            GrantOperation.READ,
            diagnostics_id,
            "diagnostic-before-job",
        )
        before = await memory.diagnostics(access)
        assert before.schema_version >= 2
        assert before.record_count == 1
        assert before.stale_record_count == 0
        assert before.memory_fts_count == 1
        assert before.source_fts_count == 0
        assert before.embedding_count == 0
        assert before.queued_job_count == 0
        assert before.active_lease_count == 0
        assert before.budget_state == "available"
        assert before.budget_job_count == 0
        assert before.budget_reservation_count == 0
        assert before.budget_unknown_usage_count == 0
        assert before.recovery_state == "ready"

        budget = ModelBudget(
            max_items=2,
            max_input_tokens=256,
            max_output_tokens=128,
            max_requests=2,
            max_cost_units=20,
            max_retries=1,
            max_wall_ms=20_000,
        )
        await memory.summary_enqueue(
            _maintenance_request(scope, job_id, "diagnostic-enqueue"),
            MaintenanceJobSpec(
                id=job_id,
                kind=MaintenanceKind.REFRESH_SUMMARIES,
                target_scope=scope,
                actor_scope=scope,
                required_operation=MemoryOperation.SUMMARIZE,
                input_cursor=created.cursor,
                input_digest=Digest.sha256(b"diagnostic-input"),
                config_digest=Digest.sha256(b"diagnostic-config"),
                budget=budget,
                available_at_ms=0,
                created_at_ms=1,
            ),
        )
        queued = await memory.diagnostics(
            _access(
                scope,
                GrantOperation.READ,
                diagnostics_id,
                "diagnostic-queued",
            )
        )
        assert queued.queued_job_count == 1
        assert queued.active_lease_count == 0
        assert queued.budget_state == "available"
        assert queued.budget_job_count == 1
        assert queued.budget_reservation_count == 0
        assert queued.budget_unknown_usage_count == 0
        assert queued.recovery_state == "ready"

        claim = await memory.maintenance_claim(
            _maintenance_request(scope, None, "diagnostic-claim"),
            worker_id=Id("diagnostic-worker"),
            ttl_ms=5_000,
        )
        assert claim is not None and claim.job_id == job_id
        leased = await memory.diagnostics(
            _access(
                scope,
                GrantOperation.READ,
                diagnostics_id,
                "diagnostic-leased",
            )
        )
        assert leased.queued_job_count == 0
        assert leased.active_lease_count == 1
        assert leased.budget_job_count == 1
        assert leased.recovery_state == "ready"

        encoded = json.dumps(asdict(leased), sort_keys=True)
        lowered_keys = {key.lower() for key in asdict(leased)}
        assert secret_content not in encoded
        assert secret_credential not in encoded
        assert str(capability_id) not in encoded
        assert not lowered_keys.intersection(
            {"content", "captured_content", "credential", "secret", "token"}
        )

        await memory.maintenance_terminate(
            _maintenance_request(scope, job_id, "diagnostic-terminate"),
            proof=claim.proof,
            state="abandoned",
            error_code="DIAGNOSTIC_GATE_COMPLETE",
        )
        final = await memory.diagnostics(
            _access(
                scope,
                GrantOperation.READ,
                diagnostics_id,
                "diagnostic-final",
            )
        )
        assert final.queued_job_count == 0
        assert final.active_lease_count == 0
        assert final.budget_job_count == 1
        assert final.recovery_state == "ready"
        await client.close()
        client = None
    finally:
        if client is not None:
            await client.close()
        daemon.stop()
