from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.automation.triggers import provider
from gideon.automation.triggers.models import (
    INERT_OUTCOMES,
    Outcome,
    Trigger,
    TriggerHealth,
    TriggerState,
)

logger = logging.getLogger(__name__)
MAX_SLEEP_SECS = 30.0
MIN_SLEEP_SECS = 0.5


@dataclass
class DueFire:
    trigger: Trigger
    claim: Any = None
    scheduled_for: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(
            trigger_id=self.trigger.id,
            kind=self.trigger.kind,
            scheduled_for=self.scheduled_for,
            reason=self.reason,
        )


@dataclass
class TickResult:
    fires: list[DueFire] = field(default_factory=list)
    ledger_rows: list[dict[str, Any]] = field(default_factory=list)
    next_sleep: float = MAX_SLEEP_SECS
    rescheduled: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unparked: list[str] = field(default_factory=list)
    store_changed: bool = False

    @property
    def suppressed(self) -> int:
        return sum(row.get("outcome") != Outcome.RAN.value for row in self.ledger_rows)

    def to_dict(self) -> dict[str, Any]:
        lists = {
            key: list(getattr(self, key))
            for key in ("ledger_rows", "rescheduled", "retired", "unparked")
        }
        return dict(
            fires=[fire.to_dict() for fire in self.fires],
            **lists,
            next_sleep=self.next_sleep,
            store_changed=self.store_changed,
            suppressed=self.suppressed,
        )


