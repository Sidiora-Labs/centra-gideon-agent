"""Bounded runtime status for Gideon's Hypermid adapter."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from .models import Cursor, SessionAccepted

_MODES = frozenset({"off", "pass_through", "shadow", "primary"})
_AVAILABILITY = frozenset(
    {"disabled", "starting", "ready", "unavailable", "draining", "stopped"}
)
_DIGEST_STATES = frozenset({"unknown", "healthy", "broken"})
_LEASE_STATES = frozenset({"none", "held", "expired", "unknown"})
_MAX_FAILURE_MESSAGE = 240


def _mode(value: object) -> str:
    raw = getattr(value, "value", value)
    result = str(raw)
    if result not in _MODES:
        raise ValueError(f"unsupported Hypermid mode {result!r}")
    return result


def _bounded_text(value: object, *, limit: int = 160) -> str | None:
    if not isinstance(value, (str, int, float, bool)):
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _description_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    operations = getattr(value, "operations", None)
    if operations is None:
        return {}
    return {
        "daemon_instance_id": getattr(value, "daemon_instance_id", None),
        "protocol": getattr(value, "protocol", None),
        "operations": operations,
        "health": getattr(value, "health", None),
        "build_version": getattr(value, "build_version", None),
        "storage_version": getattr(value, "storage_version", None),
    }


def _cursor(value: object) -> Cursor | None:
    if isinstance(value, Cursor):
        return value
    if isinstance(value, Mapping):
        try:
            return Cursor.from_wire(value)
        except ValueError:
            return None
    return None


@dataclass(frozen=True, slots=True)
class HypermidStatus:
    mode: str = "off"
    availability: str = "disabled"
    available: bool = False
    healthy: bool = False
    writer: str = "gideon"
    scope_bound: bool = False
    protocol_version: str | None = None
    storage_version: str | None = None
    build_version: str | None = None
    capabilities: tuple[str, ...] = ()
    cursor: Cursor | None = None
    lease_state: str = "none"
    digest_health: str = "unknown"
    daemon_instance_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    checked_at_ms: int = 0

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError("invalid Hypermid mode")
        if self.availability not in _AVAILABILITY:
            raise ValueError("invalid Hypermid availability")
        if self.writer not in ("gideon", "hypermid"):
            raise ValueError("invalid Hypermid writer")
        if self.lease_state not in _LEASE_STATES:
            raise ValueError("invalid Hypermid lease state")
        if self.digest_health not in _DIGEST_STATES:
            raise ValueError("invalid Hypermid digest health")
        if self.writer == "hypermid" and self.mode != "primary":
            raise ValueError("Hypermid can write only in primary mode")
        if self.writer == "hypermid" and self.lease_state != "held":
            raise ValueError("Hypermid writer status requires a held lease")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        cursor = self.cursor
        result["cursor"] = cursor.to_wire() if cursor is not None else None
        result["capabilities"] = list(self.capabilities)
        return result


class HypermidStatusStore:
    """Thread-safe last-known status that never contains credentials or content."""

    def __init__(self, mode: object = "off") -> None:
        self._lock = threading.RLock()
        self._storage_writable = False
        self._status = HypermidStatus(mode=_mode(mode), checked_at_ms=self._now())

    @staticmethod
    def _now() -> int:
        return time.time_ns() // 1_000_000

    def snapshot(self) -> HypermidStatus:
        with self._lock:
            return self._status

    def disabled(self, mode: object = "off") -> HypermidStatus:
        return self._set(
            HypermidStatus(mode=_mode(mode), checked_at_ms=self._now())
        )

    def starting(self, mode: object) -> HypermidStatus:
        return self._set(
            HypermidStatus(
                mode=_mode(mode),
                availability="starting",
                writer="gideon",
                checked_at_ms=self._now(),
            )
        )

    def ready(
        self,
        mode: object,
        session: SessionAccepted,
        description: object,
        *,
        daemon_instance_id: str | None,
    ) -> HypermidStatus:
        raw = _description_mapping(description)
        health = _safe_mapping(raw.get("health"))
        checks = _safe_mapping(health.get("checks"))
        digest_check = _safe_mapping(checks.get("digest_chain"))
        digest_value = raw.get("digest_health", digest_check.get("value"))
        if digest_value is True:
            digest_health = "healthy"
        elif digest_value is False:
            digest_health = "broken"
        else:
            digest_health = str(digest_value or "unknown").lower()
            if digest_check.get("state") == "available" and digest_health in (
                "intact",
                "valid",
                "live",
            ):
                digest_health = "healthy"
            elif digest_health in ("corrupt", "corrupted", "invalid"):
                digest_health = "broken"
            if digest_health not in _DIGEST_STATES:
                digest_health = "unknown"
        lease_raw = raw.get("lease")
        lease = _safe_mapping(lease_raw)
        lease_state = str(
            lease.get("state", raw.get("lease_state", "none")) or "none"
        ).lower()
        if lease_state not in _LEASE_STATES:
            lease_state = "unknown"
        cursor = _cursor(raw.get("cursor"))
        capabilities_raw = raw.get("operations", raw.get("capabilities", ()))
        capabilities = tuple(
            sorted(
                {
                    item
                    for item in capabilities_raw
                    if isinstance(item, str) and 0 < len(item) <= 160
                }
            )
        ) if isinstance(capabilities_raw, (list, tuple, set, frozenset)) else ()
        writer = str(raw.get("writer", "gideon"))
        active_mode = _mode(mode)
        if active_mode != "primary" or lease_state != "held":
            writer = "gideon"
        if writer not in ("gideon", "hypermid"):
            writer = "gideon"
        storage = _safe_mapping(checks.get("storage"))
        storage_value = _safe_mapping(storage.get("value"))
        storage_writable = raw.get(
            "storage_writable", storage.get("writable", storage_value.get("writable"))
        )
        self._storage_writable = storage_writable is True
        healthy = digest_health == "healthy"
        if active_mode == "primary":
            healthy = healthy and storage_writable is True and writer == "hypermid"
        status = HypermidStatus(
            mode=active_mode,
            availability="ready",
            available=True,
            healthy=healthy,
            writer=writer,
            scope_bound=True,
            protocol_version=_bounded_text(
                raw.get("protocol_version", raw.get("protocol"))
            ),
            storage_version=_bounded_text(raw.get("storage_version")),
            build_version=_bounded_text(raw.get("build_version")),
            capabilities=capabilities,
            cursor=cursor,
            lease_state=lease_state,
            digest_health=digest_health,
            daemon_instance_id=_bounded_text(daemon_instance_id),
            checked_at_ms=self._now(),
        )
        return self._set(status)

    def mode_changed(self, mode: object) -> HypermidStatus:
        active_mode = _mode(mode)
        with self._lock:
            current = self._status
            if active_mode == "off":
                return self.disabled(active_mode)
            writer = current.writer if active_mode == "primary" else "gideon"
            lease_state = current.lease_state if writer == "hypermid" else "none"
            healthy = current.available and current.digest_health == "healthy"
            if active_mode == "primary":
                healthy = (
                    healthy
                    and self._storage_writable
                    and writer == "hypermid"
                    and lease_state == "held"
                )
            return self._set(
                replace(
                    current,
                    mode=active_mode,
                    healthy=healthy,
                    writer=writer,
                    lease_state=lease_state,
                    checked_at_ms=self._now(),
                )
            )

    def authority(self, mode: object, snapshot: object) -> HypermidStatus:
        active_mode = _mode(mode)
        writer = str(getattr(snapshot, "writer", "gideon"))
        lease_state = str(getattr(snapshot, "lease_state", "none"))
        lease = getattr(snapshot, "lease", None)
        if active_mode != "primary" or writer != "hypermid" or lease_state != "held":
            writer = "gideon"
            lease_state = "none" if lease_state != "unknown" else "unknown"
            lease = None
        cursor = getattr(lease, "cursor", None)
        with self._lock:
            current = self._status
            healthy = current.available and current.digest_health == "healthy"
            if active_mode == "primary":
                healthy = (
                    healthy
                    and self._storage_writable
                    and writer == "hypermid"
                    and lease_state == "held"
                )
            return self._set(
                replace(
                    current,
                    mode=active_mode,
                    healthy=healthy,
                    writer=writer,
                    lease_state=lease_state,
                    cursor=cursor if isinstance(cursor, Cursor) else current.cursor,
                    checked_at_ms=self._now(),
                )
            )

    def unavailable(
        self,
        mode: object,
        error: BaseException | str,
        *,
        code: str | None = None,
    ) -> HypermidStatus:
        failure_code = code or type(error).__name__.upper()
        failure_code = "".join(
            character if character.isalnum() or character == "_" else "_"
            for character in failure_code
        )[:64] or "UNAVAILABLE"
        message = " ".join(str(error).split())[:_MAX_FAILURE_MESSAGE]
        return self._set(
            HypermidStatus(
                mode=_mode(mode),
                availability="unavailable",
                writer="gideon",
                failure_code=failure_code,
                failure_message=message,
                checked_at_ms=self._now(),
            )
        )

    def draining(self) -> HypermidStatus:
        with self._lock:
            return self._set(
                replace(
                    self._status,
                    availability="draining",
                    healthy=False,
                    checked_at_ms=self._now(),
                )
            )

    def stopped(self) -> HypermidStatus:
        with self._lock:
            return self._set(
                replace(
                    self._status,
                    availability="stopped",
                    available=False,
                    healthy=False,
                    scope_bound=False,
                    writer="gideon",
                    lease_state="none",
                    daemon_instance_id=None,
                    checked_at_ms=self._now(),
                )
            )

    def _set(self, status: HypermidStatus) -> HypermidStatus:
        with self._lock:
            self._status = status
            return status


__all__ = ["HypermidStatus", "HypermidStatusStore"]
