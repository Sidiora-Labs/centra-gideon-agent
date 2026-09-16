"""Persistent claim publication and scheduling boundary contracts."""

import json
import os
import subprocess
import sys
from pathlib import Path

from gideon.automation.triggers import claims, scheduling


def test_concurrent_publishers_leave_a_complete_claim(tmp_path):
    script = """
import sys
from gideon.automation.triggers.claims import write_claim
from gideon.automation.triggers.scheduling import Claim
for sequence in range(20):
    write_claim(Claim('clock:shared', sys.argv[2], 1000 + sequence, 3600), base_dir=sys.argv[1])
"""
    environment = dict(
        os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "runtime")
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), owner],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for owner in ("worker-a", "worker-b", "worker-c")
    ]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stdout + stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    active = claims.read_claim("clock:shared", now=1020, base_dir=tmp_path)
    assert active is not None
    assert active.holder in {"worker-a", "worker-b", "worker-c"}
    assert active.claimed_at == 1019
    assert claims.running_ids(now=1020, base_dir=tmp_path) == ["clock:shared"]
    assert not list(claims._claims_dir(tmp_path).glob("*.tmp"))


def test_claim_expiry_retains_record_duration_and_decision_floor(tmp_path):
    claim = scheduling.Claim("short", "worker", 1000, 0.5)
    claims.write_claim(claim, base_dir=tmp_path)
    assert claims.read_claim("short", now=1000.4, base_dir=tmp_path) == claim
    assert claims.read_claim("short", now=1000.5, base_dir=tmp_path) is None
    assert not claim.expired(1000.5)
    assert claim.expired(1001)
    assert claim.to_dict()["expires_at"] == 1000.5
    assert (
        json.loads(claims._claim_path("short", tmp_path).read_text())[
            "max_duration_secs"
        ]
        == 0.5
    )


def test_admission_precedence_and_future_anchor_are_preserved():
    assert scheduling.is_due(
        next_fire_at=0, now=1000, fires_automatically=False, expires_at=1
    ) == (False, "disabled")
    assert scheduling.is_due(
        next_fire_at=0, now=1000, fires_automatically=True, expires_at=1
    ) == (False, "expired")
    assert (
        scheduling.recompute_from_completion(
            interval_secs=60, created_at=1060, completed_at=999
        )
        == 1000
    )
    assert scheduling.revalidate(
        still_enabled=False, next_fire_at_at_arm=1000, next_fire_at_now=2000
    ) == (False, "disabled while the timer slept")
