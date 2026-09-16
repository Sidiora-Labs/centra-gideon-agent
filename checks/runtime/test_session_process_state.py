"""PID journals and signaling tested with isolated files and owned children."""

import asyncio
import fcntl
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from gideon.engine import session_pid as tracking


@pytest.fixture(autouse=True)
def process_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


@pytest.fixture
def owned_child():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "claude-owned-pid-check"],
        start_new_session=True,
    )
    try:
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_periodic_preview_protects_active_child_then_confirmed_cleanup(owned_child):
    tracking._track_session_pid(owned_child.pid)
    assert tracking._is_managed_agent_process(owned_child.pid)
    assert tracking._periodic_pid_sweep(os.getpid(), {owned_child.pid}) == (set(), [])
    retired, candidates = tracking._periodic_pid_sweep(os.getpid(), set())
    assert retired == set() and candidates == [owned_child.pid]
    assert owned_child.poll() is None
    assert tracking._kill_confirmed_and_writeback(os.getpid(), candidates, retired) == 1
    assert owned_child.wait(timeout=5) == -signal.SIGKILL
    assert tracking._session_pid_file_path().read_text() == ""


def test_ancestry_mismatch_prunes_claim_without_signaling_live_child(owned_child):
    departed = subprocess.Popen([sys.executable, "-c", "pass"])
    departed.wait(timeout=5)
    tracking._track_child_pids({owned_child.pid: None}, parent_pid=departed.pid)
    assert tracking._cleanup_orphaned_mcp_servers() == 0
    assert owned_child.poll() is None
    assert tracking._pid_file_path().read_text() == ""
    tracking._track_child_pids({owned_child.pid: None}, parent_pid=os.getpid())
    assert tracking._cleanup_orphaned_mcp_servers() == 0
    assert f"{owned_child.pid}:{os.getpid()}" in tracking._pid_file_path().read_text()


def test_live_gateway_preserves_its_child_during_startup(owned_child):
    tracking._track_session_pid(owned_child.pid)
    tracking.cleanup_orphaned_sessions()
    assert owned_child.poll() is None
    assert (
        tracking._session_pid_file_path().read_text()
        == f"{os.getpid()}:{owned_child.pid}\n"
    )


def test_tracking_serializes_concurrent_writers_and_nonblocking_sweep(owned_child):
    with ThreadPoolExecutor(max_workers=6) as workers:
        list(workers.map(tracking._track_session_pid, [owned_child.pid] * 24))
    path = tracking._session_pid_file_path()
    assert path.read_text() == f"{os.getpid()}:{owned_child.pid}\n"
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            assert tracking._periodic_pid_sweep(os.getpid(), set()) == (set(), [])
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    assert tracking._periodic_pid_sweep(os.getpid(), set()) == (
        set(),
        [owned_child.pid],
    )
    assert owned_child.poll() is None


def test_journal_home_switch_and_entry_formats(owned_child, tmp_path, monkeypatch):
    tracking._track_pid(owned_child.pid)
    tracking._track_pid(owned_child.pid)
    tracking._track_child_pids({owned_child.pid: None}, parent_pid=os.getpid())
    first = tracking._pid_file_path()
    tracking._untrack_child_pids({owned_child.pid: None})
    assert first.read_text().splitlines() == [str(owned_child.pid)] * 2
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "another-home"))
    tracking._track_pid(owned_child.pid)
    second = tracking._pid_file_path()
    assert second != first
    tracking._untrack_pid(owned_child.pid)
    assert second.read_text() == ""
    assert first.read_text().splitlines() == [str(owned_child.pid)] * 2


def test_startup_removes_only_stale_notes_and_empty_workspaces(
    process_home, owned_child
):
    tracking._pid_file_path().parent.mkdir(parents=True, exist_ok=True)
    departed = subprocess.Popen([sys.executable, "-c", "pass"])
    departed.wait(timeout=5)
    stale = process_home / f"session_pid_{departed.pid}.txt"
    alive = process_home / f"session_pid_{owned_child.pid}.txt"
    malformed = process_home / "session_pid_invalid.txt"
    for note in (stale, alive, malformed):
        note.write_text("session")
    empty = process_home / "sessions" / "empty"
    full = process_home / "sessions" / "full"
    empty.mkdir(parents=True)
    full.mkdir()
    (full / "result.md").write_text("retain")
    tracking.cleanup_orphaned_sessions()
    assert not stale.exists() and not malformed.exists()
    assert alive.exists() and owned_child.poll() is None
    assert not empty.exists() and (full / "result.md").read_text() == "retain"


@pytest.mark.asyncio
async def test_actual_provider_process_collection_and_group_shutdown(tmp_path):
    from gideon.engine.session import _Session
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    provider = AcpAgentProvider(
        command=[sys.executable, "-c", code, "claude-owned-provider-check"],
        cwd=tmp_path,
        sandbox="none",
        sandbox_mode="off",
    )
    transport = provider.client._transport
    try:
        await transport.spawn()
        process = transport.process
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
        assert os.getpgid(process.pid) == process.pid
        assert tracking._collect_active_pids({"owned": _Session(provider)}) == (
            {process.pid},
            True,
        )
        tracking._sync_kill_provider(provider)
        assert await asyncio.wait_for(process.wait(), 5) == -signal.SIGKILL
    finally:
        await transport.kill(force=True)
        transport.teardown()
    assert tracking._session_pid_file_path().read_text() == ""
