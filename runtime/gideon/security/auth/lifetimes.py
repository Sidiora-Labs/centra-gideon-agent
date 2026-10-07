"""Shared lifetime policy for self-hosted sign-in sessions."""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

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


def parse_config_duration(s: str, *, default_secs: int) -> int:
    """Parse ``'<int>[mhd]'`` from CONFIG into seconds, falling back to *default_secs*.

    Deliberately a second function rather than a widened `parse_duration`. That one serves
    `gideon token --ttl` and the token endpoint, where an unrecognised unit must be a
    hard error the user sees immediately — silently reading ``30d`` as something else would
    mint a token with the wrong lifetime. Here the input is a config file that may have been
    hand-edited, so the posture is the opposite: never let a typo brick the box; take the
    documented default and carry on. ``d`` is accepted because a browser session lifetime is
    naturally expressed in days (the plan's ``30d``), where a token's is in hours.
    """
    value = parse_lifetime(s, units="mhd")
    if value is None:
        logger.warning("unparseable duration %r in config — using the default", s)
        return default_secs
    return value


