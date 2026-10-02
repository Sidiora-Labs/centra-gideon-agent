from __future__ import annotations

import pytest

from gideon.hypermid.models import Scope
from gideon.hypermid.operations import (
    ActionPlan,
    ActionReceipt,
    DiagnosticsSnapshot,
    LogPage,
    OperatorContractError,
)


SCOPE = Scope("owner-1", "project-1", "workspace-1")
DIGEST = "a" * 64


def test_operator_contract_preserves_authority_receipts_and_redacts_observations() -> None:
    diagnostics = DiagnosticsSnapshot.from_wire(
        {
            "scope": SCOPE.to_wire(),
            "observed_at": "2026-10-02T12:00:00Z",
            "cursor": {"epoch": 7, "sequence": 19},
            "cached": True,
            "checks": [
                {
                    "id": "daemon",
                    "title": "Daemon",
                    "health": "degraded",
                    "observed_at": "2026-10-02T11:59:58Z",
                    "cached": True,
                    "summary": "token=must-not-leak",
                    "next_action": "rerun after recovery",
                    "evidence_digest": DIGEST,
                }
            ],
        },
        SCOPE,
    )
    assert diagnostics.cached is True
    assert diagnostics.checks[0].summary == "[REDACTED]"

    logs = LogPage.from_wire(
        {
            "scope": SCOPE.to_wire(),
            "entries": [
                {
                    "cursor": {"epoch": 7, "sequence": 20},
                    "observed_at": "2026-10-02T12:00:01Z",
                    "severity": "warning",
                    "component": "adapter",
                    "message": "Bearer abc.def",
                    "fields": {"api_key": "must-not-leak", "attempt": 2},
                }
            ],
            "cursor": {"epoch": 7, "sequence": 20},
            "gap": True,
            "recovery_cursor": {"epoch": 7, "sequence": 18},
        },
        SCOPE,
    )
    assert logs.gap is True
    assert logs.entries[0].message == "[REDACTED]"
    assert logs.entries[0].fields["api_key"] == "[REDACTED]"

    plan = ActionPlan.from_wire(
        {
            "plan_id": "plan-1",
            "operation": "maintenance.reindex.apply",
            "scope": SCOPE.to_wire(),
            "created_at": "2026-10-02T12:00:00Z",
            "expires_at": "2026-10-02T12:10:00Z",
            "plan_digest": DIGEST,
            "destructive": False,
            "restart_required": False,
            "steps": [
                {
                    "id": "verify",
                    "title": "Verify source digests",
                    "effect": "read",
                    "state": "planned",
                }
            ],
            "blockers": [],
            "authority_digest": "b" * 64,
            "blocker_digest": "c" * 64,
        },
        SCOPE,
    )
    receipt = ActionReceipt.from_wire(
        {
            "job_id": "job-1",
            "operation": "maintenance.reindex.apply",
            "scope": SCOPE.to_wire(),
            "plan_digest": plan.plan_digest,
            "state": "committed",
            "started_at": "2026-10-02T12:00:02Z",
            "finished_at": "2026-10-02T12:00:03Z",
            "cursor": {"epoch": 7, "sequence": 22},
            "steps": [
                {
                    "id": "verify",
                    "title": "Verify source digests",
                    "effect": "read",
                    "state": "committed",
                }
            ],
            "rollback_available": False,
        },
        SCOPE,
    )
    assert receipt.terminal is True
    assert receipt.plan_digest == plan.plan_digest

    with pytest.raises(OperatorContractError, match="recovery cursor"):
        LogPage.from_wire(
            {
                "scope": SCOPE.to_wire(),
                "entries": [],
                "cursor": {"epoch": 7, "sequence": 22},
                "gap": True,
            },
            SCOPE,
        )

    with pytest.raises(OperatorContractError, match="finished_at"):
        ActionReceipt.from_wire(
            {
                "job_id": "job-2",
                "operation": "maintenance.reindex.apply",
                "scope": SCOPE.to_wire(),
                "plan_digest": DIGEST,
                "state": "failed",
                "started_at": "2026-10-02T12:00:02Z",
                "steps": [],
                "rollback_available": False,
            },
            SCOPE,
        )
