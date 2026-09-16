"Per-observer event accumulation with first-event deadlines and immediate control delivery."

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

WINDOW_SECS = 0.025

MAX_BATCH = 50

BATCH_EVENT = "workflow_batch"

COALESCING_EVENTS = frozenset(
    {
        "workflow_node_started",
        "workflow_node_done",
        "workflow_progress",
    }
)


def is_coalescing(event: str) -> bool:
    """True when ``event`` may be batched.

    Deliberately an allowlist: a NEW event defaults to pass-through. The failure mode of
    wrongly passing through is one extra frame; the failure mode of wrongly coalescing is a
    gate ask arriving late or out of order, so the safe default is the boring one.
    """
    return event in COALESCING_EVENTS


class _Window:
    """One observer's in-flight batch and its timer."""

    __slots__ = ("events", "handle", "seen")

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.handle: asyncio.TimerHandle | None = None
        self.seen: dict[tuple[str, str], int] = {}

    def append(self, event: str, payload) -> None:
        path = payload.get("instance_path") if isinstance(payload, dict) else None
        identity = (event, path) if isinstance(path, str) else None
        position = self.seen.get(identity) if identity is not None else None
        record = {"event": event, "payload": payload}
        if position is None:
            if identity is not None:
                self.seen[identity] = len(self.events)
            self.events.append(record)
        else:
            self.events[position] = record

    def schedule(self, delay: float, callback, key: str) -> bool:
        if self.handle is not None:
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self.handle = loop.call_later(delay, callback, key)
        return True

    def cancel_timer(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            handle.cancel()

    def delivery(self):
        if len(self.events) != 1:
            return BATCH_EVENT, {"events": self.events}
        (record,) = self.events
        return record["event"], record["payload"]


class EventCoalescer:
    """Debounced per-observer batching in front of a raw publish function.

    ``sink(key, event, payload)`` is the underlying write (in production
    ``SseRegistry.publish``). Construct one per registry; keys scope the windows.
    """

    def __init__(
        self,
        sink: Callable[[str, str, Any], None],
        *,
        window: float = WINDOW_SECS,
        max_batch: int = MAX_BATCH,
    ) -> None:
        self._sink = sink
        self._window = window
        self._max = max_batch
        self._windows: dict[str, _Window] = {}

    def publish(self, key: str, event: str, payload: dict[str, Any]) -> None:
        if is_coalescing(event):
            window = self._windows.setdefault(key, _Window())
            window.append(event, payload)
            if len(window.events) < self._max:
                self._arm(key, window)
            else:
                self.flush(key)
        else:
            self.flush(key)
            self._sink(key, event, payload)

    def _arm(self, key: str, win: _Window) -> None:
        if not win.schedule(self._window, self._on_timer, key):
            self.flush(key)

    def _on_timer(self, key: str) -> None:
        window = self._windows.get(key)
        if window is not None:
            window.handle = None
        self.flush(key)

    def flush(self, key: str) -> None:
        window = self._windows.pop(key, None)
        if window is None:
            return
        window.cancel_timer()
        if window.events:
            try:
                event, payload = window.delivery()
                self._sink(key, event, payload)
            except Exception:
                logger.debug("coalesced flush failed for %s", key, exc_info=True)

    def flush_all(self) -> None:
        observers = tuple(self._windows)
        for observer in observers:
            self.flush(observer)

    @property
    def pending(self) -> int:
        """Total accumulated events across all observers — for tests and a debug view."""
        return sum(len(w.events) for w in self._windows.values())
