from __future__ import annotations

import hashlib

import pytest

from gideon.hypermid.operator_lifecycle import (
    HypermidOperatorLifecycle,
    LifecyclePlan,
    LifecycleReceipt,
)
from gideon.hypermid.models import Scope
from gideon.hypermid.operations import OperatorContractError


SCOPE = Scope("owner-1", "project-1", "workspace-1")
DIGEST = "a" * 64
EXCLUSIONS = [
    "credentials",
    "local_authentication_material",
    "vectors",
    "indexes",
    "leases",
    "jobs",
    "budget_reservations",
]


def _plan(operation: str, **extra: object) -> dict[str, object]:
    return {
        "plan_id": "plan-1",
        "operation": f"lifecycle.{operation}.apply",
        "scope": SCOPE.to_wire(),
        "created_at": "2026-10-02T12:00:00Z",
        "expires_at": "2026-10-02T12:10:00Z",
        "plan_digest": DIGEST,
        "destructive": operation in {"uninstall", "restore"},
        "restart_required": operation in {"install", "update", "uninstall", "restore"},
        "steps": [
            {
                "id": "verify",
                "title": "Verify inputs",
                "effect": "read",
                "state": "planned",
            }
        ],
        "blockers": [],
        "authority_digest": "b" * 64,
        "blocker_digest": "c" * 64,
        **extra,
    }


def test_lifecycle_plans_keep_data_choice_and_verify_export_artifact(tmp_path) -> None:
    retained = LifecyclePlan.from_wire(
        "uninstall",
        _plan(
            "uninstall",
            data_disposition="retain",
            inventory={"runtime": ["daemon"], "user_data": ["store"]},
        ),
        SCOPE,
    )
    assert retained.data_disposition == "retain"
    assert retained.inventory["user_data"] == ("store",)

    artifact = tmp_path / "project.hypermid.jsonl"
    artifact.write_bytes(b'{"schema_version":1}\n')
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    export = LifecyclePlan.from_wire(
        "export",
        _plan(
            "export",
            destination=str(artifact),
            estimated_items=1,
            estimated_bytes=artifact.stat().st_size,
            exclusions=EXCLUSIONS,
        ),
        SCOPE,
    )
    receipt = LifecycleReceipt.from_wire(
        {
            "job_id": "job-export",
            "operation": "lifecycle.export.apply",
            "scope": SCOPE.to_wire(),
            "plan_digest": export.plan_digest,
            "state": "committed",
            "started_at": "2026-10-02T12:01:00Z",
            "finished_at": "2026-10-02T12:01:01Z",
            "cursor": {"epoch": 3, "sequence": 11},
            "steps": [
                {
                    "id": "verify",
                    "title": "Verify inputs",
                    "effect": "read",
                    "state": "committed",
                }
            ],
            "rollback_available": False,
            "artifact_digest": digest,
            "artifact_bytes": artifact.stat().st_size,
            "artifact_path": str(artifact),
        },
        SCOPE,
    )
    HypermidOperatorLifecycle._verify_export(export, receipt)

    artifact.write_bytes(b"changed")
    with pytest.raises(OperatorContractError, match="verification failed"):
        HypermidOperatorLifecycle._verify_export(export, receipt)


def test_lifecycle_rejects_unsafe_or_incomplete_portability_plans() -> None:
    with pytest.raises(OperatorContractError, match="separate inventories"):
        LifecyclePlan.from_wire(
            "uninstall",
            _plan(
                "uninstall",
                data_disposition="retain",
                inventory={"runtime": ["daemon"]},
            ),
            SCOPE,
        )

    with pytest.raises(OperatorContractError, match="verified staged input"):
        LifecyclePlan.from_wire(
            "restore",
            _plan("restore", source_digest="d" * 64),
            SCOPE,
        )

    with pytest.raises(OperatorContractError, match="complete private/derivative"):
        LifecyclePlan.from_wire(
            "export",
            _plan("export", destination="/tmp/export.jsonl", exclusions=["vectors"]),
            SCOPE,
        )
