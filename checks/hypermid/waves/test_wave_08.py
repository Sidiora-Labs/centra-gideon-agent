"""Complete OSS distribution through the real packaged and native paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path


HYPERMID_CHECKS = Path(__file__).resolve().parents[1]
if os.fspath(HYPERMID_CHECKS) not in sys.path:
    sys.path.insert(0, os.fspath(HYPERMID_CHECKS))

from verify_wave_08 import run_release_acceptance


def test_complete_oss_distribution_binds_real_journeys_to_one_source(tmp_path: Path) -> None:
    source_digest = os.environ.get("HYPERMID_SOURCE_DIGEST")
    evidence_dir = Path(
        os.environ.get(
            "HYPERMID_SECURITY_EVIDENCE_DIR",
            Path(__file__).resolve().parents[3] / ".artifacts/hypermid/security",
        )
    )
    result = run_release_acceptance(
        artifact_root=tmp_path / "release",
        evidence_dir=evidence_dir,
        source_digest=source_digest,
        allow_live_provider=os.environ.get("HYPERMID_ALLOW_LIVE_PROVIDER") == "1",
    )
    report = result["report"]
    assert report["status"] == "passed"
    assert report["deployment_release"]["claimed"] is False
    assert len(report["source"]["revision"]) in {40, 64}
    assert len(report["source"]["digest"]) == 64
    assert len(report["source"]["closure_digest"]) == 64
    assert report["source"]["closure_digest"] != report["source"]["digest"]
    assert all(journey["status"] == "passed" for journey in report["live_journeys"])
    assert result["evidence"]["result"] == "passed"
    artifact_ids = {
        artifact["artifact_id"] for artifact in result["evidence"]["artifacts"]
    }
    assert {
        "published-source-closure",
        "packaging-source-closure",
        "wheel-manifest",
        "image-manifest",
        "packaged-wheel",
        "packaged-daemon",
    }.issubset(artifact_ids)
