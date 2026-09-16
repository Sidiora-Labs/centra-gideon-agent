"""Replace state files as a single commit and publish successful changes."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class _WriteSubscriptions:
    def __init__(self) -> None:
        self._listeners: tuple[Callable[[Path], object], ...] = ()
        self._mutex = threading.Lock()
        self._delivery = threading.local()

    def subscribe(self, listener: Callable[[Path], object]) -> None:
        with self._mutex:
            if listener not in self._listeners:
                self._listeners += (listener,)

    def unsubscribe(self, listener: Callable[[Path], object]) -> None:
        with self._mutex:
            self._listeners = tuple(
                item for item in self._listeners if item != listener
            )

    def snapshot(self) -> tuple[Callable[[Path], object], ...]:
        with self._mutex:
            return self._listeners

    def publish(self, destination: Path) -> None:
        if getattr(self._delivery, "busy", False):
            return
        self._delivery.busy = True
        try:
            for listener in self.snapshot():
                try:
                    listener(destination)
                except Exception:
                    logger.debug("State write subscriber failed", exc_info=True)
        finally:
            self._delivery.busy = False


class _CreationPermissions:
    def __init__(self) -> None:
        self._cached: int | None = None
        self._mutex = threading.Lock()

    def resolve(self, requested: int | None) -> int:
        if requested is not None:
            return requested
        with self._mutex:
            if self._cached is None:
                previous = os.umask(0)
                os.umask(previous)
                self._cached = 0o666 & ~previous
            return self._cached


_subscriptions = _WriteSubscriptions()
_permissions = _CreationPermissions()


def register_post_write_hook(hook: Callable[[Path], object]) -> None:
    _subscriptions.subscribe(hook)


def unregister_post_write_hook(hook: Callable[[Path], object]) -> None:
    _subscriptions.unsubscribe(hook)


def post_write_hooks() -> tuple[Callable[[Path], object], ...]:
    return _subscriptions.snapshot()


def _replace_file(
    destination: Path,
    payload: str | bytes,
    *,
    binary: bool,
    durable: bool,
    permissions: int | None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb" if binary else "w",
            encoding=None if binary else "utf-8",
            dir=destination.parent,
            suffix=".tmp",
            delete=False,
        ) as staged:
            staged_name = staged.name
            os.fchmod(staged.fileno(), _permissions.resolve(permissions))
            staged.write(payload)
            if durable:
                staged.flush()
                os.fsync(staged.fileno())
        os.replace(staged_name, str(destination))
        staged_name = None
    finally:
        if staged_name is not None:
            try:
                os.unlink(staged_name)
            except OSError:
                pass
    _subscriptions.publish(destination)


def atomic_write(
    path: Path | str,
    content: str,
    *,
    fsync: bool = False,
    mode: int | None = None,
) -> None:
    _replace_file(Path(path), content, binary=False, durable=fsync, permissions=mode)


def atomic_write_bytes(
    path: Path | str,
    data: bytes,
    *,
    fsync: bool = False,
    mode: int | None = None,
) -> None:
    _replace_file(Path(path), data, binary=True, durable=fsync, permissions=mode)
