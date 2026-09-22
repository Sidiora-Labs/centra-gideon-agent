"""Tests for AcpProcess (acp/transport.py) — the shared ACP subprocess + stdio
transport. Driven against a fake process (no real spawn) except where a helper is
pure. Complements test_acp_client.py, which exercises the transport through the
client's delegating methods."""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gideon.integrations.acp.errors import AcpError, AcpProcessDied
from gideon.integrations.acp.transport import AcpProcess


def _mk(**kw) -> AcpProcess:
    return AcpProcess(
        command=kw.pop("command", ["/bin/echo"]),
        work_dir=kw.pop("work_dir", "/tmp"),
        **kw,
    )


async def _spawned_env(transport: AcpProcess) -> dict:
    """Spawn *transport* against a mocked subprocess and return the env dict it passed
    to ``create_subprocess_exec`` (the env-building the transport owns)."""
    with (
        patch(
            "gideon.integrations.sandbox_providers.none.wrap_argv",
            return_value=(["/bin/echo"], None),
        ),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        patch("gideon.engine.session._track_pid"),
        patch("gideon.engine.session._track_session_pid"),
    ):
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = None
        proc.stderr = MagicMock()
        proc.stderr.readline = AsyncMock(return_value=b"")
        mock_exec.return_value = proc
        await transport.spawn()
        call = mock_exec.call_args
        return call.kwargs.get("env") or call[1].get("env")


@pytest.mark.asyncio
async def test_spawn_env_has_session_key_and_channel(tmp_path):
    t = _mk(work_dir=tmp_path, session_key="test-key", channel_id="C0ABC123")
    env = await _spawned_env(t)
    assert env is not None
    assert env["GIDEON_SESSION_KEY"] == "test-key"
    assert env["GIDEON_CHANNEL_ID"] == "C0ABC123"


@pytest.mark.asyncio
async def test_spawn_env_omits_channel_when_absent(tmp_path):
    t = _mk(work_dir=tmp_path, session_key="k", channel_id=None)
    env = await _spawned_env(t)
    assert env is not None
    assert env["GIDEON_SESSION_KEY"] == "k"
    assert "GIDEON_CHANNEL_ID" not in env


@pytest.mark.asyncio
async def test_spawn_env_omits_session_key_when_absent(tmp_path):
    import os

    clean = {k: v for k, v in os.environ.items() if k != "GIDEON_SESSION_KEY"}
    with patch.dict(os.environ, clean, clear=True):
        t = _mk(work_dir=tmp_path, session_key=None, channel_id="C0ABC123")
        env = await _spawned_env(t)
    assert env is not None
    assert env["GIDEON_CHANNEL_ID"] == "C0ABC123"
    assert "GIDEON_SESSION_KEY" not in env


