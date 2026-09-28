"""Shared lifetime policy for self-hosted sign-in sessions."""

from __future__ import annotations

import re

MAX_SESSION_TTL_SECS = 90 * 24 * 60 * 60
DEFAULT_BROWSER_SESSION_TTL_SECS = 30 * 24 * 60 * 60
_UNITS = {"m": 60, "h": 3600, "d": 86400}


def parse_lifetime(value: str, *, units: str = "mhd") -> int | None:
    """Parse a positive integer duration and reject values over the 90-day policy."""
    allowed = "".join(unit for unit in _UNITS if unit in units)
    match = re.fullmatch(rf"(\d+)([{re.escape(allowed)}])", (value or "").strip())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    seconds = amount * _UNITS[unit]
    return seconds if 0 < seconds <= MAX_SESSION_TTL_SECS else None


def cap_legacy_expiry(issued_at: float, expiry: float) -> float:
    """Apply the present-day cap to a previously issued session claim."""
    return min(float(expiry), float(issued_at) + MAX_SESSION_TTL_SECS)
