from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from gideon.automation.triggers.models import FIRE_OUTCOMES, Outcome

logger = logging.getLogger(__name__)
STATUS_PENDING = "_pending"
MAX_DRAIN = 50
STATUS_TO_OUTCOME: dict[str, str] = {
    "ok": Outcome.RAN.value,
    "success": Outcome.RAN.value,
    "done": Outcome.RAN.value,
    "report": Outcome.RAN.value,
    "skip": Outcome.SKIPPED_NOOP.value,
    "launched": Outcome.DEFERRED.value,
    "queued": Outcome.DEFERRED.value,
    "needs_input": Outcome.DEFERRED.value,
    "error": Outcome.FAILED.value,
    "failure": Outcome.FAILED.value,
    "timeout": Outcome.FAILED.value,
    "refused": Outcome.REFUSED.value,
    "blocked": Outcome.REFUSED.value,
}


def _release_claim(trigger_id: str, *, base_dir: Any = None) -> bool:
    if base_dir is not None:
        from gideon.automation.triggers.claims import release_claim

        return release_claim(trigger_id, base_dir=base_dir)
    return False


def release_claim_for(trigger_id: str, *, base_dir: Any = None) -> bool:
    try:
        return _release_claim(trigger_id, base_dir=base_dir)
    except Exception:
        logger.debug("claim release failed for %s", trigger_id, exc_info=True)
        return False


@dataclass
class RunOutcome:
    trigger_id: str
    session_key: str
    outcome: str
    reason: str = ""
    duration_secs: float = 0.0
    reported: str = ""
    run_id: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == Outcome.RAN.value

    @property
    def settled(self) -> bool:
        return self.outcome != Outcome.DEFERRED.value

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self), ok=self.ok, settled=self.settled)


