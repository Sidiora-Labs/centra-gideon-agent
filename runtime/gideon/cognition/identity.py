"""Owner identity — the attribution username (TEAM-SHARED-ENTITIES §1).

Single-string attribution stamped on new writes. Not a credential, not an
authenticator — answers "who wrote this row", never "who may". Empty means no
attribution (pre-existing behavior, unchanged). Slug rule is strict because
this string lands in JSON records and shard filenames effectively forever.
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

USERNAME_MAX_LEN = 32
_ALLOWED = re.compile(r"[^a-z0-9_-]+")
_REPEATS = re.compile(r"-{2,}")


def slugify_username(raw: str) -> str:
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", str(raw))
    letters = "".join(
        character for character in normalized if not unicodedata.combining(character)
    ).lower()
    output: list[str] = []
    for character in letters:
        allowed = "a" <= character <= "z" or "0" <= character <= "9" or character == "_"
        if allowed:
            output.append(character)
        elif not output or output[-1] != "-":
            output.append("-")
    slug = "".join(output).strip("-_")
    return (
        slug[:USERNAME_MAX_LEN].rstrip("-_") if len(slug) > USERNAME_MAX_LEN else slug
    )


def is_valid_username(value: str) -> bool:
    """Whether ``value`` is already in canonical form."""
    return value == slugify_username(value)


def suggest_username(display_name: str) -> str:
    """Pre-fill username from the operator's display name."""
    return slugify_username(display_name)


def current_username() -> str:
    """The owner's username, or ``""`` when unset. Never raises."""
    try:
        from gideon.core.config.loader import AppConfig

        return slugify_username(AppConfig.load().dashboard.username or "")
    except Exception:
        logger.debug("identity: username unreadable — writing without attribution")
        return ""
