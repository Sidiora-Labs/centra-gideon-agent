"""Turn-scoped stop state and owned child-process retirement."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
import signal
from dataclasses import dataclass, field, fields
from typing import Any, Iterator

logger = logging.getLogger(__name__)
CANCEL_USER = "user"
CANCEL_INTERNAL = "internal"
REQUEST_NO_TURN = "no_turn"
REQUEST_FIRST = "first"
REQUEST_REPEAT = "repeat"
REAP_GRACE_SECS = 2.0
TIMEOUT_GRACE_SECS = 0.5
_PIPE_FDS = (0, 1, 2)


@dataclass
class StopReport:
    reason: str = ""
    model_request_aborted: bool = False
    children_reaped: int = 0
    children_escaped: int = 0
    tool_calls_dropped: int = 0
    subagents_stopped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass
class _TurnStopState:
    active: bool = False
    report: StopReport = field(default_factory=StopReport)
    children: dict[int, Any] = field(default_factory=dict)

    def detach_children(self) -> list[Any]:
        handles = list(self.children.values())
        self.children.clear()
        return handles

    def request(self, reason: str) -> str:
        if self.active and not self.report.reason:
            self.report.reason = reason or CANCEL_USER
            return REQUEST_FIRST
        return REQUEST_REPEAT if self.active else REQUEST_NO_TURN


class CancelScope:
    def __init__(self):
        self._state = _TurnStopState()

    def begin_turn(self) -> None:
        self._state = _TurnStopState(active=True)

    def end_turn(self) -> None:
        self._state.active = False
        self._state.detach_children()

    @property
    def turn_active(self) -> bool:
        return self._state.active

    @property
    def cancelled(self) -> bool:
        return bool(self.reason)

    @property
    def reason(self) -> str:
        return self.report.reason

    @property
    def stopped_by_user(self) -> bool:
        return self.reason == CANCEL_USER

    @property
    def report(self) -> StopReport:
        return self._state.report

    @property
    def child_count(self) -> int:
        return len(self._state.children)

    def request(self, reason: str = CANCEL_USER) -> str:
        return self._state.request(reason)

    def register_child(self, proc: Any) -> None:
        if (pid := getattr(proc, "pid", None)) is not None:
            self._state.children[int(pid)] = proc

    def unregister_child(self, proc: Any) -> None:
        if (pid := getattr(proc, "pid", None)) is not None:
            self._state.children.pop(int(pid), None)

    async def reap_children(self) -> int:
        handles = self._state.detach_children()
        successes = 0
        for handle in handles:
            try:
                complete = await terminate_and_reap(handle)
            except Exception:
                logger.warning("cancel: reaping child failed", exc_info=True)
                complete = False
            if complete:
                successes += 1
            else:
                self.report.children_escaped += 1
        self.report.children_reaped += successes
        if handles:
            logger.info(
                "cancel: reaped %d/%d child process(es) on stop",
                successes,
                len(handles),
            )
        return successes

    def note_model_request_aborted(self) -> None:
        self.report.model_request_aborted = True

    def note_tool_call_dropped(self, count: int = 1) -> None:
        self._increase("tool_calls_dropped", count)

    def note_subagents_stopped(self, count: int) -> None:
        self._increase("subagents_stopped", count)

    def _increase(self, name: str, count: int) -> None:
        setattr(self.report, name, getattr(self.report, name) + count)


def _is_group_leader(pid: int) -> bool:
    with contextlib.suppress(OSError):
        return os.getpgid(pid) == pid
    return False


def _signal_child(proc: Any, sig: int) -> None:
    raw = getattr(proc, "pid", None)
    if raw is None or int(raw) <= 1:
        return
    pid = int(raw)
    if _is_group_leader(pid):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except OSError:
            logger.debug(
                "cancel: killpg(%d) failed; falling back to pid", pid, exc_info=True
            )
        else:
            return
    with contextlib.suppress(ProcessLookupError, OSError, ValueError):
        action = proc.kill if sig == signal.SIGKILL else proc.terminate
        action()


def _pipe_transports(proc: Any) -> list[Any]:
    transport = getattr(proc, "_transport", None)
    getter = getattr(transport, "get_pipe_transport", None)
    if getter is None:
        return []
    pipes = []
    for descriptor in _PIPE_FDS:
        with contextlib.suppress(Exception):
            pipe = getter(descriptor)
            if pipe is not None:
                pipes.append(pipe)
    return pipes


def close_child_pipes(proc: Any) -> None:
    """Close the child's stdin/stdout/stderr so no descendant keeps our ends alive.

    A killed child does not close the pipes its own children inherited, and
    ``Process.wait()`` resolves on pipe disconnect rather than on reaping — so a
    grandchild that still holds the inherited stdout turns a deadline into the
    grandchild's full runtime. Retiring a child therefore has to drop OUR ends too.
    """
    writer = getattr(proc, "stdin", None)
    if writer is not None:
        with contextlib.suppress(Exception):
            writer.close()
    for pipe in _pipe_transports(proc):
        with contextlib.suppress(Exception):
            pipe.close()


def open_pipe_count(proc: Any) -> int:
    """How many of *proc*'s pipe transports are still open. Diagnostics and rails."""
    return sum(1 for pipe in _pipe_transports(proc) if not pipe.is_closing())


