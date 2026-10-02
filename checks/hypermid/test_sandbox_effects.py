from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
import subprocess

from checks.hypermid.test_egress_secrets import network_gate_observations


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "checks/hypermid/fixtures/sandbox-driver/Cargo.toml"
ADVERSARY = ROOT / "checks/hypermid/fixtures/sandbox-adversary/adversary.py"


def _run(scenario: str, state: Path, canary: str) -> tuple[dict, int]:
    environment = os.environ.copy()
    environment["CARGO_BUILD_JOBS"] = "2"
    environment["CARGO_TARGET_DIR"] = os.environ.get(
        "CARGO_TARGET_DIR", str(ROOT.parent / "build/target")
    )
    environment["HYPERMID_SANDBOX_CANARY"] = canary
    completed = subprocess.run(
        [
            str(Path.home() / ".cargo/bin/cargo"),
            "run",
            "--quiet",
            "--manifest-path",
            str(DRIVER),
            "--",
            scenario,
            str(ADVERSARY),
            str(state),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=True,
    )
    occurrences = completed.stdout.count(canary) + completed.stderr.count(canary)
    return json.loads(completed.stdout), occurrences


def test_production_sandbox_contains_adversarial_stdio_effects(tmp_path: Path) -> None:
    ADVERSARY.chmod(ADVERSARY.stat().st_mode | stat.S_IXUSR)
    canary = f"hypermid-sandbox-canary-{secrets.token_hex(24)}"
    canary_occurrences = 0

    traversal, observed = _run("traversal", tmp_path / "traversal", canary)
    canary_occurrences += observed
    assert traversal["committed"]["readable"] == []

    descriptors, observed = _run("descriptors", tmp_path / "descriptors", canary)
    canary_occurrences += observed
    assert descriptors["committed"]["inherited"] == []

    environment, observed = _run("environment", tmp_path / "environment", canary)
    canary_occurrences += observed
    child_environment = environment["committed"]["environment"]
    assert child_environment["SAFE_VALUE"] == "allowed"
    assert set(child_environment) <= {"SAFE_VALUE", "PWD", "LC_CTYPE"}
    assert not any(
        token in key.lower()
        for key in child_environment
        for token in ("key", "token", "secret", "password", "credential")
    )

    network, observed = _run("network", tmp_path / "network", canary)
    canary_occurrences += observed
    assert network["committed"]["connected"] is False
    assert network["committed"]["error"]

    oversized, observed = _run("oversized", tmp_path / "oversized", canary)
    canary_occurrences += observed
    assert "frame exceeds" in oversized["error"].lower()

    malformed, observed = _run("malformed", tmp_path / "malformed", canary)
    canary_occurrences += observed
    assert "malformed json" in malformed["error"].lower()

    unsolicited, observed = _run("unsolicited", tmp_path / "unsolicited", canary)
    canary_occurrences += observed
    assert "unsolicited or mismatched" in unsolicited["error"].lower()

    cancelled, observed = _run("cancel_unknown", tmp_path / "cancelled", canary)
    canary_occurrences += observed
    assert cancelled == {
        "unknown": True,
        "effect_marker": True,
        "descendant_pid": cancelled["descendant_pid"],
        "descendant_alive": False,
    }
    assert isinstance(cancelled["descendant_pid"], int)

    unauthorized_connections = int(network["committed"]["connected"])
    unauthorized_file_accesses = len(traversal["committed"]["readable"])
    surviving_children = int(cancelled["descendant_alive"])
    network_gate_observations().record_sandbox(
        unauthorized_connections=unauthorized_connections,
        unauthorized_file_accesses=unauthorized_file_accesses,
        surviving_children=surviving_children,
        secret_canary_occurrences=canary_occurrences,
        matrix={
            "filesystem": {
                "unauthorized_file_accesses": unauthorized_file_accesses,
            },
            "descriptors": {
                "inherited_descriptors": len(
                    descriptors["committed"]["inherited"]
                ),
            },
            "environment": {
                "forwarded_keys": sorted(child_environment),
            },
            "network": {
                "connected": network["committed"]["connected"],
                "error_observed": bool(network["committed"]["error"]),
            },
            "protocol": {
                "oversized_rejected": "frame exceeds" in oversized["error"].lower(),
                "malformed_rejected": "malformed json" in malformed["error"].lower(),
                "unsolicited_rejected": (
                    "unsolicited or mismatched" in unsolicited["error"].lower()
                ),
            },
            "cancellation": {
                "unknown_outcome": cancelled["unknown"],
                "effect_marker_observed": cancelled["effect_marker"],
                "surviving_children": surviving_children,
            },
            "secret_canary_occurrences": canary_occurrences,
        },
    )
