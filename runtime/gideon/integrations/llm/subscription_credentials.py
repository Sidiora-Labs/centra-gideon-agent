"""Read application-declared sign-in stores without taking ownership of them."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_EXPIRY_UNITS = ("ms", "s")


@dataclass(frozen=True)
class SubscriptionSource:
    id: str
    login_hint: str
    credential_files: tuple[str, ...] = ()
    token_path: tuple[str, ...] = ()
    expires_at_path: tuple[str, ...] = ()
    expires_at_unit: str = "ms"

    def validate(self) -> list[str]:
        requirements = (
            (bool(self.id.strip()), "id is required"),
            (
                bool(self.login_hint.strip()),
                "login_hint is required (the user must be told how to sign in)",
            ),
            (
                bool(self.credential_files),
                "credential_files must name at least one path",
            ),
            (bool(self.token_path), "token_path must name at least one key"),
            (
                self.expires_at_unit in _EXPIRY_UNITS,
                f"expires_at_unit must be one of {list(_EXPIRY_UNITS)}",
            ),
        )
        return [message for valid, message in requirements if not valid]


@dataclass(frozen=True)
class SubscriptionAuth:
    source: str
    logged_in: bool
    reason: str = ""
    secret: str = field(default="", repr=False)


_SOURCES: dict[str, SubscriptionSource] = {}


def register_subscription_source(source: SubscriptionSource) -> None:
    failures = source.validate()
    if failures:
        raise ValueError(
            f"invalid SubscriptionSource {source.id!r}: {'; '.join(failures)}"
        )
    _SOURCES.update({source.id.strip(): source})


def _walk(payload: Any, keys: tuple[str, ...]) -> Any:
    cursor = payload
    for key in keys:
        if not isinstance(cursor, dict) or key not in cursor:
            return None
        cursor = cursor[key]
    return cursor


def _expired(payload: Any, source: SubscriptionSource, *, now: float) -> bool:
    if not source.expires_at_path:
        return False
    value = _walk(payload, source.expires_at_path)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    try:
        deadline = float(value) / (1000 if source.expires_at_unit == "ms" else 1)
    except (TypeError, ValueError):
        return False
    return deadline <= now


def _inspect_store(
    source: SubscriptionSource, location: str, now: float
) -> SubscriptionAuth:
    def unavailable(detail: str) -> SubscriptionAuth:
        return SubscriptionAuth(
            source.id, False, f"{source.id} {detail} — {source.login_hint}"
        )

    path = Path(os.path.expanduser(os.path.expandvars(location)))
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return unavailable("is not signed in on this machine")
    except UnicodeError:
        return unavailable(
            f"credential store at {location} is unreadable (malformed or half-written)"
        )
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        return unavailable(
            f"credential store at {location} is unreadable (malformed or half-written)"
        )
    token = _walk(document, source.token_path)
    if not isinstance(token, str) or not token.strip():
        return unavailable(f"credential store at {location} holds no sign-in token")
    if _expired(document, source, now=now):
        return unavailable("sign-in has expired")
    return SubscriptionAuth(source.id, True, secret=token.strip())


def resolve_subscription_credential(source_id: str) -> SubscriptionAuth:
    key = str(source_id or "").strip()
    if not key:
        return SubscriptionAuth("", False, "no credential source declared")
    source = _SOURCES.get(key)
    if source is None:
        return SubscriptionAuth(
            key,
            False,
            (
                f"no {key!r} subscription credential source is registered "
                "(its provider app is not installed or not enabled)"
            ),
        )
    first_rejection = None
    observed_at = time.time()
    for location in source.credential_files:
        result = _inspect_store(source, location, observed_at)
        if result.logged_in:
            return result
        if first_rejection is None:
            first_rejection = result
    return first_rejection or SubscriptionAuth(
        source.id, False, f"{source.id} is not signed in — {source.login_hint}"
    )


def subscription_source_status(source_id: str) -> tuple[bool, str]:
    result = resolve_subscription_credential(source_id)
    return result.logged_in, "" if result.logged_in else result.reason


__all__ = [
    "SubscriptionAuth",
    "SubscriptionSource",
    "register_subscription_source",
    "resolve_subscription_credential",
    "subscription_source_status",
]
