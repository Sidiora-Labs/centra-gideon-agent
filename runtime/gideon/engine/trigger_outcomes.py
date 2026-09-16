from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from gideon.automation.triggers.models import Outcome

_REFUSAL_STATUSES = ("blocked_injection", Outcome.SKIPPED_GATE.value)


def trigger_id(trigger: Any) -> str:
    return str(getattr(trigger, "id", "") or "")


@dataclass(frozen=True)
class MissedRuns:
    total: int
    affected: int
    catch_up: int
    truncated: bool

    @classmethod
    def read(cls, report: dict[str, Any]) -> MissedRuns:
        review = report.get("review") or {}
        slots, summaries = review.get("rows") or [], review.get("summaries") or []
        count = len(slots) + sum(int(item.get("count", 0) or 0) for item in summaries)
        identities = {str(item.get("trigger_id", "")) for item in [*slots, *summaries]}
        caught = sum(
            bool(item.get("catching_up")) for item in report.get("catch_up") or []
        )
        return cls(count, len(identities), caught, bool(review.get("truncated")))

    def body(self) -> str:
        runs = "run" if self.total == 1 else "runs"
        automations = "automation" if self.affected == 1 else "automations"
        message = (
            f"{self.total} scheduled {runs} were missed across "
            f"{self.affected} {automations} while Gideon was not running. "
            "Review them and choose what to run now."
        )
        if self.catch_up:
            message += f" {self.catch_up} with catch-up enabled will fire once, staggered, on their own."
        return message

    def metadata(self) -> dict[str, Any]:
        return dict(
            event="automation.missed_review",
            statusUrl="#/triggers",
            missed=self.total,
            triggers=self.affected,
            caught_up=self.catch_up,
            truncated=self.truncated,
        )


class TriggerPublication:
    def __init__(self, runtime: Any, logger: logging.Logger) -> None:
        self.runtime, self.logger = runtime, logger

    def missed(self, report: dict[str, Any]) -> None:
        try:
            state = getattr(self.runtime, "dashboard_state", None)
            if state is None:
                return
            review = MissedRuns.read(report)
            if review.total > 0:
                state.notify(
                    kind="info",
                    title="Missed scheduled runs",
                    body=review.body(),
                    meta=review.metadata(),
                )
        except Exception:
            self.logger.debug("could not surface the missed-fire review", exc_info=True)

    def attention(self, trigger: Any, decision: Any) -> None:
        from gideon.automation.triggers import autopause

        try:
            state = getattr(self.runtime, "dashboard_state", None)
            if state is None:
                return
            card = autopause.attention_card(
                trigger_id=trigger_id(trigger),
                trigger_name=str(getattr(trigger, "name", "") or ""),
                decision=decision,
                last_error=str(getattr(trigger, "last_error_summary", "") or ""),
            )
            if card is None:
                return
            if not hasattr(self.runtime, "_attention_fingerprints"):
                self.runtime._attention_fingerprints = set()
            seen = self.runtime._attention_fingerprints
            if autopause.is_duplicate_card(card.fingerprint, seen):
                return
            state.notify(
                kind="warning",
                title=card.title,
                body=card.body,
                meta=dict(
                    event="automation.needs_attention",
                    statusUrl=f"#/triggers?open={card.trigger_id}",
                    trigger_id=card.trigger_id,
                    state=card.state,
                    actions=list(card.actions),
                ),
            )
            seen.add(card.fingerprint)
        except Exception:
            self.logger.debug(
                "could not surface the attention card for %s", trigger, exc_info=True
            )

    def next_attempt(self) -> str:
        previous = getattr(self.runtime, "_delivery_attempt_seq", 0)
        self.runtime._delivery_attempt_seq = int(previous) + 1
        return f"a{self.runtime._delivery_attempt_seq}"

    def repeated_failure(self, trigger: Any, error: str) -> bool:
        try:
            from gideon.automation.triggers.delivery import suppress_repeat_failure
            from gideon.automation.triggers.store import TriggerStore
            from gideon.core.config.loader import config_dir

            policy = getattr(trigger, "failure_policy", None)
            if not isinstance(policy, dict) or not policy.get("dedupe_hash"):
                return False
            identity = trigger_id(trigger)
            if not identity:
                return False
            store = TriggerStore(base_dir=config_dir())
            record = store.get(identity)
            current = record.trigger if record is not None else trigger
            suppress, digest = suppress_repeat_failure(
                error=error,
                last_hash=str(getattr(current, "last_alert_hash", "") or ""),
                last_at=float(getattr(current, "last_alert_at", 0.0) or 0.0),
                now=time.time(),
            )
            if not digest:
                return False
            if suppress:
                self.logger.info(
                    "trigger %s: duplicate failure suppressed (same error within the reminder window)",
                    identity,
                )
                return True
            if record is not None:
                current.last_alert_hash, current.last_alert_at = digest, time.time()
                store.upsert(current)
            return False
        except Exception:
            self.logger.debug(
                "failure dedup check failed for %s", trigger, exc_info=True
            )
            return False

    def outcome(self, trigger: Any, ok: bool, error: str) -> None:
        try:
            from gideon.automation.triggers import delivery

            state = getattr(self.runtime, "dashboard_state", None)
            if state is None:
                return
            if not hasattr(self.runtime, "_delivered_event_ids"):
                self.runtime._delivered_event_ids = set()
            if not ok and self.runtime._dedupe_repeat_failure(trigger, error=error):
                return
            notice = delivery.build_delivery(
                trigger_id=trigger_id(trigger),
                trigger_name=str(getattr(trigger, "name", "") or ""),
                ok=ok,
                summary=error[:200],
                attempt_key=self.runtime._next_delivery_attempt(),
                destination=delivery.route_for(trigger, ok=ok),
            )
            delivery.deliver(
                state, notice, delivered_ids=self.runtime._delivered_event_ids
            )
        except Exception:
            self.logger.debug(
                "could not deliver the fire outcome for %s", trigger, exc_info=True
            )


