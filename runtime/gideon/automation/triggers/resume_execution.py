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
                "event gate woken"
                if result.get("woken")
                else "gate answered"
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
        payload_trigger_id = str(self.payload.get("trigger_id") or "")
        wakeup_trigger_id = str(getattr(self.wakeup, "trigger_id", "") or "")
        if not payload_trigger_id or payload_trigger_id != wakeup_trigger_id:
            return self.outcome(
                Outcome.REFUSED.value,
                "the wake-up trigger identity does not match its payload",
            )
        try:
            from gideon.automation.triggers import grants
            from gideon.automation.triggers.store import TriggerStore
            from gideon.automation.triggers.wakeup import resume_target_of
            from gideon.automation.workflows.human_input import (
                Ask,
                AskKind,
                list_continuations,
            )

            row = TriggerStore(base_dir=self.base_dir).get(payload_trigger_id)
            if row is None or not row.ok or not row.trigger.enabled:
                return self.outcome(
                    Outcome.REFUSED.value,
                    "the current trigger is missing, invalid, or disabled",
                )
            trigger = row.trigger
            action_revision = grants.action_revision(trigger)
            if not action_revision or not grants.is_granted(trigger):
                return self.outcome(
                    Outcome.REFUSED.value,
                    "the current trigger action does not have its owner grant",
                )
            target = resume_target_of(trigger)
            if (
                not target
                or target.get("run_id") != self.run_id
                or target.get("run_id") != self.payload.get("run_id")
                or target.get("project_id", "")
                != str(self.payload.get("project_id") or "")
                or target.get("resume_token", "")
                != str(self.payload.get("resume_token") or "")
                or target.get("answers_gate") is not True
                or self.payload.get("answers_gate") is not True
                or not service.json_values_equal(
                    target.get("gate_answer"), self.payload.get("gate_answer")
                )
            ):
                return self.outcome(
                    Outcome.REFUSED.value,
                    "the wake-up payload no longer matches the trigger's saved resume target",
                )
            pending = list_continuations(self.run_id)
            declared_token = str(target.get("resume_token") or "")
            if declared_token:
                matching = [item for item in pending if item.token == declared_token]
                if len(matching) != 1 or Ask.from_dict(matching[0].ask).kind != AskKind.EVENT:
                    return self.outcome(
                        Outcome.REFUSED.value,
                        "the declared resume token does not name one pending event gate",
                    )
            else:
                event_pending = [
                    item
                    for item in pending
                    if Ask.from_dict(item.ask).kind == AskKind.EVENT
                ]
                if len(event_pending) != 1:
                    return self.outcome(
                        Outcome.REFUSED.value,
                        "a token-less target needs exactly one pending event gate",
                    )
                matching = event_pending
            continuation = matching[0]
            if continuation.run_id != self.run_id:
                return self.outcome(
                    Outcome.REFUSED.value,
                    "the pending event gate belongs to another run",
                )
            event_wake = service.ScheduledEventWake(
                trigger_id=payload_trigger_id,
                action_revision=action_revision,
                run_id=self.run_id,
                resume_token=continuation.token,
                node_id=continuation.node_id,
                declared_resume_token=declared_token,
                answer=target.get("gate_answer"),
                project_id=str(target.get("project_id") or ""),
            )
            response = service.resume_run(
                self.run_id,
                supervisor=self.supervisor(),
                token=event_wake.resume_token,
                answer=event_wake.answer,
                responder=f"trigger:{payload_trigger_id}",
                scheduled_event_wake=event_wake,
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
            executor.release_claim_for(self.trigger_id, base_dir=self.base_dir, holder=str(self.payload.get("claim_holder") or ""))
