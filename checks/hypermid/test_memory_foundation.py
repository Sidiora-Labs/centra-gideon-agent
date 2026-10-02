from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_memory_foundation_real_sqlite_transactions() -> None:
    environment = os.environ.copy()
    environment.setdefault("CARGO_BUILD_JOBS", "2")
    completed = subprocess.run(
        [
            "cargo",
            "test",
            "-p",
            "hypermid-memory",
            "foundation_",
            "--",
            "--nocapture",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
