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

    async def graceful(self) -> bool:
        if getattr(self.process, "pid", None) is None:
            return False
        if self.process.returncode is not None:
            with contextlib.suppress(Exception):
                await self.process.wait()
            return True
        for signal_number in (signal.SIGTERM, signal.SIGKILL):
            if await self.attempt(signal_number):
                return True
        logger.warning(
            "cancel: child %s survived SIGKILL", getattr(self.process, "pid", "?")
        )
        return False

    async def expired(self) -> bool:
        if getattr(self.process, "pid", None) is None:
            return False
        complete = await self.attempt(signal.SIGKILL)
        if not complete:
            logger.warning(
                "cancel: timed-out child %s not reaped within %ss",
                getattr(self.process, "pid", "?"),
                self.allowance,
            )
        return complete


async def terminate_and_reap(proc: Any, *, grace: float = REAP_GRACE_SECS) -> bool:
    return await _ChildRetirement(proc, grace).graceful()


async def kill_timed_out(proc: Any, *, grace: float = REAP_GRACE_SECS) -> bool:
    return await _ChildRetirement(proc, grace).expired()


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
