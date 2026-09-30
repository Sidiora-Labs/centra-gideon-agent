"""Gideon's runtime identity and process shutdown coordination."""

from __future__ import annotations

import asyncio
import threading
from importlib.metadata import PackageNotFoundError, version

_FALLBACK_VERSION = "0.1.3"
try:
    __version__ = version("gideon-agent-harness")
except PackageNotFoundError:
    __version__ = _FALLBACK_VERSION


class ShutdownLatch:
    """A resettable signal whose waiters belong to their own running loops."""

    def __init__(self) -> None:
        self._requested = False
        self._restart_intent: object | None = None
        self._waiters: set[asyncio.Future[bool]] = set()
        self._mutex = threading.Lock()

    async def wait(self) -> bool:
        try:
            owner = asyncio.get_running_loop()
        except RuntimeError as error:
            raise RuntimeError(
                "shutdown_event cannot be accessed without a running event loop"
            ) from error
        with self._mutex:
            if self._requested:
                return True
            waiter = owner.create_future()
            self._waiters.add(waiter)
        try:
            return await waiter
        finally:
            with self._mutex:
                self._waiters.discard(waiter)

    @staticmethod
    def _release(waiter: asyncio.Future[bool]) -> None:
        if not waiter.done():
            waiter.set_result(True)

    def set(self) -> None:
        with self._mutex:
            self._requested = True
            self._restart_intent = None
            waiting = tuple(self._waiters)
        for waiter in waiting:
            try:
                waiter.get_loop().call_soon_threadsafe(self._release, waiter)
            except RuntimeError:
                pass

    def request_restart(self, intent: object) -> bool:
        """Request shutdown with a restart intent unless an ordinary stop won."""
        with self._mutex:
            if self._requested:
                return False
            self._requested = True
            self._restart_intent = intent
            waiting = tuple(self._waiters)
        for waiter in waiting:
            try:
                waiter.get_loop().call_soon_threadsafe(self._release, waiter)
            except RuntimeError:
                pass
        return True

    def take_restart_intent(self) -> object | None:
        """Consume pending restart intent after the ordinary shutdown hooks run."""
        with self._mutex:
            intent = self._restart_intent
            self._restart_intent = None
            return intent

    def clear(self) -> None:
        with self._mutex:
            self._requested = False
            self._restart_intent = None

    def is_set(self) -> bool:
        with self._mutex:
            return self._requested


shutdown_event = ShutdownLatch()
