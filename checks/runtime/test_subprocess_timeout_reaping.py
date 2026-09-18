"""A timed-out command leaves no descendant and no open pipe behind (req.3).

Every asynchronous process deadline in the runtime is owned by
:mod:`gideon.core.cancellation` — :func:`run_with_timeout` / :func:`wait_with_timeout`
around the await, :func:`terminate_and_reap` / :func:`kill_timed_out` behind them. The
owner does four things a hand-rolled ``proc.kill()`` forgets one at a time: it signals
the child's process GROUP when the child leads one, it escalates SIGTERM → SIGKILL after
a short grace, it reaps inside a bound, and it closes our ends of the child's pipes.

These tests drive the four real call sites req.3 names — installer, build, provider,
workflow — against a real forking shell (``sleep 30 & … ; sleep 30``: the ``&`` child is
a genuine grandchild in the same group, and it inherits the pipes). Each waits for the
grandchild to actually exist BEFORE the deadline lands, so a green run can never come
from a shell that never forked, and then asserts the three observable halves: the child
is reaped, the grandchild is gone, the pipes are closed.

:func:`test_the_unowned_shape_leaks_all_three` is the vacuity proof — the shape the
owner replaced, same command, leaks all three — so the bound and the assertions above
discriminate rather than pass trivially.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from pathlib import Path

import pytest

from gideon.core import cancellation

TIMEOUT_SECS = 2.0
BOUND_SECS = 10.0
_FORKING = "sleep 30 & printf %s $! > {pidfile}; sleep 30"


def _forking_command(pidfile: Path) -> str:
    """A shell that forks a real grandchild, records its pid, then blocks."""
    return _FORKING.format(pidfile=str(pidfile))


def _alive(pid: int) -> bool:
    """True while *pid* is a live, non-zombie process."""
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            state = stat.read_bytes().rsplit(b")", 1)[1].split()[0]
        except (OSError, IndexError):
            return False
        return state not in (b"Z", b"X")
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _gone(pid: int, *, deadline: float = 5.0) -> bool:
    """Poll until *pid* is gone — signal delivery and reparenting are not instant."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


class _Spy:
    """Wraps a real spawn function and keeps the process, so a test can inspect it after."""

    def __init__(self, real):
        self._real = real
        self.proc = None

    async def __call__(self, *args, **kwargs):
        self.proc = await self._real(*args, **kwargs)
        return self.proc


def _spy_on_limited(monkeypatch) -> _Spy:
    import gideon.security.sandbox as sandbox

    spy = _Spy(sandbox.create_subprocess_limited)
    monkeypatch.setattr(sandbox, "create_subprocess_limited", spy)
    return spy


