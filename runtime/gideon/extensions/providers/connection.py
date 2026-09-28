"""Measured model-provider connection state, cached without blocking settings reads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
CHECK_TTL_SECS = 15 * 60.0
CHECK_TIMEOUT_SECS = 30.0
CHECKING = "checking"
CONNECTED = "connected"
FAILED = "failed"
UNTESTABLE = "untestable"


@dataclass(frozen=True)
class Connection:
    state: str
    detail: str = ""
    rejected_credential: bool = False
    checked_at: float | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "rejected_credential": self.rejected_credential,
            "checked_at": self.checked_at,
        }


_CHECKING = Connection(CHECKING)


def settings_fingerprint(provider_type: str, options: dict[str, Any] | None) -> str:
    safe_options = {k: v for k, v in (options or {}).items() if not str(k).startswith("_")}
    raw = json.dumps({"type": provider_type, "options": safe_options}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def entry_fingerprint(entry: Any) -> str:
    from gideon.integrations.llm.registry import canonical_provider_type

    return settings_fingerprint(canonical_provider_type(entry.type), entry.options)


async def measure(catalog: Any) -> Connection:
    if catalog is None:
        return Connection(UNTESTABLE, "This provider type has no connection test.", checked_at=time.time())
    try:
        result = await asyncio.wait_for(catalog.test_connection(), timeout=CHECK_TIMEOUT_SECS)
    except TimeoutError:
        return Connection(FAILED, f"The connection test got no answer within {CHECK_TIMEOUT_SECS:.0f} s.", checked_at=time.time())
    except Exception as exc:  # noqa: BLE001
        from gideon.extensions.providers.failure_copy import relayed_failure_copy

        logger.debug("connection test raised", exc_info=True)
        return Connection(FAILED, relayed_failure_copy(exc), checked_at=time.time())
    if result.ok and (result.model_count is None or result.model_count > 0):
        detail = result.detail or (
            f"Connected — {result.model_count} model(s) available"
            if result.model_count is not None else "Connected"
        )
        return Connection(CONNECTED, detail, checked_at=time.time())
    return Connection(FAILED, result.detail or "The connection test failed without saying why.", checked_at=time.time())


class ConnectionBoard:
    def __init__(self, *, ttl_secs: float = CHECK_TTL_SECS) -> None:
        self._ttl = ttl_secs
        self._answers: dict[tuple[str, str], tuple[Connection, float]] = {}
        self._checks: dict[tuple[str, str], asyncio.Task[None]] = {}

    def read(self, name: str, fingerprint: str, catalog: Callable[[], Any]) -> Connection:
        key = (name, fingerprint)
        hit = self._answers.get(key)
        if hit is not None and time.monotonic() - hit[1] < self._ttl:
            return hit[0]
        if key not in self._checks or self._checks[key].done():
            task = asyncio.get_running_loop().create_task(self._check(key, catalog))
            self._checks[key] = task
        return hit[0] if hit is not None else _CHECKING

    def record(self, name: str, fingerprint: str, answer: Connection) -> None:
        self._answers[(name, fingerprint)] = (answer, time.monotonic())

    async def _check(self, key: tuple[str, str], catalog: Callable[[], Any]) -> None:
        try:
            try:
                built = catalog()
            except Exception as exc:  # noqa: BLE001
                from gideon.extensions.providers.failure_copy import relayed_failure_copy

                answer = Connection(FAILED, relayed_failure_copy(exc), checked_at=time.time())
            else:
                answer = await measure(built)
            self.record(*key, answer)
        finally:
            self._checks.pop(key, None)

    def forget(self, name: str) -> None:
        for key in [key for key in self._answers if key[0] == name]:
            del self._answers[key]
        for key in [key for key in self._checks if key[0] == name]:
            task = self._checks.pop(key)
            if not task.done():
                task.cancel()


_board: ConnectionBoard | None = None


def get_connection_board() -> ConnectionBoard:
    global _board
    if _board is None:
        _board = ConnectionBoard()
    return _board


__all__ = ["CHECKING", "CONNECTED", "FAILED", "UNTESTABLE", "Connection", "ConnectionBoard", "entry_fingerprint", "get_connection_board", "measure", "settings_fingerprint"]
