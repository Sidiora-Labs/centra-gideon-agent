from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)
MAX_ITERATION_SLEEP_SECS = 60.0
MAX_PENDING_RESUMES = 200
_RESUME_NOT_YET: frozenset[str] = frozenset({"WF_RUN_NOT_LIVE", "WF_NO_PENDING_GATE"})
Runner = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class ClockDriver:
    store: Any
    runner: Runner
    sessions: Any
    base_dir: Any
    user_active: Callable[[], bool] | None
    pending: list[Any] = field(default_factory=list)

    async def advance(self) -> float:
        try:
            result = await tick_once(
                self.store,
                runner=self.runner,
                sessions=self.sessions,
                base_dir=self.base_dir,
                user_active=bool(self.user_active()) if self.user_active else False,
                pending_resumes=self.pending,
            )
            return min(max(0.5, float(result.next_sleep)), MAX_ITERATION_SLEEP_SECS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("clock tick failed; continuing", exc_info=True)
            return MAX_ITERATION_SLEEP_SECS

    async def run(self) -> None:
        while True:
            await asyncio.sleep(await self.advance())


async def run_forever(
    store: Any,
    *,
    runner: Runner,
    sessions: Any = None,
    base_dir: Any = None,
    user_active: Callable[[], bool] | None = None,
) -> None:
    driver = ClockDriver(store, runner, sessions, base_dir, user_active)
    await driver.run()


@dataclass
class ClockCycle:
    store: Any
    runner: Runner
    sessions: Any
    base_dir: Any
    now: float
    user_active: bool
    pending: list[Any] | None

    async def run(self) -> Any:
        from gideon.automation.triggers import service, wakeup

        result = await service.tick(
            self.store,
            now=self.now,
            base_dir=self.base_dir,
            user_active=self.user_active,
        )
        activity = [
            _drain_spool(now=self.now),
            _retry_pending_resumes(self.sessions, self.pending),
        ]
        activity.append(
            await _poll_idle(
                self.store,
                self.sessions,
                self.runner,
                now=self.now,
                base_dir=self.base_dir,
            )
        )
        if not result.fires:
            if any(activity):
                logger.debug(
                    "idle tick drained %d spooled, retried %d resumes, fired %d idle",
                    *activity,
                )
            return result
        if self.sessions is None:
            logger.debug(
                "clock tick produced %d fires with no session manager",
                len(result.fires),
            )
            return result
        deliveries = wakeup.dispatch_fires(self.sessions, result.fires, now=self.now)
        summary = wakeup.summary(deliveries)
        no_session = int(
            (summary.get("by_disposition") or {}).get("no_session", 0) or 0
        )
        if no_session:
            logger.debug(
                "clock dispatch: %d wakeups have no session inbox (run directly)",
                no_session,
            )
        _hold_resumes(wakeup, deliveries, self.pending)
        for delivery in deliveries:
            try:
                await _execute_delivery(
                    delivery,
                    self.runner,
                    sessions=self.sessions,
                    now=self.now,
                    base_dir=self.base_dir,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "execution failed for %s", delivery.wakeup.trigger_id, exc_info=True
                )
        return result


async def tick_once(
    store: Any,
    *,
    runner: Runner,
    sessions: Any = None,
    base_dir: Any = None,
    user_active: bool = False,
    now: float = 0.0,
    pending_resumes: list[Any] | None = None,
) -> Any:
    cycle = ClockCycle(
        store, runner, sessions, base_dir, now, user_active, pending_resumes
    )
    return await cycle.run()


@dataclass
class DeliveryExecution:
    delivery: Any
    runner: Runner
    sessions: Any
    now: float
    base_dir: Any

    async def run(self) -> list[Any]:
        from gideon.automation.triggers import executor, wakeup

        signal = self.delivery.wakeup
        disposition = self.delivery.disposition
        if disposition == wakeup.Disposition.RESUME_TARGET.value:
            return [await _apply_resume(signal, now=self.now, base_dir=self.base_dir)]
        if disposition == wakeup.Disposition.QUEUED.value:
            result = await executor.drain(
                self.sessions,
                signal.session_key,
                self.runner,
                now=self.now,
                base_dir=self.base_dir,
            )
            return list(result.outcomes)
        if disposition == wakeup.Disposition.NO_SESSION.value:
            outcome = await executor.run_one(
                signal.payload,
                self.runner,
                session_key=signal.session_key,
                now=self.now,
                base_dir=self.base_dir,
            )
            logger.info(
                "clock fire for %s ran without a session inbox: %s",
                signal.trigger_id,
                outcome.outcome,
            )
            return [outcome]
        released = executor.release_claim_for(signal.trigger_id, base_dir=self.base_dir)
        logger.debug(
            "clock fire for %s not executed (%s); claim released=%s",
            signal.trigger_id,
            disposition,
            released,
        )
        return []


async def _execute_delivery(
    delivery: Any,
    runner: Runner,
    *,
    sessions: Any,
    now: float = 0.0,
    base_dir: Any = None,
) -> list[Any]:
    execution = DeliveryExecution(delivery, runner, sessions, now, base_dir)
    return await execution.run()


def _supervisor() -> Any:
    try:
        from gideon.integrations.action_providers.services import get_action_services

        services = get_action_services()
    except Exception:
        return None
    return getattr(services, "workflows", None) if services else None


async def _apply_resume(wakeup: Any, *, now: float = 0.0, base_dir: Any = None) -> Any:
    from gideon.automation.triggers.resume_execution import ResumeExecution

    execution = ResumeExecution(
        wakeup, now or time.time(), base_dir, _supervisor, _RESUME_NOT_YET
    )
    return execution.run()


async def _poll_idle(
    store: Any,
    sessions: Any,
    runner: Runner,
    *,
    now: float = 0.0,
    base_dir: Any = None,
) -> int:
    from gideon.automation.triggers import idle_poll

    try:
        result = await idle_poll.poll(
            store, sessions, runner, now=now, base_dir=base_dir
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("idle poll failed; continuing the tick", exc_info=True)
        return 0
    delivered, skipped = result
    for trigger_id, reason in (
        (row.get("trigger_id"), row.get("reason")) for row in skipped
    ):
        logger.debug("idle %s did not fire: %s", trigger_id, reason)
    return delivered


def _reenter_spooled(envelope: Any, *, now: float) -> tuple[str, str]:
    from gideon.automation.triggers.spool_replay import EventReentry

    return EventReentry(envelope, now).run()


def _drain_spool(*, now: float = 0.0) -> int:
    from gideon.automation.triggers.spool_replay import SpoolReplay

    return SpoolReplay(now, _reenter_spooled).run()


@dataclass
class ResumeBacklog:
    pending: list[Any]

    def hold(self, wakeup: Any, deliveries: list[Any]) -> None:
        try:
            waiting = wakeup.retry_queue(deliveries)
        except Exception:
            logger.debug("retry_queue raised", exc_info=True)
            return
        if not waiting:
            return
        self.pending.extend(waiting)
        excess = len(self.pending) - MAX_PENDING_RESUMES
        if excess > 0:
            logger.warning(
                "resume retry queue full (%d); dropping the %d oldest",
                MAX_PENDING_RESUMES,
                excess,
            )
            del self.pending[:excess]
        logger.info("holding %d resume(s) for the next tick", len(waiting))

    def retry(self, sessions: Any) -> int:
        from gideon.automation.triggers import wakeup

        try:
            deliveries = wakeup.deliver_all(sessions, list(self.pending))
        except Exception:
            logger.debug("resume retry failed; keeping the queue", exc_info=True)
            return 0
        remaining = []
        delivered = 0
        for delivery in deliveries:
            if getattr(delivery, "delivered", False):
                delivered += 1
            else:
                remaining.append(delivery.wakeup)
        self.pending[:] = remaining
        if delivered:
            logger.info("re-delivered %d held resume(s)", delivered)
        return delivered


def _hold_resumes(wk: Any, deliveries: list[Any], pending: list[Any] | None) -> None:
    if pending is not None:
        ResumeBacklog(pending).hold(wk, deliveries)


def _retry_pending_resumes(sessions: Any, pending: list[Any] | None) -> int:
    if pending and sessions is not None:
        return ResumeBacklog(pending).retry(sessions)
    return 0