@dataclass
class DrainResult:
    session_key: str
    outcomes: list[RunOutcome] = field(default_factory=list)
    truncated: bool = False
    skipped: int = 0

    @property
    def ran(self) -> int:
        return sum(row.ok for row in self.outcomes)

    @property
    def failed(self) -> int:
        return sum(row.outcome == Outcome.FAILED.value for row in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["outcomes"] = [row.to_dict() for row in self.outcomes]
        return dict(
            document, total=len(self.outcomes), ran=self.ran, failed=self.failed
        )


@dataclass(frozen=True)
class StatusVerdict:
    reported: str
    exception: BaseException | None = None

    def resolve(self) -> tuple[str, str]:
        if self.exception is not None:
            from gideon.automation.triggers.dispatch import classify_handler_outcome

            result = classify_handler_outcome(self.exception, self.reported or "")
            reason = f"{type(self.exception).__name__}: {self.exception}"[:200]
            return (result if result in FIRE_OUTCOMES else Outcome.FAILED.value), reason

        status = (self.reported or "").strip().lower()
        if status in ("", STATUS_PENDING):
            return Outcome.RAN.value, ""
        outcome = STATUS_TO_OUTCOME.get(status)
        if outcome is None:
            return Outcome.FAILED.value, f"unrecognized runner status {status!r}"
        if outcome == Outcome.RAN.value:
            return outcome, ""
        if outcome == Outcome.DEFERRED.value:
            reason = {
                "queued": "the run was queued behind a run already in flight; it starts when that one ends"
            }.get(status, "action launched background work; outcome not yet known")
            return outcome, reason
        if outcome == Outcome.SKIPPED_NOOP.value:
            return (
                outcome,
                "the action ran and had nothing to do; nothing durable changed",
            )
        return outcome, f"runner reported {status}"


def classify(reported: str, exception: BaseException | None = None) -> tuple[str, str]:
    return StatusVerdict(reported, exception).resolve()


def _payload_of(row: Any) -> dict[str, Any]:
    if isinstance(row, tuple) and len(row) >= 3 and isinstance(row[2], dict):
        candidate = row[2].get("wakeup")
        if isinstance(candidate, dict):
            return candidate
    return {}


@dataclass
class RunAttempt:
    payload: dict[str, Any]
    session_key: str
    started: float
    release: Any
    base_dir: Any

    async def execute(
        self, runner: Callable[[dict[str, Any]], Awaitable[Any]]
    ) -> RunOutcome:
        identity = str(self.payload.get("trigger_id") or "")
        receipt = {"reported": "", "run_id": ""}
        failure = None
        try:
            response = await runner(self.payload)
            for target, key in (("reported", "status"), ("run_id", "run_id")):
                value = (
                    response.get(key)
                    if isinstance(response, dict)
                    else getattr(
                        response, "last_status" if key == "status" else key, ""
                    )
                )
                receipt[target] = str(value or "")
        except Exception as exc:
            failure = exc
        finally:
            if identity and self.release is not None:
                try:
                    self.release(identity, base_dir=self.base_dir)
                except Exception:
                    logger.debug(
                        "could not release claim for %s", identity, exc_info=True
                    )
        outcome, reason = classify(receipt["reported"], failure)
        return RunOutcome(
            trigger_id=identity,
            session_key=self.session_key or str(self.payload.get("session_key") or ""),
            outcome=outcome,
            reason=reason,
            duration_secs=round(max(0.0, time.time() - self.started), 3),
            **receipt,
        )


async def run_one(
    payload: dict[str, Any],
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    session_key: str = "",
    now: float = 0.0,
    release_claim: Any = _release_claim,
    base_dir: Any = None,
) -> RunOutcome:
    attempt = RunAttempt(
        payload, session_key, now or time.time(), release_claim, base_dir
    )
    return await attempt.execute(runner)


@dataclass
class InboxDrain:
    sessions: Any
    result: DrainResult
    limit: int
    now: float
    base_dir: Any

    def take(self) -> Any:
        return self.sessions.dequeue(self.result.session_key)

    async def execute(
        self, runner: Callable[[dict[str, Any]], Awaitable[Any]]
    ) -> DrainResult:
        if self.sessions is None:
            return self.result
        budget = max(1, self.limit)
        while budget:
            budget -= 1
            try:
                row = self.take()
            except Exception:
                logger.debug(
                    "dequeue failed for %s", self.result.session_key, exc_info=True
                )
                break
            if row is None:
                return self.result
            envelope = _payload_of(row)
            if envelope.get("trigger_id"):
                receipt = await run_one(
                    envelope.get("payload") or envelope,
                    runner,
                    session_key=self.result.session_key,
                    now=self.now,
                    base_dir=self.base_dir,
                )
                self.result.outcomes.append(receipt)
            else:
                self.result.skipped += 1
        try:
            self.result.truncated = self.take() is not None
        except Exception:
            self.result.truncated = False
        return self.result


async def drain(
    sessions: Any,
    session_key: str,
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    limit: int = MAX_DRAIN,
    now: float = 0.0,
    base_dir: Any = None,
) -> DrainResult:
    inbox = InboxDrain(sessions, DrainResult(session_key), limit, now, base_dir)
    return await inbox.execute(runner)


def delivery_for(
    outcome: RunOutcome, *, trigger_name: str = "", destination: str = ""
) -> Any:
    if outcome.settled:
        from gideon.automation.triggers.delivery import build_delivery

        return build_delivery(
            trigger_id=outcome.trigger_id,
            trigger_name=trigger_name,
            ok=outcome.ok,
            summary=outcome.reason,
            run_id=outcome.run_id,
            destination=destination,
            duration_secs=outcome.duration_secs,
        )
    return None


@dataclass(frozen=True)
class ExecutionSummary:
    result: DrainResult

    def ledger(self) -> list[dict[str, Any]]:
        columns = (
            "trigger_id",
            "outcome",
            "reason",
            "duration_secs",
            "reported",
            "run_id",
        )
        return [
            dict({key: getattr(row, key) for key in columns}, phase="execute")
            for row in self.result.outcomes
        ]

    def health(self) -> dict[str, Any]:
        counts = Counter(row.outcome for row in self.result.outcomes if row.settled)
        settled = sum(counts.values())
        return {
            "settled": settled,
            "succeeded": counts[Outcome.RAN.value],
            "failed": counts[Outcome.FAILED.value],
            "deferred": len(self.result.outcomes) - settled,
            "consecutive_failures": counts[Outcome.FAILED.value],
        }


def ledger_rows(result: DrainResult) -> list[dict[str, Any]]:
    return ExecutionSummary(result).ledger()


def health_delta(result: DrainResult) -> dict[str, Any]:
    return ExecutionSummary(result).health()
