from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)
KEY_PREFIX_TRIGGER = "cron:"
KEY_PREFIX_LOOP = "loop-"
RESUME_TARGET_KEY = "resume"


class WakeKind(str, Enum):
    WAKE = "wake"
    RESUME = "resume"


class Disposition(str, Enum):
    QUEUED = "queued"
    SKIPPED_RUNNING = "skipped_running"
    REQUEUED = "requeued"
    NO_SESSION = "no_session"
    RESUME_TARGET = "resume_target"


def session_key_for(trigger_id: str, *, session: str = "") -> str:
    fallback = KEY_PREFIX_TRIGGER + trigger_id.removeprefix("schedule:")
    prefix, separator, target = (session or "").strip().partition(":")
    return (target or fallback) if separator and prefix == "conversation" else fallback


@dataclass
class Wakeup:
    kind: str
    trigger_id: str
    session_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    emitted_at: float = 0.0

    @property
    def droppable(self) -> bool:
        from gideon.automation.triggers.dispatch import droppable

        return droppable(self.kind)

    def to_dict(self) -> dict[str, Any]:
        values = vars(self)
        return {
            key: values[key]
            for key in ("kind", "trigger_id", "session_key", "seq", "emitted_at")
        } | {
            "payload": dict(self.payload),
            "droppable": self.droppable,
        }


@dataclass
class Delivery:
    disposition: str
    wakeup: Wakeup
    reason: str = ""

    @property
    def delivered(self) -> bool:
        return self.disposition == Disposition.QUEUED.value

    @property
    def needs_retry(self) -> bool:
        return self.disposition == Disposition.REQUEUED.value

    def to_dict(self) -> dict[str, Any]:
        status = {"disposition": self.disposition, "reason": self.reason}
        status.update(delivered=self.delivered, needs_retry=self.needs_retry)
        status.update(self.wakeup.to_dict())
        return status


@dataclass(frozen=True)
class ResumeTarget:
    run_id: str
    project_id: str
    resume_token: str
    gate_answer: Any
    answers_gate: bool

    @classmethod
    def read(cls, trigger: Any) -> ResumeTarget | None:
        workflow = getattr(trigger, "workflow", None)
        raw = workflow.get(RESUME_TARGET_KEY) if isinstance(workflow, dict) else None
        if not isinstance(raw, dict):
            return None
        names = {
            key: str(raw.get(key, "") or "").strip()
            for key in ("run_id", "project_id", "resume_token")
        }
        return (
            cls(**names, gate_answer=raw.get("answer"), answers_gate="answer" in raw)
            if names["run_id"]
            else None
        )


def resume_target_of(trigger: Any) -> dict[str, Any]:
    target = ResumeTarget.read(trigger)
    if target is None:
        return {}
    return {
        name: getattr(target, name)
        for name in (
            "run_id",
            "project_id",
            "resume_token",
            "gate_answer",
            "answers_gate",
        )
    }


@dataclass(frozen=True)
class WakeupFactory:
    seq: int
    now: float

    def resume(
        self, trigger_id: str, session_key: str, answer: dict[str, Any]
    ) -> Wakeup:
        return Wakeup(
            WakeKind.RESUME.value,
            trigger_id,
            session_key,
            dict(answer),
            self.seq,
            self.now or time.time(),
        )

    def from_fire(self, fire: Any) -> Wakeup:
        trigger = getattr(fire, "trigger", None)
        trigger_id = str(
            getattr(trigger, "id", "") or getattr(fire, "trigger_id", "") or ""
        )
        target = resume_target_of(trigger)
        if target:
            return self.resume(trigger_id, "", dict(trigger_id=trigger_id, **target))
        fields = {
            "trigger_id": trigger_id,
            "kind": str(getattr(trigger, "kind", "") or ""),
            "scheduled_for": float(getattr(fire, "scheduled_for", 0) or 0),
            "reason": str(getattr(fire, "reason", "") or ""),
        }
        binding = str(getattr(trigger, "session", "") or "")
        return Wakeup(
            WakeKind.WAKE.value,
            trigger_id,
            session_key_for(trigger_id, session=binding),
            fields,
            self.seq,
            self.now or time.time(),
        )


