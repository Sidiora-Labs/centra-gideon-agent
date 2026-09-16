from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from gideon.automation.triggers.models import Outcome

logger = logging.getLogger(__name__)


@dataclass
class ResumeExecution:
    wakeup: Any
    started: float
    base_dir: Any
    supervisor: Callable[[], Any]
    transient_codes: frozenset[str]

    def __post_init__(self) -> None:
        self.payload = (
            self.wakeup.payload if isinstance(self.wakeup.payload, dict) else {}
        )
        self.trigger_id = str(
            self.payload.get("trigger_id") or self.wakeup.trigger_id or ""
        )
        self.run_id = str(self.payload.get("run_id") or "")

    def outcome(self, outcome: str, reason: str, reported: str = "") -> Any:
        from gideon.automation.triggers.executor import RunOutcome

        return RunOutcome(
            trigger_id=self.trigger_id,
            session_key=self.wakeup.session_key,
            outcome=outcome,
            reason=reason,
            duration_secs=round(max(0.0, time.time() - self.started), 3),
            reported=reported,
            run_id=self.run_id,
        )

    def refusal(self, run: Any) -> str:
        if run is None:
            return (
                f"the resume target {self.run_id!r} no longer exists, so there is nothing to resume; "
                "point this automation at a live run or remove its resume target"
            )
        if getattr(run, "is_terminal", False):
            return (
                f"the resume target {self.run_id!r} has finished ({getattr(run.status, 'value', '')!r}) "
                "and a run is one attempt, so it cannot be resumed"
            )
        expected = str(self.payload.get("project_id") or "")
        actual = str(getattr(run, "project_id", "") or "")
        if expected and expected != actual:
            return (
                f"the resume target {self.run_id!r} belongs to project {actual or '(none)'!r}, "
                f"not the declared {expected!r}"
            )
        return ""

    def classify(self, response: Any) -> Any:
        result = response if isinstance(response, dict) else {}
        code = str(result.get("code") or "")
        if result.get("ok"):
            report = (
                "gate answered"
                if result.get("gate_answered", True)
                else "pause cleared"
            )
            return self.outcome(Outcome.RAN.value, "", report)
        if code in self.transient_codes:
            reason = f"the resume target {self.run_id!r} is not ready yet ({code}); the next scheduled fire re-evaluates it"
            logger.info("trigger %s: %s", self.trigger_id, reason)
            return self.outcome(Outcome.DEFERRED.value, reason, code)
        reason = f"the resume of {self.run_id!r} was refused ({code or 'no code'})"
        if result.get("message"):
            reason += ": " + str(result.get("message"))
        logger.warning("trigger %s: %s", self.trigger_id, reason)
        return self.outcome(Outcome.REFUSED.value, reason, code)

    def invoke(self) -> Any:
        try:
            from gideon.automation.workflows import service, store
        except Exception as exc:
            logger.warning("resume target for %s unreachable: %r", self.trigger_id, exc)
            return self.outcome(
                Outcome.FAILED.value, f"the workflows service is unreachable: {exc!r}"
            )
        try:
            run = store.get(self.run_id)
        except Exception:
            logger.warning(
                "resume target %s could not be read", self.run_id, exc_info=True
            )
            return self.outcome(
                Outcome.FAILED.value,
                f"run {self.run_id!r} could not be read from the store",
            )
        reason = self.refusal(run)
        if reason:
            logger.warning("trigger %s: %s", self.trigger_id, reason)
            return self.outcome(Outcome.REFUSED.value, reason)
        try:
            response = service.resume_run(
                self.run_id,
                supervisor=self.supervisor(),
                token=str(self.payload.get("resume_token") or ""),
                answer=(
                    self.payload.get("gate_answer")
                    if bool(self.payload.get("answers_gate"))
                    else None
                ),
                responder=(
                    f"trigger:{self.trigger_id}" if self.trigger_id else "trigger"
                ),
            )
        except Exception as exc:
            logger.warning(
                "resume of %s raised for %s",
                self.run_id,
                self.trigger_id,
                exc_info=True,
            )
            return self.outcome(Outcome.FAILED.value, f"the resume raised: {exc!r}")
        return self.classify(response)

    def run(self) -> Any:
        from gideon.automation.triggers import executor

        try:
            return self.invoke()
        finally:
            executor.release_claim_for(self.trigger_id, base_dir=self.base_dir)