@pytest.mark.asyncio
async def test_spawn_routes_through_sandbox_provider_handle(tmp_path):
    """EI-1: spawn resolves the named sandbox provider and launches via handle.exec — the
    inline wrap_argv/create_subprocess_limited pair is gone. A fake provider proves the seam.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_proc = MagicMock()
    fake_proc.pid = 4242
    fake_proc.returncode = None
    fake_proc.stderr = MagicMock()
    fake_proc.stderr.readline = AsyncMock(return_value=b"")

    handle = MagicMock()
    handle.argv = ["/bin/echo", "wrapped"]
    handle.exec = AsyncMock(return_value=fake_proc)
    provider = MagicMock()
    provider.wrap = MagicMock(return_value=handle)

    t = _mk(work_dir=tmp_path, sandbox="container-tier")
    with (
        patch(
            "gideon.integrations.sandbox_providers.resolve_provider",
            return_value=provider,
        ) as resolve,
        patch("gideon.engine.session._track_pid"),
        patch("gideon.engine.session._track_session_pid"),
    ):
        await t.spawn()

    resolve.assert_called_once_with("container-tier")
    spec = provider.wrap.call_args.args[0]
    assert spec.mode == "auto"
    assert spec.profile == "session_host"
    handle.exec.assert_awaited_once()
    assert t.pid == 4242


def test_liveness_before_spawn():
    t = _mk()
    assert t.is_alive() is False
    assert t.exit_code is None
    assert t.is_responsive() is False


def test_touch_refreshes_activity():
    t = _mk()
    before = t.last_activity
    t.touch()
    assert t.last_activity >= before


@pytest.mark.asyncio
async def test_write_requires_running_process():
    t = _mk()
    with pytest.raises(AcpError, match="not running"):
        await t.write("{}\n")


@pytest.mark.asyncio
async def test_write_broken_pipe_raises_process_died():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock(side_effect=BrokenPipeError("gone"))
    proc.stdin.drain = AsyncMock()
    t._process = proc
    with pytest.raises(AcpProcessDied):
        await t.write("{}\n")


@pytest.mark.asyncio
async def test_write_stamps_activity_and_frames():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    written: list[bytes] = []
    proc.stdin = MagicMock()
    proc.stdin.write = lambda b: written.append(b)
    proc.stdin.drain = AsyncMock()
    t._process = proc
    before = t.last_activity
    await t.write('{"x":1}\n')
    assert written == [b'{"x":1}\n']
    assert t.last_activity >= before


@pytest.mark.asyncio
async def test_readline_requires_running_process():
    t = _mk()
    with pytest.raises(AcpError, match="not running"):
        await t.readline()


@pytest.mark.asyncio
async def test_readline_returns_line():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=b'{"ok":true}\n')
    t._process = proc
    assert await t.readline() == b'{"ok":true}\n'


def test_stderr_tail_redacts():
    t = _mk()
    t._stderr_lines = deque(["boom key=AKIAIOSFODNN7EXAMPLE"])
    tail = t.stderr_tail()
    assert "AKIAIOSFODNN7EXAMPLE" not in tail
    assert "boom" in tail


def test_stderr_tail_empty():
    assert _mk().stderr_tail() == ""


@pytest.mark.asyncio
async def test_kill_noop_when_no_process():
    await _mk().kill()


@pytest.mark.asyncio
async def test_kill_sigterm_then_sweeps_escaped():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = proc.stdout = proc.stderr = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    t._process = proc
    t._pid = 4321
    t._child_pids = {}
    with (
        patch("os.killpg") as killpg,
        patch("os.getpgid", return_value=4321),
        patch("gideon.integrations.acp.transport._kill_escaped_children") as sweep,
    ):
        await t.kill()
    killpg.assert_called_once()
    sweep.assert_called_once()


def test_teardown_clears_and_unlinks(tmp_path):
    from gideon.integrations.sandbox_providers.none import _NoneHandle

    t = _mk()
    proc = MagicMock()
    proc.stdin = proc.stdout = proc.stderr = MagicMock()
    t._process = proc
    sb = tmp_path / "sandbox.sb"
    sb.write_text("profile")
    t._sandbox_handle = _NoneHandle(["/bin/echo"], "none", None, str(sb))
    t._child_pids = {123: None}
    with (
        patch("gideon.engine.session._untrack_child_pids"),
        patch("gideon.engine.session._untrack_pid"),
        patch("gideon.engine.session._untrack_session_pid"),
    ):
        t.teardown()
    assert t._process is None
    assert t.pid is None
    assert t._child_pids == {}
    assert not sb.exists()


def test_get_child_pids_none_and_missing():
    from gideon.integrations.acp.transport import _get_child_pids

    assert _get_child_pids(None) == []
    assert _get_child_pids(999999) == []


def test_recursive_children(monkeypatch):
    import gideon.integrations.acp.transport as tmod
    from gideon.integrations.acp.transport import _get_child_pids

    tree = {1000: [2000, 3000], 2000: [4000], 3000: [5000]}
    monkeypatch.setattr(tmod, "_direct_children", lambda pid: tree.get(pid, []))
    assert _get_child_pids(1000) == [2000, 4000, 3000, 5000]


@pytest.mark.asyncio
async def test_real_process_echo_shutdown_and_pid_release(tmp_path, monkeypatch):
    import asyncio
    import sys

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    transport = AcpProcess(
        command=[
            sys.executable,
            "-u",
            "-c",
            "import sys\nfor line in sys.stdin: print(line, end='', flush=True)",
        ],
        work_dir=tmp_path / "work",
        sandbox_mode="off",
    )
    try:
        await transport.spawn()
        pid = transport.pid
        assert pid and transport.is_alive()
        await transport.write('{"id":1}\n')
        assert await asyncio.wait_for(transport.readline(), 2) == b'{"id":1}\n'
        await transport.kill()
        assert not transport.is_alive()
    finally:
        await transport.kill(force=True)
        transport.teardown()
    assert transport.pid is None
    for path in (tmp_path / "home").glob("*pids.txt"):
        assert all(
            str(pid) not in entry.split(":") for entry in path.read_text().splitlines()
        )


@pytest.mark.asyncio
async def test_cancelled_real_spawn_releases_process_and_handle(tmp_path, monkeypatch):
    import asyncio
    import sys

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    transport = AcpProcess(
        command=[sys.executable, "-u", "-c", "import time; time.sleep(30)"],
        work_dir=tmp_path / "work",
        sandbox_mode="off",
    )
    launch = asyncio.create_task(transport.spawn())
    process = None
    try:
        async with asyncio.timeout(5):
            while transport.process is None:
                await asyncio.sleep(0.001)
        process = transport.process
        launch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await launch
        assert process.returncode is not None
        assert transport.process is None
        assert transport._sandbox_handle is None
    finally:
        if not launch.done():
            launch.cancel()
            await asyncio.gather(launch, return_exceptions=True)
        await transport.kill(force=True)
        transport.teardown()
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


def test_error_payload_contracts():
    from gideon.integrations.acp.errors import (
        AcpCommandFailedAfterOutput,
        AcpCommandsUnsupported,
        AcpMethodNotFound,
        AcpPermissionNeeded,
        AcpTimeoutError,
        AcpWorkspaceUnresolved,
    )

    detail = {"message": "absent"}
    missing = AcpMethodNotFound("session/example", detail)
    assert missing.code == -32601 and missing.error is detail
    assert missing.method == "session/example"
    assert str(missing) == "Method not found: session/example"
    assert str(AcpCommandsUnsupported("/next")).endswith("(/next)")
    assert AcpCommandFailedAfterOutput("/next").command == "/next"
    assert AcpTimeoutError("partial").partial_output == "partial"
    permission = AcpPermissionNeeded("approve?", "partial")
    assert permission.prompt == "approve?" and permission.response_so_far == "partial"
    assert isinstance(AcpWorkspaceUnresolved("missing"), AcpError)
