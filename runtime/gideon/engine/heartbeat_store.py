"""Serialize heartbeat queue read/change/write across threads and processes."""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from functools import wraps


@contextmanager
def queue_lock(path):
    from gideon.engine.heartbeat import heartbeat_path
    from gideon.core.concurrency import lock_path

    if os.path.realpath(path) != os.path.realpath(heartbeat_path()):
        yield
        return
    with lock_path("heartbeat-queue").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def queue_locked(path, writer):
    @wraps(writer)
    def locked():
        with queue_lock(path):
            return writer()
    return locked
