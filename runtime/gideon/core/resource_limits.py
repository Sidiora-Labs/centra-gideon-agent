"""Best-effort file-descriptor capacity with explicit platform availability."""

from __future__ import annotations

import logging
from dataclasses import dataclass

try:
    import resource as _resource
except ImportError:
    _resource = None

logger = logging.getLogger(__name__)
DEFAULT_FD_TARGET = 10240


@dataclass(frozen=True)
class FdLimitResult:
    available: bool
    raised: bool
    soft: int | None = None
    target: int | None = None


def resource_limits_available() -> bool:
    return bool(_resource is not None)


def raise_fd_limit(target: int = DEFAULT_FD_TARGET) -> FdLimitResult:
    backend = _resource
    if backend is None:
        return FdLimitResult(False, False)
    try:
        current, ceiling = backend.getrlimit(backend.RLIMIT_NOFILE)
        desired = min(ceiling, target)
        changed = current < desired
        if changed:
            backend.setrlimit(backend.RLIMIT_NOFILE, (desired, ceiling))
            logger.info("Raised FD limit: %d → %d", current, desired)
        return FdLimitResult(True, changed, current, desired)
    except Exception:
        return FdLimitResult(True, False)
