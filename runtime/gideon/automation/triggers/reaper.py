"""Release overdue execution claims and record their degraded health."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.automation.triggers.models import TriggerHealth

logger = logging.getLogger(__name__)
REAPER_INTERVAL_SECS = 60.0
RUN_DEADLINE_SECS = 1800.0


@dataclass(frozen=True)
class ClaimRetirement:
    identity: str
    elapsed: float

    def mark_health(self, store: Any) -> bool:
        if store is None:
            return False
        try:
            row = store.get(self.identity)
            if row is None:
                return False
            trigger = row.trigger
            trigger.health_status = TriggerHealth.DEGRADED.value
            trigger.last_error_summary = f"Reaped after {int(self.elapsed)}s (exceeded {int(RUN_DEADLINE_SECS)}s deadline)"
            store.upsert(trigger)
        except Exception:
            logger.debug(
                "Reaper: could not record the reap for %s", self.identity, exc_info=True
            )
            return False
        return True

    def audit(self) -> None:
        try:
            from gideon.security.sel import sel

            sel().log_tool_invocation(
                session_key=f"cron:{self.identity}",
                source="cron",
                tool_name="reaper_force_kill",
                outcome="reaped",
                metadata={"job_id": self.identity, "elapsed": int(self.elapsed)},
            )
        except Exception:
            logger.debug(
                "Reaper: SEL audit failed for %s", self.identity, exc_info=True
            )

    def apply(self, store: Any, base_dir: Path | str | None) -> dict[str, Any]:
        from gideon.automation.triggers import claims

        released = claims.release_claim(self.identity, base_dir=base_dir)
        receipt = dict(
            trigger_id=self.identity,
            elapsed=int(self.elapsed),
            released=bool(released),
            deadline_secs=int(RUN_DEADLINE_SECS),
            recorded=False,
        )
        logger.warning(
            "Reaper: trigger %s exceeded %ds (ran %.0fs), releasing its claim",
            self.identity,
            int(RUN_DEADLINE_SECS),
            self.elapsed,
        )
        receipt["recorded"] = self.mark_health(store)
        self.audit()
        return receipt


def overdue(
    *,
    now: float = 0.0,
    deadline_secs: float = RUN_DEADLINE_SECS,
    base_dir: Path | str | None = None,
) -> list[tuple[str, float]]:
    from gideon.automation.triggers import claims

    instant = now or time.time()
    found = []
    for identity in claims.running_ids(now=instant, base_dir=base_dir):
        started = claims.running_since(identity, now=instant, base_dir=base_dir)
        if started is not None and instant - started > deadline_secs:
            found.append((identity, instant - started))
    found.sort()
    return found


def reap_one(
    trigger_id: str,
    elapsed: float,
    *,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    return ClaimRetirement(trigger_id, elapsed).apply(store, base_dir)


def sweep_once(
    *,
    store: Any = None,
    now: float = 0.0,
    deadline_secs: float = RUN_DEADLINE_SECS,
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    candidates = overdue(now=now, deadline_secs=deadline_secs, base_dir=base_dir)
    return [
        reap_one(identity, elapsed, store=store, now=now, base_dir=base_dir)
        for identity, elapsed in candidates
    ]


async def run_forever(
    *,
    store: Any = None,
    base_dir: Path | str | None = None,
    interval_secs: float = REAPER_INTERVAL_SECS,
) -> None:
    async def sweep() -> None:
        try:
            await asyncio.to_thread(sweep_once, store=store, base_dir=base_dir)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("trigger reaper sweep failed; continuing", exc_info=True)

    while True:
        await asyncio.sleep(interval_secs)
        await sweep()
