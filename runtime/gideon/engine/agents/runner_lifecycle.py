"""Runner ownership views over the shared durable claim store."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.automation.workflows.containers import Claim

logger = logging.getLogger(__name__)
RUNNER_LEASE_PREFIX = "runner:"
IDLE_RELEASE_MIN_SECS = 60
IDLE_RELEASE_MAX_SECS = 86_400


def lease_target(runtime_id: str) -> str:
    return "".join((RUNNER_LEASE_PREFIX, runtime_id))


def idle_release_secs() -> int:
    try:
        from gideon.core.config.loader import AppConfig

        seconds = int(getattr(AppConfig.load().agent, "runner_idle_release_secs", 1800))
    except Exception:
        logger.debug(
            "runner idle-release TTL unreadable; using the default", exc_info=True
        )
        return 1800
    return min(max(seconds, IDLE_RELEASE_MIN_SECS), IDLE_RELEASE_MAX_SECS)


def durable_sessions_enabled() -> bool:
    try:
        from gideon.core.config.loader import AppConfig

        requested = bool(getattr(AppConfig.load().agent, "durable_sessions", False))
    except Exception:
        logger.debug("durable_sessions flag unreadable; treating as off", exc_info=True)
        return False
    if requested:
        from gideon.engine import tmux_substrate

        return tmux_substrate.tmux_available()
    return False


@dataclass(frozen=True)
class _RunnerLease:
    runtime: str

    @property
    def target(self):
        return lease_target(self.runtime)

    def take(self, holder, ttl):
        from gideon.automation.workflows import leases

        duration = idle_release_secs() if ttl is None else ttl
        return leases.acquire_claim(self.target, holder, ttl=int(duration))

    def drop(self, holder):
        from gideon.automation.workflows import leases

        return leases.release_claim(self.target, holder)

    def read(self):
        from gideon.automation.workflows import leases

        return leases.read_claim(self.target)

    def view(self, now):
        record = self.read()
        if record is None:
            return None
        timestamp = time.time() if now is None else now
        if record.expired(timestamp):
            return None
        details = dict(
            holder=record.holder,
            taken_at=record.taken_at,
            expires_at=record.expires_at,
            renewals=record.renewals,
        )
        details.update(
            age_secs=max(0, int(timestamp - record.taken_at)) if record.taken_at else 0,
            expires_in_secs=max(0, int(record.expires_at - timestamp)),
        )
        return details

    def expire(self, timestamp):
        record = self.read()
        if record is None or not record.expired(timestamp):
            return False
        _, refusal = self.drop(record.holder)
        if refusal:
            logger.debug(
                "runner lease sweep: %s not released (%s)", self.runtime, refusal
            )
            return False
        duration = (
            int(record.expires_at - record.taken_at)
            if record.taken_at
            else idle_release_secs()
        )
        logger.info(
            "runner lifecycle: released %s — holder %s idle past its %ds lease",
            self.runtime,
            record.holder,
            duration,
        )
        return True


def claim_runner(
    runtime_id: str, holder: str, *, ttl: int | None = None
) -> tuple["Claim | None", str]:
    return (
        _RunnerLease(runtime_id).take(holder, ttl)
        if runtime_id and holder
        else (None, "no holder")
    )


def release_runner(runtime_id: str, holder: str) -> tuple["Claim | None", str]:
    return (
        _RunnerLease(runtime_id).drop(holder)
        if runtime_id and holder
        else (None, "no holder")
    )


def lease_for(runtime_id: str, *, now: float | None = None) -> dict | None:
    return _RunnerLease(runtime_id).view(now) if runtime_id else None


def sweep_idle_leases(*, now: float | None = None) -> list[str]:
    timestamp = time.time() if now is None else now
    try:
        from gideon.automation.workflows import leases
        from gideon.engine.agents import runners
    except Exception:
        logger.debug("runner lease sweep: catalog unavailable", exc_info=True)
        return []
    try:
        names = sorted(
            {row.runtime_id for row in runners.catalog().values() if row.runtime_id}
        )
    except Exception:
        logger.debug("runner lease sweep: catalog read failed", exc_info=True)
        return []
    return [name for name in names if _RunnerLease(name).expire(timestamp)]
