from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from checks.hypermid.evidence import (
    Environment,
    Measurement,
    produce_evidence,
    source_digest,
)
from checks.hypermid.verify_security_acceptance import validate_evidence


def test_real_failed_gate_emits_valid_evidence_and_cannot_certify_release(
    tmp_path: Path,
) -> None:
    configured = os.environ.get("HYPERMID_FAILED_GATE_LOG")
    if not configured:
        pytest.skip("requires a real failed Hypermid gate log")
    failed_log = Path(configured)
    assert failed_log.is_file() and not failed_log.is_symlink()
    raw_log = failed_log.read_text(encoding="utf-8")
    assert "FAILED" in raw_log
    duration_match = re.search(r"in ([0-9]+(?:\.[0-9]+)?)s", raw_log)
    assert duration_match is not None
    ended_at = datetime.fromtimestamp(failed_log.stat().st_mtime, timezone.utc)
    started_at = ended_at - timedelta(seconds=float(duration_match.group(1)))

    repo = Path(__file__).resolve().parents[2]
    revision = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    snapshot = source_digest(revision)
    evidence_dir = tmp_path / "evidence"
    record_path = produce_evidence(
        evidence_dir,
        gate="long_history",
        capability_id="hypermid-long-history",
        scope={"owner_id": "oss-release", "project_id": "hypermid"},
        trace={"trace_id": "sec08-failed-gate", "request_id": "qualification-1"},
        command=(
            "PYTHONPATH=runtime python -m pytest -q "
            "checks/hypermid/test_long_history.py checks/hypermid/test_cache_stability.py"
        ),
        exit_code=1,
        started_at=started_at,
        ended_at=ended_at,
        environment=Environment(
            platform=platform.platform(),
            architecture=platform.machine(),
            daemon_version="sha256:2086406f8c329f1d3922dbe37be3e181b44cbb0f00cb3ff91766616e38561115",
            client_version="gideon-python-source",
        ),
        measurements=[
            Measurement("daemon-restart-available", False, "eq", True, "boolean")
        ],
        log_path=failed_log,
        source_revision=revision,
        source_snapshot_digest=snapshot,
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["result"] == "failed"
    assert record["execution"]["product_acceptance_claim"] is False
    assert record["measurements"][0] == {
        "name": "command-exit-code",
        "observed": 1,
        "comparator": "eq",
        "threshold": 0,
        "passed": False,
    }
    assert record["errors"][0]["code"] == "COMMAND_FAILED"
    assert not Path(record["artifacts"][0]["path"]).is_absolute()
    public_log = record_path.parent / record["artifacts"][0]["path"]
    assert public_log.is_file()
    public_text = public_log.read_text(encoding="utf-8")
    assert "/root/" not in public_text
    assert "/tmp/" not in public_text

    report, errors = validate_evidence(
        repo=repo,
        evidence_dir=evidence_dir,
        expected_source_digest=snapshot,
        allow_live_provider=False,
    )
    assert "long_history" in report["validated_gates"]
    assert report["status"] == "failed"
    assert any("product_acceptance_claim is false" in error for error in errors)
    assert any("result is failed, not passed" in error for error in errors)