def _spy_on_exec(monkeypatch) -> _Spy:
    spy = _Spy(asyncio.create_subprocess_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    return spy


@pytest.fixture(scope="module", autouse=True)
def _warm_the_ceiling_shim():
    """Pay the shim's cold-import cost once, outside anyone's deadline.

    ``create_subprocess_limited`` prepends ``python -m gideon.engine._spawn_exec_shim``,
    and the suite redirects ``__pycache__`` to a scratch dir — so the FIRST such spawn in
    a session recompiles gideon and takes seconds. Without this warm-up the shell would
    still be importing when a 2s deadline landed, and the tests below would be asserting
    about a grandchild that never got forked.
    """

    async def warm() -> None:
        import gideon.security.sandbox as sandbox

        proc = await sandbox.create_subprocess_limited(
            "/bin/sh",
            "-c",
            "exit 0",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await cancellation.wait_with_timeout(proc, 60)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(warm())
    finally:
        loop.close()


@pytest.fixture
def pidfile(tmp_path):
    return tmp_path / "grandchild.pid"


async def _await_grandchild(pidfile: Path, running: asyncio.Task) -> int:
    """The grandchild's pid, once the shell has forked it and the call is still in flight."""
    end = time.monotonic() + BOUND_SECS
    while time.monotonic() < end:
        raw = pidfile.read_text().strip() if pidfile.exists() else ""
        if raw.isdigit():
            return int(raw)
        if running.done():
            break
        await asyncio.sleep(0.02)
    with contextlib.suppress(Exception):
        await running
    raise AssertionError(
        f"{pidfile} never received a grandchild pid — the shell was retired before it "
        "forked, so this test would prove nothing about descendants"
    )


async def _drive(call, pidfile: Path):
    """Run a real call site to its deadline; return ``(result, grandchild_pid, elapsed)``."""
    started = time.monotonic()
    running = asyncio.ensure_future(call())
    grandchild = await _await_grandchild(pidfile, running)
    result = await running
    return result, grandchild, time.monotonic() - started


def _assert_retired(spy: _Spy, grandchild: int, elapsed: float) -> None:
    """The three halves of req.3 for one call site, plus the bound that gives them teeth."""
    proc = spy.proc
    assert proc is not None, "the spy never ran — the call site did not spawn anything"
    assert elapsed < BOUND_SECS, (
        f"a {TIMEOUT_SECS}s deadline took {elapsed:.2f}s to return — the reap waited on "
        "the grandchild's inherited pipe instead of the child's exit"
    )
    assert proc.returncode is not None, (
        f"child {proc.pid} was not reaped: returncode is still None, so it is a zombie "
        "still holding its end of the pipe"
    )
    assert _gone(proc.pid), f"child {proc.pid} outlived its deadline"
    assert _gone(grandchild), (
        f"grandchild {grandchild} survived the timeout — the signal went to the child's "
        "pid instead of the process group it leads"
    )
    assert cancellation.open_pipe_count(proc) == 0, (
        "the child's pipe transports are still open after the timeout; a descendant that "
        "inherited them keeps them alive and Process.wait() never resolves"
    )


async def test_installer_step_timeout_takes_the_whole_tree(
    tmp_path, monkeypatch, pidfile
):
    """The workflow installer step — ``provisioning.run_step``."""
    from gideon.automation.workflows import provisioning

    spy = _spy_on_limited(monkeypatch)
    (ok, detail), grandchild, elapsed = await _drive(
        lambda: provisioning.run_step(
            f"/bin/sh -c {_forking_command(pidfile)!r}", tmp_path, timeout=TIMEOUT_SECS
        ),
        pidfile,
    )

    assert ok is False and "timed out" in detail
    _assert_retired(spy, grandchild, elapsed)


async def test_frontend_build_timeout_takes_the_whole_tree(
    tmp_path, monkeypatch, pidfile
):
    """The console build — ``frontend.build_frontend_async``."""
    from gideon.operations import frontend

    monkeypatch.setattr(
        frontend,
        "_build_steps",
        lambda project: ((["/bin/sh", "-c", _forking_command(pidfile)], TIMEOUT_SECS),),
    )
    spy = _spy_on_exec(monkeypatch)
    reported: list[tuple[str, str]] = []

    _, grandchild, elapsed = await _drive(
        lambda: frontend.build_frontend_async(
            str(tmp_path), lambda level, message: reported.append((level, message))
        ),
        pidfile,
    )

    assert reported, "a build that blew its deadline reported nothing to the operator"
    _assert_retired(spy, grandchild, elapsed)


async def test_bash_provider_timeout_takes_the_whole_tree(
    tmp_path, monkeypatch, pidfile
):
    """The native command action provider — ``BashActionProvider.execute``."""
    from gideon.integrations.action_providers.base import ActionContext
    from gideon.integrations.action_providers.bash_provider import BashActionProvider

    spy = _spy_on_limited(monkeypatch)
    result, grandchild, elapsed = await _drive(
        lambda: BashActionProvider().execute(
            {"command": _forking_command(pidfile), "timeout": int(TIMEOUT_SECS)},
            ActionContext("Stop"),
            timeout=30,
        ),
        pidfile,
    )

    assert result.success is False and "Timed out" in result.error
    _assert_retired(spy, grandchild, elapsed)


async def test_workflow_container_verb_timeout_takes_the_whole_tree(
    tmp_path, monkeypatch, pidfile
):
    """A workflow container-backend verb — ``container_env._run_cli``."""
    from gideon.automation.workflows import container_env

    spy = _spy_on_limited(monkeypatch)
    result, grandchild, elapsed = await _drive(
        lambda: container_env._run_cli(
            ["/bin/sh", "-c", _forking_command(pidfile)],
            timeout=TIMEOUT_SECS,
            cwd=str(tmp_path),
        ),
        pidfile,
    )

    assert result.ok is False and "timed out" in result.reason
    _assert_retired(spy, grandchild, elapsed)


async def test_workflow_teardown_effect_timeout_takes_the_whole_tree(
    tmp_path, monkeypatch, pidfile
):
    """A workflow teardown effect — ``effects._TeardownInvocation.run``."""
    from gideon.automation.workflows import effects

    spy = _spy_on_limited(monkeypatch)
    command, problem = effects._TeardownInvocation.prepare(
        f"/bin/sh -c {_forking_command(pidfile)!r}", "output-1"
    )
    assert command is not None, problem

    (ok, detail), grandchild, elapsed = await _drive(
        lambda: command.run(TIMEOUT_SECS), pidfile
    )

    assert ok is False and "timed out" in detail
    _assert_retired(spy, grandchild, elapsed)


async def test_the_unowned_shape_leaks_all_three(pidfile):
    """VACUITY: the shape the owner replaced must leak the child, the grandchild and the pipes.

    Same command, same deadline, ``start_new_session`` still on — the only difference is
    that the kill goes to the child's pid and the reap is the plain ``wait()`` that
    resolves on pipe disconnect. If this control ever passed the assertions above, those
    tests would be proving nothing about the owner.
    """
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        _forking_command(pidfile),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    try:
        end = time.monotonic() + BOUND_SECS
        while not pidfile.exists() and time.monotonic() < end:
            await asyncio.sleep(0.02)
        grandchild = int(pidfile.read_text().strip())

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=0.5)
        proc.kill()
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(proc.wait(), timeout=2.0)

        assert _alive(grandchild), (
            "the control's pid-kill took the grandchild with it — this command no longer "
            "forks, so the tests above cannot tell a group kill from a pid kill"
        )
        assert (
            cancellation.open_pipe_count(proc) > 0
        ), "the control left no pipe open, so the pipe assertion above is vacuous"
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await cancellation.terminate_and_reap(proc)


async def test_owner_closes_pipes_and_empties_the_group(pidfile):
    """The owner itself, directly: one forking child, one bounded retirement."""
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        _forking_command(pidfile),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    assert cancellation.open_pipe_count(proc) == 3
    try:
        end = time.monotonic() + BOUND_SECS
        while not pidfile.exists() and time.monotonic() < end:
            await asyncio.sleep(0.02)

        started = time.monotonic()
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await cancellation.run_with_timeout(proc, 0.3)
        elapsed = time.monotonic() - started

        assert elapsed < BOUND_SECS
        assert proc.returncode is not None
        assert cancellation.open_pipe_count(proc) == 0
        assert _gone(int(pidfile.read_text().strip()))
        with pytest.raises((ProcessLookupError, PermissionError)):
            os.killpg(pgid, 0)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)


async def test_owner_escalates_when_the_child_ignores_sigterm():
    """SIGTERM first, SIGKILL after the grace — and the reap still lands in the bound."""
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        "trap '' TERM; echo ready; sleep 30",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert await asyncio.wait_for(proc.stdout.readline(), 10) == b"ready\n"
        started = time.monotonic()
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await cancellation.run_with_timeout(proc, 0.2, grace=0.3)
        elapsed = time.monotonic() - started

        assert proc.returncode == -signal.SIGKILL, (
            f"the child exited with {proc.returncode}; a child that ignores SIGTERM must "
            "be escalated to SIGKILL by the owner"
        )
        assert elapsed < BOUND_SECS
        assert cancellation.open_pipe_count(proc) == 0
    finally:
        if proc.returncode is None:  # pragma: no cover - the escalation failed
            await cancellation.terminate_and_reap(proc)
