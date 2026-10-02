from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "checks/hypermid/fixtures/sandbox-driver/Cargo.toml"
ADVERSARY = ROOT / "checks/hypermid/fixtures/sandbox-adversary/adversary.py"


def _run(scenario: str, state: Path) -> dict:
    environment = os.environ.copy()
    environment["CARGO_BUILD_JOBS"] = "2"
    environment["CARGO_TARGET_DIR"] = os.environ.get(
        "CARGO_TARGET_DIR", str(ROOT.parent / "build/target")
    )
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
    return json.loads(completed.stdout)


def test_production_sandbox_contains_adversarial_stdio_effects(tmp_path: Path) -> None:
    ADVERSARY.chmod(ADVERSARY.stat().st_mode | stat.S_IXUSR)

    traversal = _run("traversal", tmp_path / "traversal")
    assert traversal["committed"]["readable"] == []

    descriptors = _run("descriptors", tmp_path / "descriptors")
    assert descriptors["committed"]["inherited"] == []

    environment = _run("environment", tmp_path / "environment")
    child_environment = environment["committed"]["environment"]
    assert child_environment["SAFE_VALUE"] == "allowed"
    assert set(child_environment) <= {"SAFE_VALUE", "PWD", "LC_CTYPE"}
    assert not any(
        token in key.lower()
        for key in child_environment
        for token in ("key", "token", "secret", "password", "credential")
    )

    network = _run("network", tmp_path / "network")
    assert network["committed"]["connected"] is False
    assert network["committed"]["error"]

    oversized = _run("oversized", tmp_path / "oversized")
    assert "frame exceeds" in oversized["error"].lower()

    malformed = _run("malformed", tmp_path / "malformed")
    assert "malformed json" in malformed["error"].lower()

    unsolicited = _run("unsolicited", tmp_path / "unsolicited")
    assert "unsolicited or mismatched" in unsolicited["error"].lower()

    cancelled = _run("cancel_unknown", tmp_path / "cancelled")
    assert cancelled == {
        "unknown": True,
        "effect_marker": True,
        "descendant_pid": cancelled["descendant_pid"],
        "descendant_alive": False,
    }
    assert isinstance(cancelled["descendant_pid"], int)
