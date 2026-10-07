"""Persistent native-home signing key shared by authentication and provenance."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from gideon.core.atomic_write import atomic_write_bytes
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
KEY_FILE = "session_key"
KEY_BYTES = 32


def key_path() -> Path:
    return config_loader.config_dir() / KEY_FILE


def load_or_create_key(path: Path | None = None) -> bytes:
    """The persistent signing key, creating it on first use.

    Raises ``OSError`` when the key can neither be read nor written — see the module note on
    fail-closed. A caller that genuinely wants ephemeral behavior (tests, `--test-mode`) asks
    for it explicitly rather than getting it from a swallowed error.
    """
    path = key_path() if path is None else path
    try:
        if path.is_file():
            raw = path.read_bytes()
            if len(raw) >= KEY_BYTES:
                _ensure_owner_only(path)
                return raw
            logger.warning(
                "session key at %s is too short (%d bytes) — regenerating",
                path,
                len(raw),
            )
    except OSError:
        logger.warning("session key unreadable at %s", path, exc_info=True)

    key = os.urandom(KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, key, mode=0o600)
    logger.info("created a persistent session signing key at %s", path)
    return key


def _ensure_owner_only(path: Path) -> None:
    """Tighten the key file to 0600 if something loosened it.

    Not merely cosmetic: a key readable by another local account is a key that account can
    use to mint a dashboard session for itself.
    """
    try:
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            path.chmod(0o600)
            logger.warning("session key had mode %o — tightened to 0600", mode)
    except OSError:
        logger.debug("could not verify session key permissions", exc_info=True)


