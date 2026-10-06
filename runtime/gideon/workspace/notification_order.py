"""Display notification history by recorded time without mutating its append order."""

import math
from datetime import datetime
from typing import Any, Iterable


def _instant(value: Any) -> float:
    if isinstance(value, bool):
        return -math.inf
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else -math.inf
    if not isinstance(value, str) or not value.strip():
        return -math.inf
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return -math.inf


def newest_first(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest recorded instant first; equal instants use last-write-first order."""
    return sorted(rows, key=lambda row: _instant(row.get("ts")))[::-1]
