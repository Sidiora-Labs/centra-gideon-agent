"""Server-owned enrollment grants for the native Hypermid runtime."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .models import JsonValue, Scope

_NATIVE_OPERATIONS = ("read", "append", "revise", "delete", "restore", "administer")
_NATIVE_RESOURCES = ("memory-records", "memory-list", "memory-service", "memory-embedding")
_NATIVE_GRANT_LIFETIME_MS = 30 * 24 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class NativeEnrollmentSource:
    """Issue the fixed least-privilege grant needed by Gideon's local runtime."""

    grant_lifetime_ms: int = _NATIVE_GRANT_LIFETIME_MS

    def __post_init__(self) -> None:
        if self.grant_lifetime_ms <= 0:
            raise ValueError("native enrollment lifetime must be positive")

    def issue(
        self, *, scope: Scope, now_ms: int | None = None
    ) -> dict[str, JsonValue]:
        if not isinstance(scope, Scope):
            raise TypeError("native enrollment requires an exact scope")
        issued_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        if isinstance(issued_ms, bool) or not isinstance(issued_ms, int) or issued_ms < 0:
            raise ValueError("native enrollment issue time is invalid")
        expires_ms = issued_ms + self.grant_lifetime_ms
        return {
            "operations": list(_NATIVE_OPERATIONS),
            "resources": list(_NATIVE_RESOURCES),
            "expires_ms": expires_ms,
        }


def native_enrollment_source() -> NativeEnrollmentSource:
    return NativeEnrollmentSource()


__all__ = ["NativeEnrollmentSource", "native_enrollment_source"]
