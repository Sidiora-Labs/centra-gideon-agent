"""Bounded, cross-process read-modify-write transactions for config.json."""

from __future__ import annotations

import asyncio
import copy
import errno
import fcntl
import json
import logging
import os
import stat
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from gideon.core.atomic_write import atomic_json_write
from gideon.core.config.document import configuration_values
from gideon.core.config.loader import AppConfig, ConfigPreserveError, config_path

DEFAULT_TIMEOUT_SECS = 5.0
_POLL_SECS = 0.02
T = TypeVar("T")
_MISSING = object()
logger = logging.getLogger(__name__)
_held = threading.local()
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


class ConfigWriteError(RuntimeError):
    """A config transaction refused to write."""


class ConfigLockTimeout(ConfigWriteError):
    """The bounded wait for another config writer expired."""


class NestedConfigTransaction(ConfigWriteError):
    """A transaction was opened recursively on the same thread."""


def lock_path_for(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


class _ConfigLock:
    def __init__(self, path: Path, timeout: float) -> None:
        self.path = lock_path_for(path)
        self.key = os.path.realpath(self.path)
        self.timeout = max(0.0, float(timeout))
        with _thread_locks_guard:
            self.thread_lock = _thread_locks.setdefault(self.key, threading.Lock())
        self.fd = -1

    def __enter__(self) -> None:
        held: set[str] = getattr(_held, "paths", set())
        if self.key in held:
            raise NestedConfigTransaction(
                "nested config transaction refused; nothing was written"
            )
        deadline = time.monotonic() + self.timeout
        if not self.thread_lock.acquire(timeout=self.timeout):
            raise ConfigLockTimeout("config lock timed out; nothing was written")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(
                self.path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            if not stat.S_ISREG(os.fstat(self.fd).st_mode):
                raise ConfigWriteError(
                    "config lock is not a regular file; nothing was written"
                )
            os.fchmod(self.fd, 0o600)
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise ConfigWriteError(
                            "could not acquire config lock; nothing was written"
                        ) from exc
                    if time.monotonic() >= deadline:
                        raise ConfigLockTimeout(
                            "config lock timed out; nothing was written"
                        ) from exc
                    time.sleep(_POLL_SECS)
        except BaseException:
            self._release()
            raise
        held = set(held)
        held.add(self.key)
        _held.paths = held

    def __exit__(self, *_: object) -> None:
        held = set(getattr(_held, "paths", set()))
        held.discard(self.key)
        _held.paths = held
        self._release()

    def _release(self) -> None:
        if self.fd >= 0:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = -1
        if self.thread_lock.locked():
            self.thread_lock.release()


def _read_document(path: Path) -> dict[str, Any]:
    if not os.path.lexists(path):
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigPreserveError(
            f"config exists but is unreadable; nothing was written ({exc})"
        ) from exc
    if not isinstance(value, dict):
        raise ConfigPreserveError("config is not a JSON object; nothing was written")
    return value


def _diff(
    before: Any, after: Any, prefix: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[tuple[tuple[str, ...], Any]] = []
        for key in before.keys() | after.keys():
            old, new = before.get(key, _MISSING), after.get(key, _MISSING)
            if old is _MISSING:
                changes.append((prefix + (key,), copy.deepcopy(new)))
            elif new is _MISSING:
                changes.append((prefix + (key,), _MISSING))
            else:
                changes.extend(_diff(old, new, prefix + (key,)))
        return changes
    return [] if before == after else [(prefix, copy.deepcopy(after))]


def _apply(
    document: dict[str, Any], changes: list[tuple[tuple[str, ...], Any]]
) -> None:
    for path, value in changes:
        current: dict[str, Any] = document
        for part in path[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        if value is _MISSING:
            current.pop(path[-1], None)
        else:
            current[path[-1]] = copy.deepcopy(value)


def _write(path: Path, document: dict[str, Any], previous: dict[str, Any]) -> None:
    from gideon.core.config.secret_refs import prepare_config_secrets

    plan = prepare_config_secrets(document, previous=previous)
    stored = plan.document
    if not isinstance(stored, dict):
        plan.abort()
        raise ConfigWriteError(
            "secret-reference mapper returned an invalid config document; nothing was written"
        )
    try:
        atomic_json_write(path, stored, replace_fallback=False)
    except BaseException as exc:
        try:
            cleanup = plan.abort()
            if getattr(cleanup, "status", "") == "abort_cleanup_incomplete":
                logger.warning(
                    "config secret cleanup was incomplete after an aborted config write"
                )
        except BaseException:
            logger.warning("config secret cleanup failed after an aborted config write")
        if isinstance(exc, OSError):
            raise ConfigWriteError(
                "config replacement failed; nothing was written"
            ) from exc
        raise
    try:
        cleanup = plan.commit()
        if getattr(cleanup, "status", "") == "cleanup_incomplete":
            logger.warning(
                "config secret cleanup was incomplete after a committed config write"
            )
    except Exception:
        logger.warning("config secret cleanup failed after a committed config write")


def mutate_config(
    mutator: Callable[[dict[str, Any]], T],
    *,
    path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    create_if_missing: bool = False,
) -> T:
    target = path or config_path()
    with _ConfigLock(target, timeout):
        target_missing = not os.path.lexists(target)
        previous = _read_document(target)
        document = copy.deepcopy(previous)
        result = mutator(document)
        if document != previous or (create_if_missing and target_missing):
            _write(target, document, previous)
        return result


async def mutate_config_async(
    mutator: Callable[[dict[str, Any]], T],
    *,
    path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
) -> T:
    return await asyncio.to_thread(mutate_config, mutator, path=path, timeout=timeout)


def _save_changes(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    originally_missing: bool = False,
) -> None:
    target = path or config_path()
    with _ConfigLock(target, timeout):
        document = _read_document(target)
        if originally_missing and not target.exists():
            document.update(copy.deepcopy(after))
        else:
            _apply(document, _diff(before or {}, after))
        from gideon import __version__

        document.setdefault(
            "meta",
            {
                "lastTouchedVersion": __version__,
                "lastTouchedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        if document != _read_document(target):
            _write(target, document, _read_document(target))


def save_config(configuration: AppConfig) -> None:
    before = getattr(configuration, "_loaded_values", None)
    _save_changes(
        before,
        configuration_values(configuration),
        originally_missing=getattr(configuration, "_loaded_missing", before is None),
    )
    configuration._loaded_values = copy.deepcopy(configuration_values(configuration))
    configuration._loaded_missing = False


def update_config(
    change: Callable[[AppConfig], T], *, timeout: float = DEFAULT_TIMEOUT_SECS
) -> T:
    path = config_path()
    with _ConfigLock(path, timeout):
        _read_document(path)
        configuration = AppConfig.load()
        before = configuration_values(configuration)
        result = change(configuration)
        after = configuration_values(configuration)
        document = _read_document(path)
        _apply(document, _diff(before, after))
        if document != _read_document(path):
            _write(path, document, _read_document(path))
        configuration._loaded_values = copy.deepcopy(after)
        configuration._loaded_missing = False
        return result


async def update_config_async(
    change: Callable[[AppConfig], T], *, timeout: float = DEFAULT_TIMEOUT_SECS
) -> T:
    return await asyncio.to_thread(update_config, change, timeout=timeout)
