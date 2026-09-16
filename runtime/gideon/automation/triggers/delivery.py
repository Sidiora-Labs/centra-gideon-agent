"""Completion notification projection, routing and retry suppression."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

EVENT_SUCCEEDED = "automation.run.succeeded"

EVENT_FAILED = "automation.run.failed"

_FLAT_TEXT_PREFIXES = ("channel:",)

BODY_CAP = 600

_VOLATILE_RE = re.compile(
    "\\d{4}-\\d{2}-\\d{2}[T ]\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_EPOCH_RE = re.compile("\\b\\d{10,13}\\b")

_EPOCH_WINDOW_SECS = 300

FAILURE_REMINDER_SECS = 3600.0


@dataclass
class Delivery:
    event: str
    event_id: str
    title: str
    body: str
    status_url: str = ""
    trigger_id: str = ""
    run_id: str = ""
    destination: str = ""
    kind: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return EVENT_SUCCEEDED == self.event

    def to_notify_kwargs(self) -> dict[str, Any]:
        metadata = dict(
            event=self.event, statusUrl=self.status_url, eventId=self.event_id
        )
        metadata.update(self.meta)
        for key, value in (("trigger_id", self.trigger_id), ("run_id", self.run_id)):
            if value:
                metadata[key] = value
        return dict(kind=self.kind, title=self.title, body=self.body, meta=metadata)

    def to_text(self) -> str:
        return "\n".join(
            [self.title, *(value for value in (self.body, self.status_url) if value)]
        )

    def to_dict(self) -> dict[str, Any]:
        record = {
            key: getattr(self, key)
            for key in (
                "event",
                "event_id",
                "title",
                "body",
                "status_url",
                "trigger_id",
                "run_id",
                "destination",
                "kind",
                "ok",
            )
        }
        record["meta"] = dict(self.meta)
        return record


@dataclass(frozen=True)
class FailureFingerprint:
    instant: float

    def without_epoch(self, match: re.Match[str]) -> str:
        raw = match.group()
        number = int(raw)
        timestamp = number / 1000 if number > 9_999_999_999 else number
        near = (
            self.instant - _EPOCH_WINDOW_SECS
            <= timestamp
            <= self.instant + _EPOCH_WINDOW_SECS
        )
        return "" if near else raw

    def digest(self, text: str) -> str:
        normalized = _EPOCH_RE.sub(self.without_epoch, _VOLATILE_RE.sub("", text))
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]


@dataclass
class NotificationAttempt:
    state: Any
    delivery: Delivery
    delivered_ids: Any

    def permitted(self) -> bool:
        if self.state is None:
            return False
        if is_muted(self.delivery.destination):
            logger.debug(
                "delivery %s suppressed: destination is none", self.delivery.event_id
            )
            return False
        history = (
            self.delivered_ids if isinstance(self.delivered_ids, (set, list)) else None
        )
        if is_duplicate(self.delivery, history):
            logger.debug(
                "delivery %s already sent; not double-pinging", self.delivery.event_id
            )
            return False
        return True

    def send(self) -> bool:
        if not self.permitted():
            return False
        try:
            self.state.notify(**self.delivery.to_notify_kwargs())
        except Exception:
            logger.debug(
                "delivery %s could not be sent", self.delivery.event_id, exc_info=True
            )
            return False
        if isinstance(self.delivered_ids, set):
            self.delivered_ids.add(self.delivery.event_id)
        return True


def status_url(*, run_id: str = "", trigger_id: str = "") -> str:
    routes = ((run_id, "#/workflows/runs/"), (trigger_id, "#/triggers?open="))
    return next((prefix + identity for identity, prefix in routes if identity), "")


def event_id(*, trigger_id: str, run_id: str = "", attempt_key: str = "") -> str:
    identity = "|".join(part or "" for part in (trigger_id, run_id, attempt_key))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return "evt_" + digest[:20]


def _redact(text: str) -> str:
    try:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        result = text or ""
        for scrub in (redact_exfiltration_urls, redact_credentials):
            result = scrub(result)[0]
        return result
    except Exception:
        logger.debug(
            "delivery redaction failed; sending the untouched text", exc_info=True
        )
        return text or ""


def wants_flat_text(destination: str) -> bool:
    return (destination or "").strip().lower().startswith(_FLAT_TEXT_PREFIXES)


def build_delivery(
    *,
    trigger_id: str,
    trigger_name: str = "",
    ok: bool,
    summary: str = "",
    run_id: str = "",
    destination: str = "",
    attempt_key: str = "",
    duration_secs: float = 0.0,
) -> Delivery:
    from gideon.workspace import notification_kinds

    label = trigger_name or trigger_id or "automation"
    event, kind, verb = (
        (EVENT_SUCCEEDED, notification_kinds.INFO, "finished")
        if ok
        else (EVENT_FAILED, notification_kinds.ERROR, "failed")
    )
    metadata = (
        {"duration_secs": round(float(duration_secs), 3)} if duration_secs else {}
    )
    return Delivery(
        event=event,
        event_id=event_id(
            trigger_id=trigger_id, run_id=run_id, attempt_key=attempt_key
        ),
        title=_redact(f"{label} {verb}"),
        body=_redact(summary or "")[:BODY_CAP],
        status_url=status_url(run_id=run_id, trigger_id=trigger_id),
        trigger_id=trigger_id,
        run_id=run_id,
        destination=destination,
        kind=kind,
        meta=metadata,
    )


def route_for(trigger: Any, *, ok: bool) -> str:
    failure = "" if ok else str(getattr(trigger, "failure_delivery", "") or "")
    return failure or str(getattr(trigger, "delivery", "") or "none")


def failure_hash(text: str) -> str:
    return FailureFingerprint(time.time()).digest(text)


def suppress_repeat_failure(
    *, error: str, last_hash: str, last_at: float, now: float
) -> tuple[bool, str]:
    text = (error or "").strip()
    if not text:
        return False, ""
    current = failure_hash(text)
    previous_matches = bool(last_hash) and current == last_hash
    suppressed = (
        previous_matches and last_at > 0 and now - last_at < FAILURE_REMINDER_SECS
    )
    return suppressed, current


def is_muted(destination: str) -> bool:
    return "none" == str(destination or "").strip().lower()


def is_duplicate(
    delivery: Delivery, delivered_ids: set[str] | list[str] | None
) -> bool:
    return bool(delivered_ids) and delivery.event_id in set(delivered_ids or ())


def deliver(state: Any, delivery: Delivery, *, delivered_ids: Any = None) -> bool:
    return NotificationAttempt(state, delivery, delivered_ids).send()
