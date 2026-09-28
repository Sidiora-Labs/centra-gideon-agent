from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class PeriodicSweep:
    def __init__(self, name: str, interval: float, sweep: Callable[[], None]) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.name = name
        self.interval = interval
        self._sweep = sweep
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None

    def start(self) -> threading.Thread:
        while True:
            with self._lock:
                thread, stop = self._thread, self._stop
                if thread is not None and thread.is_alive():
                    if stop is None or not stop.is_set():
                        return thread
                    if thread is threading.current_thread():
                        return thread
                    stop.set()
                else:
                    stop = threading.Event()
                    thread = threading.Thread(
                        target=self._run,
                        args=(stop,),
                        name=self.name,
                        daemon=True,
                    )
                    self._thread, self._stop = thread, stop
                    thread.start()
                    return thread
            thread.join(5.0)
            if thread.is_alive():
                raise RuntimeError(f"{self.name} did not stop before restart")
            with self._lock:
                if self._thread is thread:
                    self._thread = self._stop = None

    def stop(self, timeout: float = 5.0) -> bool:
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        with self._lock:
            thread, stop = self._thread, self._stop
            if thread is None or stop is None:
                return True
            stop.set()
        if thread is not threading.current_thread():
            thread.join(timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._lock:
                if self._thread is thread:
                    self._thread = self._stop = None
        return stopped

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(self.interval):
            try:
                self._sweep()
            except Exception:
                logger.debug("%s sweep failed", self.name, exc_info=True)