@dataclass(frozen=True)
class FireResult:
    exit_type: str
    exception_text: str
    detail: str

    @classmethod
    def read(cls, result: Any, error: BaseException | None) -> FireResult:
        from gideon.automation.triggers import autopause

        exception_text = f"{type(error).__name__}: {error}" if error is not None else ""
        if error is not None:
            exit_type = autopause.classify_exception(error)
        else:
            exit_type = autopause.ExitType.OK.value
            if result is not None and not bool(getattr(result, "success", True)):
                exit_type = autopause.ExitType.FAILED.value
        detail = exception_text or (
            str(getattr(result, "error", "") or "") if result is not None else ""
        )
        return cls(exit_type, exception_text, detail)

    def update(self, trigger: Any, decision: Any) -> None:
        from gideon.automation.triggers import autopause
        from gideon.automation.triggers.models import TriggerState

        trigger.health_status, trigger.state = decision.health, decision.state
        stamp = datetime.now(timezone.utc).isoformat()
        if self.exit_type == autopause.ExitType.OK.value:
            trigger.last_success_at = stamp
        else:
            trigger.last_failure_at = stamp
            trigger.last_error_summary = (self.detail or decision.reason)[:200]
        if autopause.needs_attention(decision.state):
            trigger.enabled = False
        trigger.park_retry_after = (
            float(decision.retry_after)
            if decision.state == TriggerState.PARKED.value
            else 0.0
        )


class FireLedger:
    def __init__(self, runtime: Any, journal_type: Any, logger: logging.Logger) -> None:
        self.runtime, self.journal_type, self.logger = runtime, journal_type, logger

    async def record(
        self, trigger: Any, result: Any, error: BaseException | None
    ) -> None:
        try:
            from gideon.automation.schedule_history import ExecutionRecord
            from gideon.automation.triggers import autopause
            from gideon.automation.triggers.models import TriggerState
            from gideon.automation.triggers.store import TriggerStore
            from gideon.core.config.loader import config_dir

            identity = trigger_id(trigger)
            if not identity:
                return
            outcome = FireResult.read(result, error)
            journal = self.journal_type(config_dir())
            now = time.time()
            await journal.append(
                ExecutionRecord(
                    run_id=f"fire-{int(now * 1000)}",
                    job_id=identity,
                    trigger=outcome.exit_type,
                    started_at=now,
                    finished_at=now,
                    status=(
                        "success"
                        if outcome.exit_type == autopause.ExitType.OK.value
                        else "failure"
                    ),
                    error=outcome.exception_text[:200],
                )
            )
            rows, _ = await journal.list_for_job(identity, 0, 20)
            decision = autopause.evaluate(
                exit_type=outcome.exit_type,
                consecutive_failures=max(
                    0, autopause.consecutive_failures_from(rows) - 1
                ),
                now=time.time(),
                budget=autopause.budget_for(trigger),
                quarantined=str(getattr(trigger, "state", ""))
                == TriggerState.QUARANTINED.value,
            )
            store = TriggerStore(base_dir=config_dir())
            stored = store.get(identity)
            if stored is None:
                return
            current = stored.trigger
            outcome.update(current, decision)
            if autopause.needs_attention(decision.state):
                self.logger.warning(
                    "trigger %s autopaused: %s",
                    identity,
                    decision.reason or decision.state,
                )
            store.upsert(current)
            self.runtime._surface_attention_card(current, decision)
        except Exception:
            self.logger.debug(
                "could not record the fire outcome for %s", trigger, exc_info=True
            )

    async def refused(self, trigger: Any, status: str, error: str) -> None:
        if status not in _REFUSAL_STATUSES:
            self.logger.error(
                "refusing to record fire status %r: not one of %s",
                status,
                _REFUSAL_STATUSES,
            )
            return
        try:
            from gideon.automation.schedule_history import ExecutionRecord
            from gideon.core.config.loader import config_dir

            now = time.time()
            entry = ExecutionRecord(
                run_id=f"{status}-{int(now * 1000)}",
                job_id=trigger_id(trigger),
                trigger=status,
                started_at=now,
                finished_at=now,
                status=status,
                error=error,
            )
            await self.journal_type(config_dir()).append(entry)
        except Exception:
            self.logger.debug(
                "could not record the refused-fire row for %s", trigger, exc_info=True
            )
