"""Order bounded records by their own timestamps, preserving ties."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any, TypeVar

T = TypeVar('T')


def instant(value: Any) -> float:
    """Return an ordering instant; unreadable values precede every valid time."""
    if isinstance(value, bool):
        return -math.inf
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else -math.inf
    if not isinstance(value, str) or not value.strip():
        return -math.inf
    try:
        return datetime.fromisoformat(value.strip().replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return -math.inf


def in_time_order(rows: Iterable[T], *, at: str | Callable[[T], Any]) -> list[T]:
    """Order oldest first; records at the same time keep their existing order."""
    def key(row: T) -> float:
        if callable(at):
            try:
                return instant(at(row))
            except (AttributeError, KeyError, TypeError, ValueError):
                return -math.inf
        return instant(row.get(at)) if isinstance(row, Mapping) else -math.inf
    return sorted(rows, key=key)