def to_epoch(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime

    text = str(value).strip()
    for parse in (float, lambda raw: datetime.fromisoformat(raw).timestamp()):
        try:
            return parse(text)
        except ValueError:
            continue
    logger.debug("unparseable trigger timestamp %r; treating as unset", text)
    return 0.0


def to_iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return (
        "" if epoch <= 0 else datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    )


def _interval_secs(trigger: Trigger) -> float:
    spec = trigger.spec or {}
    if str(spec.get("kind") or "") == "interval":
        try:
            return max(0.0, float(spec.get("interval_secs") or 0))
        except (TypeError, ValueError):
            pass
    return 0.0


def _created_at(trigger: Trigger) -> float:
    for name in ("created_at", "created_ts"):
        raw = getattr(trigger, name, None)
        if raw:
            stamp = to_epoch(raw)
            if stamp > 0:
                return stamp
    return 0.0


@dataclass(frozen=True)
class BootSchedule:
    now: float

    def recover(self, trigger: Trigger) -> tuple[str, float, str] | None:
        from gideon.automation.triggers.arm import next_fire
        from gideon.automation.triggers.scheduling import (
            BOOT_STAGGER_WINDOW_SECS,
            boot_recovery,
            jitter_offset,
        )

        if not trigger.enabled:
            return None
        current = to_epoch(trigger.next_fire_at)
        if current <= 0:
            armed = next_fire(trigger, now=self.now)
            return (trigger.id, armed, "armed from spec") if armed > 0 else None
        scheduled, reason = boot_recovery(
            next_fire_at=current,
            now=self.now,
            trigger_id=trigger.id,
            catch_up=bool(getattr(trigger, "catch_up", False)),
        )
        if reason == "missed_dropped":
            grid = next_fire(trigger, now=self.now)
            if grid > 0:
                scheduled = grid + jitter_offset(trigger.id, BOOT_STAGGER_WINDOW_SECS)
                reason = "missed_dropped_resumed_on_grid"
        return trigger.id, scheduled, reason


def plan_boot(triggers: list[Trigger], *, now: float) -> list[tuple[str, float, str]]:
    planner = BootSchedule(now)
    return [
        plan for trigger in triggers if (plan := planner.recover(trigger)) is not None
    ]


def due_ids(
    triggers: list[Trigger], *, now: float, window_secs: float = 1.0
) -> list[str]:
    from gideon.automation.triggers.scheduling import coalesce_wakes, is_due

    candidates = {}
    for trigger in triggers:
        stamp = to_epoch(trigger.next_fire_at) if trigger.enabled else 0.0
        if stamp <= 0:
            continue
        eligible, _ = is_due(
            next_fire_at=stamp,
            now=now,
            fires_automatically=bool(trigger.fires_automatically),
            expires_at=to_epoch(getattr(trigger, "expires_at", "")),
        )
        if eligible:
            candidates[trigger.id] = stamp
    return coalesce_wakes(candidates, now, window_secs=window_secs)


def sleep_for(triggers: list[Trigger], *, now: float) -> float:
    from gideon.automation.triggers.scheduling import next_wake_delay

    upcoming = [
        stamp
        for trigger in triggers
        if trigger.enabled and (stamp := to_epoch(trigger.next_fire_at)) > 0
    ]
    delay = next_wake_delay(upcoming, now) if upcoming else MAX_SLEEP_SECS
    return max(MIN_SLEEP_SECS, min(MAX_SLEEP_SECS, delay))


def next_after_completion(
    trigger: Trigger, *, completed_at: float, now: float
) -> float:
    from gideon.automation.triggers.arm import next_fire
    from gideon.automation.triggers.scheduling import recompute_from_completion

    interval = _interval_secs(trigger)
    if interval <= 0:
        return next_fire(trigger, now=max(now, completed_at))
    return recompute_from_completion(
        interval_secs=interval,
        created_at=_created_at(trigger) or now,
        completed_at=completed_at,
    )


@dataclass
class TickPass:
    store: Any
    now: float
    persist: bool
    user_active: bool
    base_dir: Any
    result: TickResult = field(default_factory=TickResult)

    def advance(self, trigger: Trigger) -> None:
        future = next_after_completion(trigger, completed_at=self.now, now=self.now)
        if not self.persist:
            return
        if future > 0:
            trigger.next_fire_at = to_iso(future)
            self.store.upsert(trigger)
            self.result.rescheduled.append(trigger.id)
            return
        spec = trigger.spec if isinstance(trigger.spec, dict) else {}
        if bool(spec.get("delete_after_run", False)):
            self.store.delete(trigger.id)
        else:
            trigger.next_fire_at = ""
            trigger.enabled = False
            self.store.upsert(trigger)
        self.result.retired.append(trigger.id)

    async def context_for(self, trigger: Trigger, slot_map: dict[str, str]) -> Any:
        from gideon.automation.triggers import claims, firepath, screen

        return firepath.FireContext(
            trigger_id=trigger.id,
            gates=trigger.gates or {},
            capabilities=trigger.capabilities,
            holder=f"tick:{int(self.now)}",
            overlap=str(getattr(trigger, "overlap", "skip") or "skip"),
            now=self.now,
            user_active=self.user_active,
            yield_to_user=bool(getattr(trigger, "yield_to_user", False)),
            fires_in_window=await _fires_in_window(
                trigger, now=self.now, base_dir=self.base_dir
            ),
            since_last_fire=_since_last_fire(trigger, now=self.now),
            busy_slot=claims.busy_slot(trigger, holders=slot_map),
            **_target_active_kwargs(trigger, now=self.now, base_dir=self.base_dir),
            existing_claim=claims.read_claim(
                trigger.id, now=self.now, base_dir=self.base_dir
            ),
            requested=screen.requested_capabilities(trigger),
            budget_remaining=_budget_remaining(trigger),
        )

    def grant(self, trigger: Trigger, decision: Any, scheduled_for: float) -> None:
        from gideon.automation.triggers import claims

        if self.persist and decision.claim is not None:
            claims.write_claim(decision.claim, base_dir=self.base_dir)
        trigger.run_count = int(getattr(trigger, "run_count", 0) or 0) + 1
        trigger.last_fired_at = to_iso(self.now)
        if self.persist and trigger.id not in self.result.retired:
            self.store.upsert(trigger)
        self.result.fires.append(DueFire(trigger, decision.claim, scheduled_for, "due"))

    async def consider(self, trigger: Trigger, slot_map: dict[str, str]) -> None:
        from gideon.automation.triggers import firepath
        from gideon.automation.triggers.missed import late_outcome

        scheduled_for = to_epoch(trigger.next_fire_at)
        self.advance(trigger)
        context = await self.context_for(trigger, slot_map)
        decision = await firepath.evaluate(context)
        row = firepath.ledger_row(decision, context)
        row["scheduled_for"] = scheduled_for
        row["outcome"], reason = late_outcome(
            row["outcome"], scheduled_for=scheduled_for, started_at=self.now
        )
        if reason:
            row["reason"] = reason
        self.result.ledger_rows.append(row)
        if decision.allowed:
            self.grant(trigger, decision, scheduled_for)
        elif self.persist:
            await _persist_suppression(row, now=self.now, base_dir=self.base_dir)

    async def run(self) -> TickResult:
        from gideon.automation.triggers import claims

        self.result.store_changed = bool(
            getattr(self.store, "changed_on_disk", lambda: False)()
        )
        triggers = provider.armable(self.store)
        by_id = {trigger.id: trigger for trigger in triggers}
        slots = claims.slot_holders(self.store, now=self.now, base_dir=self.base_dir)
        self.result.unparked.extend(
            _unpark_ready(self.store, triggers, now=self.now, persist=self.persist)
        )
        for trigger_id in due_ids(triggers, now=self.now):
            trigger = by_id.get(trigger_id)
            if trigger is not None:
                await self.consider(trigger, slots)
        self.result.next_sleep = sleep_for(list(by_id.values()), now=self.now)
        return self.result


async def tick(
    store: Any,
    *,
    now: float = 0.0,
    persist: bool = True,
    user_active: bool = False,
    base_dir: Any = None,
) -> TickResult:
    from gideon.automation.triggers.routing import routed

    current = routed(store)
    root = getattr(current, "base_dir", None) if base_dir is None else base_dir
    return await TickPass(current, now or time.time(), persist, user_active, root).run()


def _run_store(base_dir: Any) -> Any:
    from gideon.automation.schedule_history import ExecutionJournal
    from gideon.core.config.loader import config_dir

    root = config_dir() if base_dir is None else Path(base_dir)
    return ExecutionJournal(root)


async def _persist_suppression(
    row: dict[str, Any], *, now: float, base_dir: Any = None
) -> None:
    try:
        from gideon.automation.schedule_history import ExecutionRecord

        outcome, trigger_id = (
            str(row.get(key) or "") for key in ("outcome", "trigger_id")
        )
        if not trigger_id or outcome not in INERT_OUTCOMES:
            return
        record = ExecutionRecord(
            run_id=f"skip-{int(now * 1000)}",
            job_id=trigger_id,
            trigger=outcome,
            started_at=now,
            finished_at=now,
            status=outcome,
            error=str(row.get("reason") or ""),
        )
        journal = _run_store(base_dir)
        await journal.append(record)
    except Exception:
        logger.debug(
            "could not persist the suppression row for %s", row.get("trigger_id")
        )


async def _fires_in_window(
    trigger: Any, *, now: float, base_dir: Any = None
) -> int | None:
    gates = getattr(trigger, "gates", None)
    if not isinstance(gates, dict) or not any(
        gates.get(key)
        for key in ("rate_cap", "max_runs_per_hour", "max_actions_per_hour")
    ):
        return None
    try:
        journal = _run_store(base_dir)
        return await journal.count_since(trigger.id, now - 3600.0)
    except Exception:
        logger.debug(
            "could not read the rate window for %s", getattr(trigger, "id", "?")
        )
        return None


def _unpark_ready(
    store: Any, triggers: list[Any], *, now: float, persist: bool
) -> list[str]:
    from gideon.automation.triggers import autopause

    changed = []
    for trigger in triggers:
        if str(getattr(trigger, "state", "")) != TriggerState.PARKED.value:
            continue
        retry_after = float(getattr(trigger, "park_retry_after", 0.0) or 0.0)
        if autopause.unpark_due(retry_after=retry_after, now=now):
            trigger.state = TriggerState.ACTIVE.value
            trigger.health_status = TriggerHealth.OK.value
            trigger.park_retry_after = 0.0
            changed.append(trigger.id)
            if persist:
                store.upsert(trigger)
    if changed:
        logger.info(
            "unparked %d trigger(s) whose cooldown elapsed: %s", len(changed), changed
        )
    return changed


def _since_last_fire(trigger: Any, *, now: float) -> float | None:
    stamp = to_epoch(str(getattr(trigger, "last_fired_at", "") or ""))
    return max(0.0, now - stamp) if stamp > 0 else None


def _target_active_kwargs(trigger: Any, *, now: float, base_dir: Any) -> dict[str, Any]:
    from gideon.automation.triggers.liveness import is_target_active

    state = is_target_active(
        getattr(trigger, "skip_if_active", None), now=now, base_dir=base_dir
    )
    return dict(zip(("target_active", "target_active_reason"), state))


def _budget_remaining(trigger: Any) -> float | None:
    gates = getattr(trigger, "gates", None)
    gates = gates if isinstance(gates, dict) else {}
    try:
        cap = int(gates.get("max_fires", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if cap <= 0:
        return None
    return float(max(0, cap - int(getattr(trigger, "run_count", 0) or 0)))


@dataclass
class BootPass:
    store: Any
    now: float
    persist: bool

    def run(self) -> dict[str, Any]:
        from gideon.automation.triggers.missed import review_at_boot

        triggers = provider.armable(self.store)
        review = review_at_boot(
            [trigger.to_dict() for trigger in triggers], now=self.now
        )
        catch_up = catch_up_at_boot(triggers, now=self.now)
        by_id: dict[str, Trigger] = {}
        for trigger in triggers:
            by_id.setdefault(trigger.id, trigger)
        rearmed = []
        for identity, new_at, reason in plan_boot(triggers, now=self.now):
            selected = by_id.get(identity)
            if selected is None or new_at == to_epoch(selected.next_fire_at):
                continue
            selected.next_fire_at = to_iso(new_at)
            if self.persist:
                self.store.upsert(selected)
            rearmed.append(dict(id=identity, next_fire_at=new_at, reason=reason))
        return dict(
            rearmed=rearmed,
            total=len(triggers),
            review=review.to_dict() if hasattr(review, "to_dict") else {},
            catch_up=catch_up,
            next_sleep=sleep_for(triggers, now=self.now),
        )


def boot(store: Any, *, now: float = 0.0, persist: bool = True) -> dict[str, Any]:
    from gideon.automation.triggers.routing import routed

    return BootPass(routed(store), now or time.time(), persist).run()


def catch_up_at_boot(triggers: list[Trigger], *, now: float) -> list[dict[str, Any]]:
    from gideon.automation.triggers.missed import catch_up_plan

    plan = catch_up_plan([trigger.to_dict() for trigger in triggers], now=now)
    return [
        dict(id=identity, fire_at=stamp, reason=reason, catching_up=stamp > 0)
        for identity, stamp, reason in plan
    ]


def drain_spooled_fires(*, limit: int = 500) -> tuple[list[Any], int]:
    from gideon.automation.triggers.dispatch import drain_spool

    return drain_spool(limit=limit)
