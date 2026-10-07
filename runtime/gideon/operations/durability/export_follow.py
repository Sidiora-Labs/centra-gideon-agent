"""Keep credential-bearing configuration exports current after local writes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from gideon.operations.durability import inventory as inv

logger = logging.getLogger(__name__)

#: How long a write that found the hourly export running waits to be exported again.
RETRY_SECS = 2.0
#: How long a stop waits for a re-export that is already running before it carries on.
STOP_WAIT_SECS = 5.0


class ExportFollower:
    """Re-exports a followed store's shard as soon as a write to it lands.

    ``later(delay, work)`` runs *work* after *delay* seconds, off the writer's thread: the retry
    after a busy export, and the catch-up :func:`install` asks for. A test passes one it drives;
    otherwise each runs on a timer the follower holds until the timer's thread has ended, so
    :meth:`stop` can take it back or wait for it.
    """

    def __init__(
        self,
        home: Path,
        *,
        retry_secs: float = RETRY_SECS,
        later: Callable[[float, Callable[[], object]], None] | None = None,
    ) -> None:
        self._home = Path(home)
        try:
            # Resolved once: every write of the process is matched against it.
            self._root = self._home.resolve()
        except OSError:
            self._root = self._home
        self._retry_secs = retry_secs
        self._later = later or self._on_a_timer
        self._followed = tuple(e for e in inv.export_entries() if e.exported_on_write)
        #: The first part of each followed store's path: a write whose path holds none of them is
        #: passed over without a look at the disk.
        self._first_parts = frozenset(Path(e.path).parts[0] for e in self._followed)
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._retrying = False
        self._timers: set[threading.Timer] = set()
        self._stopped = threading.Event()
        #: How many re-exports ran, and how many times one waited for the hourly job.
        self.exports = 0
        self.retries = 0

    def entry_of(self, path: Path | str) -> inv.StateEntry | None:
        """The followed store a write to *path* changed, or ``None``."""
        if self._first_parts.isdisjoint(Path(path).parts):
            return None
        try:
            rel = Path(path).resolve().relative_to(self._root).as_posix()
        except (OSError, ValueError):
            return None
        if not any(
            rel == e.path or rel.startswith(f"{e.path}/") for e in self._followed
        ):
            return None
        if inv.is_ignored(rel):
            return None
        entry = inv.claim_for(rel)
        return entry if entry is not None and entry.exported_on_write else None

    def notify(self, path: Path | str) -> bool:
        """A write landed at *path*: re-export its store now, when it is one that follows.
        Returns whether it is. Never raises: this runs in the writer's path, after the write.
        """
        if self._stopped.is_set():
            return False
        entry = self.entry_of(path)
        if entry is None:
            return False
        with self._lock:
            self._pending.add(entry.id)
        try:
            self.flush()
        except (
            Exception
        ):  # noqa: BLE001 — a re-export must never fail the write that asked
            logger.warning(
                "durability: could not re-export after a write to %s",
                path,
                exc_info=True,
            )
        return True

    def catch_up(self) -> None:
        """Re-export every followed store, off the caller's thread: what the gateway's start changed
        before this follower was listening (it moves plaintext credentials into the store).
        """
        with self._lock:
            self._pending.update(e.id for e in self._followed)
        self._later(0.0, self._flush_quietly)

    def flush(self) -> list[str]:
        """Re-export every store that is waiting, now. Returns the ids it re-exported: none when
        there is no export to keep up, or when the hourly job holds it (tried again shortly).
        """
        from gideon.core.concurrency import single_flight
        from gideon.operations.durability.shards import (
            default_shard_dir,
            export_shards,
            is_an_export,
        )

        if self._stopped.is_set():
            return []
        out_dir = default_shard_dir(self._home)
        with single_flight("durability:export") as acquired:
            if not acquired:
                self._retry()
                return []
            if self._stopped.is_set():
                return []
            with self._lock:
                pending, self._pending = sorted(self._pending), set()
            if not pending or not is_an_export(out_dir):
                return []
            try:
                result = export_shards(self._home, out_dir, entries=pending)
                if result.skipped:
                    with self._lock:
                        self._pending.update(pending)
                    self._retry()
            except Exception:
                with self._lock:
                    self._pending.update(pending)
                raise
        self.exports += 1
        return pending

    def stop(self) -> None:
        """Stop: a re-export waiting for its time never runs, and every timer's thread is waited
        for, one running a re-export at most :data:`STOP_WAIT_SECS`. A gateway's stop uninstalls
        its follower, and a retry the follower left waiting used to run after it, in whatever home
        was current by then."""
        self._stopped.set()
        with self._lock:
            timers = list(self._timers)
        for timer in timers:
            timer.cancel()
            if timer is not threading.current_thread():
                timer.join(STOP_WAIT_SECS)

    def _on_a_timer(self, delay: float, work: Callable[[], object]) -> None:
        """Run *work* after *delay* seconds on a timer this follower holds until its thread has
        ended.

        The timer is started under the lock :meth:`stop` reads the timers under, so a stop never
        finds one that has not started: waiting for one that has not started raises, and the stop
        broke off there. It is held until its thread has ended, not until its work has run: the
        thread runs on a moment after its work, and a stop waits for the thread."""
        timer = threading.Timer(delay, work)
        timer.daemon = True
        with self._lock:
            if self._stopped.is_set():
                return
            timer.start()
            self._timers = {held for held in self._timers if held.is_alive()}
            self._timers.add(timer)

    def _flush_quietly(self) -> None:
        if self._stopped.is_set():
            return
        try:
            self.flush()
        except (
            Exception
        ):  # noqa: BLE001 — off the writer's thread, with no caller to tell
            logger.warning(
                "durability: a re-export after a write failed", exc_info=True
            )

    def _retry(self) -> None:
        with self._lock:
            if self._retrying:
                return
            self._retrying = True
        self.retries += 1

        def again() -> None:
            with self._lock:
                self._retrying = False
            self._flush_quietly()

        self._later(self._retry_secs, again)


_installed: ExportFollower | None = None
_install_lock = threading.Lock()


def install(*, home: Path) -> ExportFollower:
    """Install a follower for the active home, stopping any prior home's follower."""
    global _installed
    from gideon.core.atomic_write import (
        register_post_write_hook,
        unregister_post_write_hook,
    )

    with _install_lock:
        if (
            _installed is not None
            and _installed._home.absolute() == Path(home).absolute()
        ):
            return _installed
        previous = _installed
        if previous is not None:
            unregister_post_write_hook(previous.notify)
            previous.stop()
        follower = ExportFollower(home)
        register_post_write_hook(follower.notify)
        _installed = follower
    follower.catch_up()
    return follower


def uninstall(follower: ExportFollower | None = None) -> None:
    """Remove this follower; an older gateway's cleanup cannot remove a newer owner."""
    global _installed
    from gideon.core.atomic_write import unregister_post_write_hook

    with _install_lock:
        selected = follower or _installed
        if selected is _installed:
            _installed = None
    if selected is not None:
        unregister_post_write_hook(selected.notify)
        selected.stop()