class _ChildRetirement:
    def __init__(self, process: Any, allowance: float):
        self.process = process
        self.allowance = allowance

    async def attempt(self, signal_number: int) -> bool:
        _signal_child(self.process, signal_number)
        try:
            await asyncio.wait_for(self.process.wait(), timeout=self.allowance)
        except Exception:
            return False
        return True

    async def retire(self) -> bool:
        """SIGTERM the group when it is ours, escalate to SIGKILL, reap, close pipes."""
        if getattr(self.process, "pid", None) is None:
            close_child_pipes(self.process)
            return False
        try:
            if self.process.returncode is not None:
                with contextlib.suppress(Exception):
                    await self.process.wait()
                return True
            for signal_number in (signal.SIGTERM, signal.SIGKILL):
                if await self.attempt(signal_number):
                    return True
            logger.warning(
                "cancel: child %s not reaped within %ss of SIGKILL",
                getattr(self.process, "pid", "?"),
                self.allowance,
            )
            return False
        finally:
            close_child_pipes(self.process)


async def terminate_and_reap(proc: Any, *, grace: float = REAP_GRACE_SECS) -> bool:
    """Retire a child we own: terminate, escalate, reap within ``2 * grace``."""
    return await _ChildRetirement(proc, grace).retire()


async def kill_timed_out(proc: Any, *, grace: float = TIMEOUT_GRACE_SECS) -> bool:
    """Retire a child that blew its deadline. The same owner, a tighter allowance."""
    return await _ChildRetirement(proc, grace).retire()


async def _under_deadline(
    proc: Any, awaited: Any, timeout: float | None, grace: float
) -> Any:
    try:
        return await asyncio.wait_for(awaited, timeout=timeout)
    except BaseException:
        with contextlib.suppress(BaseException):
            await kill_timed_out(proc, grace=grace)
        raise


async def run_with_timeout(
    proc: Any,
    timeout: float | None,
    *,
    payload: bytes | None = None,
    grace: float = TIMEOUT_GRACE_SECS,
) -> tuple[bytes, bytes]:
    """``proc.communicate(payload)`` under *timeout*. THE owner of a blown deadline.

    On expiry (or on cancellation, or on a broken pipe) the child is retired here and
    only here: its group is signalled when the child leads one, the signal escalates to
    SIGKILL after *grace*, the reap is bounded, and the child's pipes are closed so no
    descendant keeps them open. The original exception is then re-raised, so a caller
    keeps whatever it already does with ``asyncio.TimeoutError``.

    *payload* is forwarded only when there is one: a caller with no stdin pipe gets the
    bare ``communicate()`` it was already making, so routing a site through here never
    changes the call the child sees, and the return type is ``communicate()``'s own.
    """
    awaited = proc.communicate() if payload is None else proc.communicate(payload)
    return await _under_deadline(proc, awaited, timeout, grace)


async def wait_with_timeout(
    proc: Any, timeout: float | None, *, grace: float = TIMEOUT_GRACE_SECS
) -> int | None:
    """``proc.wait()`` under *timeout*, retired through :func:`run_with_timeout`'s owner."""
    await _under_deadline(proc, proc.wait(), timeout, grace)
    return proc.returncode


_CURRENT_SCOPE: contextvars.ContextVar[CancelScope | None] = contextvars.ContextVar(
    "gideon_cancel_scope", default=None
)


def bind_scope(scope: CancelScope | None) -> Any:
    return _CURRENT_SCOPE.set(scope)


def reset_scope(token: Any) -> None:
    with contextlib.suppress(ValueError, LookupError):
        _CURRENT_SCOPE.reset(token)


def current_scope() -> CancelScope | None:
    return _CURRENT_SCOPE.get()


@contextlib.contextmanager
def track_child(proc: Any) -> Iterator[None]:
    owner = current_scope()
    try:
        if owner is not None:
            owner.register_child(proc)
        yield
    finally:
        if owner is not None:
            owner.unregister_child(proc)
