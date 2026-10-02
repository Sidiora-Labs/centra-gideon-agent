from __future__ import annotations

import pytest

from gideon.hypermid.maintenance import (
    LeaseReceipt,
    MaintenanceJob,
    ModelReservation,
    publication_request,
    require_explicit_relocation_authority,
    settlement_request,
)
from gideon.hypermid.model_budget import ActualUsage, BudgetAmount, ModelBudget
from gideon.hypermid.models import Cursor, Scope, Trace


def _job() -> MaintenanceJob:
    return MaintenanceJob(
        job_id="job-1",
        kind="refresh_summaries",
        scope=Scope("record-owner", "record-project", "workspace-1"),
        actor_scope=Scope("worker-owner", "worker-project", "workspace-1"),
        required_operation="update",
        input_cursor=Cursor(1, 4),
        input_digest="1" * 64,
        config_digest="2" * 64,
        budget=ModelBudget(2, 100, 50, 2, 10, 1, 5_000),
        trace=Trace("trace-1", "request-1"),
        available_at_ms=1_000,
    )


def test_maintenance_wire_keeps_fence_predicates_and_unknown_usage_explicit() -> None:
    job = _job()
    publication = publication_request(
        job,
        LeaseReceipt("job-1", "worker-1", "secret-token", 2_000),
        current_input_digest="1" * 64,
        current_config_digest="2" * 64,
    )
    assert publication["actor_scope"] == job.actor_scope.to_wire()
    assert publication["target_scope"] == job.scope.to_wire()
    assert publication["fencing_token"] == "secret-token"

    settlement = settlement_request(
        ModelReservation(
            "reservation-1", "job-1", BudgetAmount(input_tokens=40, cost_units=5)
        ),
        ActualUsage(input_tokens=None, cost_units=0),
    )
    assert "input_tokens" in settlement["unknown_fields"]
    assert settlement["actual"]["cost_units"] == 0
    assert "cost_units" not in {
        name for name in settlement["unknown_fields"] if name == "cost_units"
    }


def test_workspace_membership_does_not_replace_explicit_foreign_grant() -> None:
    source = Scope("worker-owner", "worker-project", "workspace-1")
    destination = Scope("record-owner", "record-project", "workspace-1")
    with pytest.raises(PermissionError, match="AUTHORIZATION_DENIED"):
        require_explicit_relocation_authority(source, destination, None, now_ms=1_000)

    grant = {
        "operations": ["relocate"],
        "target_scope": destination.to_wire(),
        "expires_at_ms": 2_000,
        "revoked_at_ms": None,
    }
    require_explicit_relocation_authority(source, destination, grant, now_ms=1_000)
    grant["revoked_at_ms"] = 1_100
    with pytest.raises(PermissionError, match="AUTHORIZATION_DENIED"):
        require_explicit_relocation_authority(source, destination, grant, now_ms=1_200)
