"""Hugging Face authentication resolution and guarded validation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

HF_CREDENTIAL_KEY = "HF_TOKEN"
HF_ENV_KEYS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
WHOAMI_URL = "https://huggingface.co/api/whoami-v2"
VALIDATION_CACHE_FILE = "huggingface_auth_cache.json"
DEFAULT_VALIDATION_TTL_SECS = 300
_MAX_TOKEN_BYTES = 4096
_MAX_CACHE_BYTES = 16 * 1024
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_HF_TOKEN_RE = re.compile(r"\bhf_[A-Za-z0-9_-]{10,}\b")

TokenSource = Literal["credential_store", "environment", "huggingface_cache", "none"]
AuthState = Literal["unconfigured", "valid", "invalid", "unavailable"]

_cache_lock = threading.RLock()
_memory_cache: dict[str, "ValidationResult"] = {}


@dataclass(frozen=True)
class ResolvedToken:
    token: str = field(repr=False)
    source: TokenSource
    environment_key: str = ""


@dataclass(frozen=True)
class ValidationResult:
    state: AuthState
    username: str = ""
    error: str = ""
    checked_at: float = 0.0
    expires_at: float = 0.0
    cached: bool = False

    @property
    def valid(self) -> bool | None:
        if self.state == "valid":
            return True
        if self.state == "invalid":
            return False
        return None

    def from_cache(self) -> "ValidationResult":
        return ValidationResult(
            state=self.state,
            username=self.username,
            error=self.error,
            checked_at=self.checked_at,
            expires_at=self.expires_at,
            cached=True,
        )


def _credential_token() -> str:
    try:
        from gideon.core.config.credentials import get_credential

        return _normalise_token(get_credential(HF_CREDENTIAL_KEY))
    except Exception:
        logger.warning("Hugging Face credential-store read failed", exc_info=True)
        return ""


def _normalise_token(value: object) -> str:
    token = value.strip() if isinstance(value, str) else ""
    if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
        return ""
    return token


def huggingface_token_path() -> Path:
    """Return the standard huggingface_hub token-cache path without importing it."""
    explicit = os.environ.get("HF_TOKEN_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        return Path(hf_home).expanduser() / "token"
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    cache_home = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return cache_home / "huggingface" / "token"


def _cached_token() -> str:
    path = huggingface_token_path()
    try:
        if not path.is_file() or path.stat().st_size > _MAX_TOKEN_BYTES:
            return ""
        return _normalise_token(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        logger.debug("Hugging Face token cache unreadable", exc_info=True)
        return ""


def resolve_token() -> ResolvedToken:
    """Resolve a token in strict credential-store → environment → HF-cache order."""
    stored = _credential_token()
    if stored:
        return ResolvedToken(stored, "credential_store")
    for key in HF_ENV_KEYS:
        token = _normalise_token(os.environ.get(key))
        if token:
            return ResolvedToken(token, "environment", key)
    cached = _cached_token()
    if cached:
        return ResolvedToken(cached, "huggingface_cache")
    return ResolvedToken("", "none")


def mask_token(token: str) -> str:
    """Return a presence hint that never reveals more than the final four characters."""
    value = _normalise_token(token)
    if not value:
        return ""
    return "••••" if len(value) <= 4 else f"••••{value[-4:]}"


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validation_cache_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / VALIDATION_CACHE_FILE


def validation_ttl_secs() -> int:
    try:
        from gideon.core.config.loader import AppConfig

        return max(
            0,
            int(AppConfig.load().local_models.hf_whoami_ttl_secs),
        )
    except Exception:
        return DEFAULT_VALIDATION_TTL_SECS


def _cache_record(result: ValidationResult, fingerprint: str) -> dict[str, object]:
    return {
        "fingerprint": fingerprint,
        "state": result.state,
        "username": result.username,
        "error": result.error,
        "checked_at": result.checked_at,
        "expires_at": result.expires_at,
    }


def _decode_cache_record(
    value: object, now: float
) -> tuple[str, ValidationResult] | None:
    if not isinstance(value, dict):
        return None
    fingerprint = value.get("fingerprint")
    state = value.get("state")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
        return None
    if state not in ("valid", "invalid", "unavailable"):
        return None
    try:
        checked_at = float(value.get("checked_at", 0.0))
        expires_at = float(value.get("expires_at", 0.0))
    except (TypeError, ValueError):
        return None
    if checked_at <= 0 or expires_at <= now:
        return None
    result = ValidationResult(
        state=state,
        username=str(value.get("username") or "")[:200],
        error=str(value.get("error") or "")[:300],
        checked_at=checked_at,
        expires_at=expires_at,
    )
    return fingerprint, result


def _load_disk_cache(now: float) -> None:
    path = validation_cache_path()
    try:
        if not path.is_file() or path.stat().st_size > _MAX_CACHE_BYTES:
            return
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        logger.debug("Hugging Face validation cache unreadable", exc_info=True)
        return
    rows = document.get("entries", []) if isinstance(document, dict) else []
    if not isinstance(rows, list):
        return
    for row in rows:
        decoded = _decode_cache_record(row, now)
        if decoded is not None:
            _memory_cache[decoded[0]] = decoded[1]


def _cached_validation(token: str, now: float) -> ValidationResult | None:
    fingerprint = _fingerprint(token)
    with _cache_lock:
        result = _memory_cache.get(fingerprint)
        if result is None:
            _load_disk_cache(now)
            result = _memory_cache.get(fingerprint)
        if result is None or result.expires_at <= now:
            _memory_cache.pop(fingerprint, None)
            return None
        return result.from_cache()


def _write_validation_cache(fingerprint: str, result: ValidationResult) -> None:
    from gideon.core.atomic_write import atomic_write

    path = validation_cache_path()
    with _cache_lock:
        _memory_cache.clear()
        _memory_cache[fingerprint] = result
        payload = {"version": 1, "entries": [_cache_record(result, fingerprint)]}
        atomic_write(
            path,
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
            fsync=True,
        )


def clear_validation_cache() -> None:
    with _cache_lock:
        _memory_cache.clear()
        try:
            validation_cache_path().unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Could not clear Hugging Face validation cache", exc_info=True
            )


def _redact_text(value: object, token: str, *, limit: int) -> str:
    text = str(value or "")
    try:
        from gideon.security.security import redact_credentials

        text = redact_credentials(text)[0]
    except Exception:
        pass
    if token:
        text = text.replace(token, "[REDACTED: credential]")
    text = _HF_TOKEN_RE.sub("[REDACTED: credential]", text)
    return text[:limit]


def _safe_error(error: object, token: str) -> str:
    return _redact_text(error or "Hugging Face validation failed", token, limit=300)


async def validate_token(
    token: str,
    *,
    force: bool = False,
    ttl_secs: int | None = None,
) -> ValidationResult:
    """Validate one token through the guarded egress client and cache the result."""
    value = _normalise_token(token)
    if not value:
        return ValidationResult(state="unconfigured")
    ttl = validation_ttl_secs() if ttl_secs is None else max(0, int(ttl_secs))
    now = time.time()
    if not force and ttl > 0:
        cached = _cached_validation(value, now)
        if cached is not None:
            return cached

    from gideon.security.net import CONNECTOR, egress_policy_for, fetch

    policy = egress_policy_for(CONNECTOR).with_overrides(
        allow_only=True,
        allow_hosts=("huggingface.co",),
        max_redirects=0,
        max_bytes=64 * 1024,
        timeout_s=min(15.0, CONNECTOR.timeout_s),
    )
    try:
        response = await fetch(
            WHOAMI_URL,
            policy=policy,
            headers={
                "Authorization": f"Bearer {value}",
                "Accept": "application/json",
                "User-Agent": "gideon/huggingface-auth",
            },
        )
        username = ""
        if response.status == 200:
            try:
                body = json.loads(response.text)
            except ValueError:
                body = None
            if isinstance(body, dict) and body.get("name"):
                username = _redact_text(body.get("name"), value, limit=200)
                result = ValidationResult(state="valid", username=username)
            else:
                result = ValidationResult(
                    state="unavailable",
                    error="Hugging Face returned an invalid whoami response",
                )
        elif response.status in (401, 403):
            result = ValidationResult(
                state="invalid", error="Hugging Face rejected this token"
            )
        else:
            result = ValidationResult(
                state="unavailable",
                error=f"Hugging Face validation returned HTTP {response.status}",
            )
    except Exception as exc:
        result = ValidationResult(state="unavailable", error=_safe_error(exc, value))

    checked_at = time.time()
    result = ValidationResult(
        state=result.state,
        username=result.username,
        error=_safe_error(result.error, value) if result.error else "",
        checked_at=checked_at,
        expires_at=checked_at + ttl,
    )
    if ttl > 0:
        try:
            _write_validation_cache(_fingerprint(value), result)
        except Exception:
            logger.warning(
                "Could not persist Hugging Face validation cache", exc_info=True
            )
    return result


async def auth_status(*, force: bool = False) -> dict[str, object]:
    """Return the presence-only wire status for the currently resolved token."""
    resolved = resolve_token()
    if not resolved.token:
        return {
            "configured": False,
            "source": "none",
            "masked_token": "",
            "state": "unconfigured",
            "valid": None,
            "username": "",
            "error": "",
            "cached": False,
            "checked_at": 0.0,
            "expires_at": 0.0,
        }
    validation = await validate_token(resolved.token, force=force)
    return {
        "configured": True,
        "source": resolved.source,
        "masked_token": mask_token(resolved.token),
        "state": validation.state,
        "valid": validation.valid,
        "username": validation.username,
        "error": validation.error,
        "cached": validation.cached,
        "checked_at": validation.checked_at,
        "expires_at": validation.expires_at,
    }


def save_token(token: str) -> None:
    value = _normalise_token(token)
    if not value:
        raise ValueError("a non-empty Hugging Face token is required")
    preserve_validation = _cached_validation(value, time.time()) is not None
    from gideon.core.config.credentials import save_credential

    save_credential(HF_CREDENTIAL_KEY, value)
    if not preserve_validation:
        clear_validation_cache()


def delete_stored_token() -> bool:
    from gideon.core.config.credentials import credential_names, delete_credential

    if HF_CREDENTIAL_KEY not in credential_names():
        return False
    deleted = delete_credential(HF_CREDENTIAL_KEY)
    clear_validation_cache()
    return deleted


__all__ = [
    "AuthState",
    "HF_CREDENTIAL_KEY",
    "ResolvedToken",
    "TokenSource",
    "ValidationResult",
    "auth_status",
    "clear_validation_cache",
    "delete_stored_token",
    "huggingface_token_path",
    "mask_token",
    "resolve_token",
    "save_token",
    "validate_token",
]
