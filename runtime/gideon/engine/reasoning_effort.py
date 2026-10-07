"""Validate provider-defined reasoning effort tokens before storing or launching them."""

import logging
import re

logger = logging.getLogger(__name__)
_REASONING_EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")


def validate_reasoning_effort(raw: object) -> str:
    """Return *raw* if it's a safe reasoning_effort token, else "".

    Enforces a format (not a fixed value set) so any backend-declared effort is
    accepted while a tampered/corrupted metadata file cannot smuggle spaces or
    shell metacharacters into a subprocess ``--effort`` arg / config value.
    """
    if raw == "" or raw is None:
        return ""
    if isinstance(raw, str) and _REASONING_EFFORT_RE.match(raw):
        return raw
    if raw:
        logger.warning("Discarding invalid persisted reasoning_effort: %r", raw)
    return ""
