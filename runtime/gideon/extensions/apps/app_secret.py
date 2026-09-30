"""Per-app proxy secret — mint + read the HMAC key that authenticates the proxy.

An app backend binds on loopback with no inbound auth of its own (see
``docs/architecture/APP_PLATFORM.md`` §2.1): the port is a *network* boundary, not
an *authorization* one. To make the permission model hold, every request the gateway
reverse-proxy forwards is signed with an HMAC over a per-app secret, and the backend's
SDK middleware refuses anything unsigned (fail-closed). This module owns that secret's
one true storage shape so the two call sites agree:

- :func:`rotate_app_secret` — used by the backend supervisor at ``start()`` to mint
  a fresh secret after any previous child has exited, then inject it into the child
  env as ``GIDEON_APP_SECRET``. Fail-closed: returns ``None`` if replacement fails.
- :func:`ensure_app_secret` — compatibility helper to mint only when absent and read
  the secret; it is not used for backend launches.
- :func:`read_app_secret` — used by the proxy handler at forward time to sign. The
  supervisor already minted it; the proxy just reads (returns ``None`` if absent →
  the proxy fails closed rather than forwarding unsigned).

Kept in its own module (not inline in ``backend_runtime`` or the handler) precisely
because two independent call sites need identical path + 0600 discipline; a single
auditable home is safer than duplicating the crypto-adjacent bits.

The secret is a 256-bit hex token (``secrets.token_hex(32)``). The file is 0600 and its
value is NEVER logged.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.extensions.apps.manager import app_dir

logger = logging.getLogger(__name__)

APP_SECRET_FILENAME = ".app_secret"
_SECRET_BYTES = 32


def secret_path(name: str) -> Path:
    """``apps_dir()/<app>/.app_secret`` — the per-app secret file path."""
    return app_dir(name) / APP_SECRET_FILENAME


def ensure_app_secret(name: str) -> str | None:
    """Mint (if absent) and return app ``name``'s proxy secret. ``None`` on failure.

    Fail-closed: if the secret cannot be created or read, the caller (the backend
    supervisor) must NOT start the backend — an unprotected backend is worse than a
    missing one. Never logs the secret value.
    """
    path = secret_path(name)
    try:
        if path.exists() and not path.is_symlink():
            existing = path.read_text(encoding="ascii").strip()
            if existing:
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                return existing
        return rotate_app_secret(name)
    except OSError as exc:
        logger.warning("app %s: could not mint/read proxy secret: %s", name, exc)
        return None


def rotate_app_secret(name: str) -> str | None:
    """Atomically replace app ``name``'s proxy secret with a fresh 0600 token.

    ``atomic_write`` stages beside the destination and uses ``os.replace``, so an
    existing secret or planted symlink is replaced as a directory entry instead of
    being opened through the link. A failed replacement returns ``None`` so callers
    can refuse to launch an unprotected backend.
    """
    path = secret_path(name)
    token = secrets.token_hex(_SECRET_BYTES)
    try:
        atomic_write(path, token, mode=0o600, fsync=True)
        return token
    except OSError as exc:
        logger.warning("app %s: could not rotate proxy secret: %s", name, exc)
        return None


def read_app_secret(name: str) -> str | None:
    """Read app ``name``'s proxy secret for signing. ``None`` if absent/unreadable.

    Used by the proxy at forward time. Does NOT mint — the supervisor owns minting at
    boot; a missing secret here means the backend was never started protected, so the
    proxy fails closed. Never logs the secret value.
    """
    path = secret_path(name)
    try:
        value = path.read_text(encoding="ascii").strip()
        return value or None
    except OSError:
        return None
