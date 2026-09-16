"""File-backed execution leases and crash-survivor adoption."""

from __future__ import annotations

import fcntl
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, TypeVar

from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]")


def config_dir() -> Path:
    return config_loader.config_dir()


def _locks_dir() -> Path:
    directory = config_dir().joinpath("locks")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def lock_path(job_key: str) -> Path:
    pieces = (
        _UNSAFE.sub("_", job_key)[:48],
        hashlib.sha256(job_key.encode("utf-8")).hexdigest()[:8],
        "lock",
    )
    return _locks_dir().joinpath(".".join(pieces))


class _ExecutionLease:
    def __init__(self, stream):
        self.stream = stream
        self.acquired = False

    def acquire(self) -> bool:
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self.acquired = True
        return True

    def release(self) -> None:
        if self.acquired:
            fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.acquired = False


@contextmanager
def single_flight(job_key: str) -> Iterator[bool]:
    with lock_path(job_key).open("w") as stream:
        lease = _ExecutionLease(stream)
        try:
            yield lease.acquire()
        finally:
            lease.release()


class BootSweepRow(Protocol):
    id: str


_RowT = TypeVar("_RowT", bound=BootSweepRow)


async def boot_sweep(
    label: str,
    rows: Iterable[_RowT],
    *,
    survived: Callable[[_RowT], bool],
    decide: Callable[[_RowT], Awaitable[bool]],
) -> set[str]:
    snapshot = list(rows)
    candidates = tuple(filter(survived, snapshot))
    settled = set()
    for candidate in candidates:
        try:
            changed = await decide(candidate)
        except Exception:
            logger.warning(
                "boot_sweep[%s]: failed to decide %r",
                label,
                candidate.id,
                exc_info=True,
            )
        else:
            if changed:
                settled.add(candidate.id)
    if candidates:
        logger.info(
            "boot_sweep[%s]: %d crash survivor(s), %d decided",
            label,
            len(candidates),
            len(settled),
        )
    return settled
