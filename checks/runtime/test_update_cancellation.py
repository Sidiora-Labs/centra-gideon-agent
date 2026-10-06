"""Update cancellation retires actual children before publishing stopped state."""

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.core.cancellation import run_with_timeout
from gideon.operations import self_update as update


def running(pid):
    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (ProcessLookupError, FileNotFoundError):
        return False


@pytest.mark.asyncio
async def test_cancel_stops_parent_and_descendant_before_any_test_cleanup(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    update.begin_update("pip", "1.0", "2.0")
    ready = asyncio.Event()
    children = []
    events = []

    async def work():
        child = await update.launch_update_process(
            sys.executable,
            "-c",
            "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        children.extend([child.pid, int(await child.stdout.readline())])
        ready.set()
        await run_with_timeout(child, 120)

    operation = update.UpdateOperation(work(), lambda *args: events.append(args))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        assert await operation.cancel() == "stopped"
        # Inspect before fallback cleanup: zombies are stopped processes.
        for _ in range(100):
            if not any(running(pid) for pid in children):
                break
            await asyncio.sleep(0.02)
        assert not any(running(pid) for pid in children)
        assert update.read_update_state()["phase"] == "cancelled"
        assert update.rollback_snapshot("pip")["version"] == "1.0"
        assert events[-1][0] == "cancelled"
        assert await operation.cancel() == "not_running"
    finally:
        for pid in children:
            if running(pid):
                os.kill(pid, signal.SIGKILL)
        await operation.cancel()


@pytest.mark.asyncio
async def test_cancel_restores_checkout_and_preserves_recovery_point(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Update Test")
    (repo / "file").write_text("before")
    git("add", ".")
    git("commit", "-m", "before")
    old = git("rev-parse", "HEAD")
    (repo / "file").write_text("after")
    git("commit", "-am", "after")
    new = git("rev-parse", "HEAD")
    update.begin_update("git", "1", "2", rollback_ref=old)
    ready = asyncio.Event()

    async def work():
        git("reset", "--hard", old)
        ready.set()
        await asyncio.Event().wait()

    operation = update.UpdateOperation(work(), lambda *_: None, str(repo))
    await ready.wait()
    assert await operation.cancel() == "stopped"
    assert git("rev-parse", "HEAD") == new
    assert git("branch", "--show-current") == "main"
    assert update.read_update_state()["rollback_ref"] == old


@pytest.mark.asyncio
async def test_cancel_refuses_after_install_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    operation = update.UpdateOperation(asyncio.sleep(0.01), lambda *_: None)
    operation.cancellable = False
    assert await operation.cancel() == "too_late"
    await operation.task


def test_cli_interrupt_retires_installer_and_descendant_before_cleanup(tmp_path):
    pids = tmp_path / "pids"
    installer = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"Path({str(pids)!r}).write_text(str(__import__('os').getpid())+' '+str(p.pid)); time.sleep(60)"
    )
    command = (
        "from gideon.operations.self_update import run_install_command; import sys; "
        f"run_install_command([sys.executable,'-c',{installer!r}])"
    )
    cli = subprocess.Popen(
        [sys.executable, "-c", command], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    children = []
    try:
        import time

        deadline = time.monotonic() + 5
        while not pids.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        children = [int(pid) for pid in pids.read_text().split()]
        cli.send_signal(signal.SIGINT)
        cli.communicate(timeout=5)
        stopped = not any(running(pid) for pid in children)
        assert stopped
        assert cli.returncode != 0
    finally:
        for pid in children:
            if running(pid):
                os.kill(pid, signal.SIGKILL)
        if cli.poll() is None:
            cli.kill()
            cli.communicate()


def test_metadata_delta_reports_real_distribution_change(tmp_path, monkeypatch):
    distribution = tmp_path / "cancellation_probe-1.0.dist-info"
    distribution.mkdir()
    metadata = distribution / "METADATA"
    metadata.write_text(
        "Metadata-Version: 2.1\nName: cancellation-probe\nVersion: 1.0\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    before = update.installed_metadata()
    metadata.write_text(
        "Metadata-Version: 2.1\nName: cancellation-probe\nVersion: 2.0\n"
    )
    assert "cancellation-probe" in update.installation_delta(before)


@pytest.mark.asyncio
async def test_failed_update_restores_actual_checkout_before_completion(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Update Test")
    (repo / "file").write_text("before")
    git("add", ".")
    git("commit", "-m", "before")
    old = git("rev-parse", "HEAD")
    (repo / "file").write_text("after")
    git("commit", "-am", "after")
    new = git("rev-parse", "HEAD")
    git("reset", "--hard", old)
    update.begin_update("git", "1", "2", rollback_ref=old)

    async def work():
        git("reset", "--hard", new)
        update.fail_update("installer failed")

    operation = update.UpdateOperation(work(), lambda *_: None, str(repo))
    await operation.task
    assert git("rev-parse", "HEAD") == old
    assert update.read_update_state()["phase"] == "failed"
    assert "Checkout restored" in update.read_update_state()["error"]
