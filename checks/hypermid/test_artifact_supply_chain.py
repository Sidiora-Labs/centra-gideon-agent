from __future__ import annotations

import copy

import pytest

from gideon.hypermid.artifacts import ArtifactApproval, ArtifactApprovalError


def manifest(*capabilities: str) -> dict[str, object]:
    return {
        "artifact_id": "hypermid-tool",
        "version": "2.0.0",
        "archive_sha256": "a" * 64,
        "publisher_id": "hypermid-release",
        "source_uri": "https://downloads.example.invalid/hypermid-tool.tar.gz",
        "capabilities": list(capabilities),
    }


def test_capability_expansion_requires_fresh_exact_manifest_approval() -> None:
    candidate = manifest("filesystem_read", "network")
    approval = ArtifactApproval.issue(
        candidate,
        approved_capabilities={"network"},
        approved_by="owner-1",
        approved_at_ms=100,
        ttl_ms=50,
    )
    assert approval.authorize_update(
        candidate, {"filesystem_read"}, now_ms=120
    ) == frozenset({"network"})

    tampered = copy.deepcopy(candidate)
    tampered["source_uri"] = "https://other.example.invalid/tool.tar.gz"
    with pytest.raises(ArtifactApprovalError) as changed:
        approval.authorize_update(tampered, {"filesystem_read"}, now_ms=120)
    assert changed.value.code == "FRESH_APPROVAL_REQUIRED"

    with pytest.raises(ArtifactApprovalError) as expired:
        approval.authorize_update(candidate, {"filesystem_read"}, now_ms=150)
    assert expired.value.code == "FRESH_APPROVAL_REQUIRED"

    assert approval.authorize_update(
        manifest("filesystem_read"), {"filesystem_read"}, now_ms=1_000
    ) == frozenset()

