"""Exercise runtime composition over real sockets and isolated on-disk state."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.engine.lifecycle import StartupStage, advance, cancel_tasks


@pytest.mark.asyncio
async def test_optional_startup_failure_preserves_later_persistence(tmp_path):
    first = tmp_path / "first"
    last = tmp_path / "last"
    missing = tmp_path / "absent" / "optional"
    finished = await advance(
        (
            StartupStage("first", lambda: asyncio.to_thread(first.write_text, "ready")),
            StartupStage(
                "optional", lambda: missing.write_text("unavailable"), optional=True
            ),
            StartupStage("last", lambda: last.write_text("ready")),
        )
    )
    assert finished == ("first", "last")
    assert first.read_text() == last.read_text() == "ready"


@pytest.mark.asyncio
async def test_required_startup_failure_prevents_later_stage(tmp_path):
    later = tmp_path / "later"
    with pytest.raises(FileNotFoundError):
        await advance(
            (
                StartupStage("required", lambda: (tmp_path / "missing").read_text()),
                StartupStage("later", lambda: later.touch()),
            )
        )
    assert not later.exists()


@pytest.mark.asyncio
async def test_shutdown_waits_for_task_resource_release(tmp_path):
    entered = asyncio.Event()
    marker = tmp_path / "lease"

    async def worker():
        marker.touch()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            marker.unlink()

    task = asyncio.create_task(worker())
    await entered.wait()
    await cancel_tasks((task, task, asyncio.current_task(), None))
    assert task.cancelled()
    assert not marker.exists()


@pytest.mark.parametrize("surface", ["api", "console"])
def test_real_http_binding_authentication_and_retirement(tmp_path, surface):
    script = Path(__file__).parents[1] / "harness" / "runtime_lifecycle_probe.py"
    environment = {
        **os.environ,
        "GIDEON_HOME": str(tmp_path / "home"),
        "GIDEON_BIND_HOST": "127.0.0.1",
        "GIDEON_AUTH_MODE": "local_token",
        "GIDEON_APP_CATALOG_URLS": "[]",
        "GIDEON_APP_REGISTRY_URL": "",
    }
    environment.pop("GIDEON_PROJECT_DIR", None)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "runtime")
    result = subprocess.run(
        [sys.executable, str(script), surface],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{surface}: live HTTP and cleanup passed" in result.stdout