def wakeup_for(fire: Any, *, seq: int = 0, now: float = 0.0) -> Wakeup:
    return WakeupFactory(seq, now).from_fire(fire)


def resume_for(
    *,
    trigger_id: str,
    session_key: str,
    answer: dict[str, Any],
    seq: int = 0,
    now: float = 0.0,
) -> Wakeup:
    return WakeupFactory(seq, now).resume(trigger_id, session_key, answer)


def is_running(sessions: Any, key: str) -> bool:
    try:
        session = getattr(sessions, "_sessions", {}).get(key)
    except Exception:
        return False
    if session is None:
        return False
    semaphore = getattr(session, "semaphore", None)
    if semaphore is None:
        return False
    try:
        return bool(semaphore.locked())
    except Exception:
        return False


@dataclass(frozen=True)
class InboxSignal:
    sessions: Any
    wakeup: Wakeup

    def result(self, disposition: Disposition, reason: str = "") -> Delivery:
        return Delivery(disposition.value, self.wakeup, reason)

    def enqueue(self) -> bool:
        try:
            return bool(
                self.sessions.enqueue(
                    self.wakeup.session_key,
                    _msg_ts(self.wakeup),
                    _text(self.wakeup),
                    force=True,
                    wakeup=self.wakeup.to_dict(),
                )
            )
        except Exception:
            logger.debug(
                "wakeup enqueue raised for %s", self.wakeup.session_key, exc_info=True
            )
            return False

    def send(self) -> Delivery:
        if self.sessions is None:
            return self.result(Disposition.NO_SESSION, "no session manager")
        if self.wakeup.droppable and is_running(self.sessions, self.wakeup.session_key):
            return self.result(
                Disposition.SKIPPED_RUNNING,
                "session is already running and will drain its own inbox",
            )
        if self.enqueue():
            return self.result(Disposition.QUEUED)
        if self.wakeup.droppable:
            return self.result(
                Disposition.NO_SESSION,
                "no session for this key; create it or spool the payload",
            )
        return self.result(
            Disposition.REQUEUED, "session not ready; a resume is never dropped"
        )


def deliver(sessions: Any, wakeup: Wakeup) -> Delivery:
    return InboxSignal(sessions, wakeup).send()


def _msg_ts(wakeup: Wakeup) -> str:
    identity = (
        wakeup.kind,
        wakeup.trigger_id,
        str(wakeup.seq or int(wakeup.emitted_at)),
    )
    return ":".join(identity)


def _text(wakeup: Wakeup) -> str:
    return "[{}:{}]".format(wakeup.kind, wakeup.trigger_id)


def deliver_all(sessions: Any, wakeups: list[Wakeup]) -> list[Delivery]:
    return list(map(lambda signal: deliver(sessions, signal), wakeups))


@dataclass(frozen=True)
class FireDispatch:
    sessions: Any
    now: float

    def send(self, position: int, fire: Any) -> Delivery:
        signal = wakeup_for(fire, seq=position, now=self.now)
        if signal.kind != WakeKind.RESUME.value:
            return deliver(self.sessions, signal)
        return Delivery(
            Disposition.RESUME_TARGET.value,
            signal,
            "names a parked run; the loop resumes it rather than queueing an inbox wake",
        )


def dispatch_fires(
    sessions: Any, fires: list[Any], *, now: float = 0.0
) -> list[Delivery]:
    dispatcher = FireDispatch(sessions, now or time.time())
    return [
        dispatcher.send(index, fire) for index, fire in enumerate(fires or [], start=1)
    ]


def retry_queue(deliveries: list[Delivery]) -> list[Wakeup]:
    return list(
        map(lambda row: row.wakeup, filter(lambda row: row.needs_retry, deliveries))
    )


def summary(deliveries: list[Delivery]) -> dict[str, Any]:
    observed = Counter(row.disposition for row in deliveries)
    counts = {kind.value: observed[kind.value] for kind in Disposition}
    return dict(
        total=len(deliveries),
        delivered=counts[Disposition.QUEUED.value],
        by_disposition=counts,
        retry=counts[Disposition.REQUEUED.value],
    )
