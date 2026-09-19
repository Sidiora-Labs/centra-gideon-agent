from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from gideon.automation.triggers.models import (
    FIRE_OUTCOMES,
    INERT_OUTCOMES,
    FireRecord,
    Outcome,
    RunWeight,
)

logger = logging.getLogger(__name__)

SCHEDULE_STATUS_TO_OUTCOME: dict[str, str] = {
    "success": Outcome.RAN.value,
    "failure": Outcome.FAILED.value,
    "timeout": Outcome.FAILED.value,
    "launched": Outcome.DEFERRED.value,
    "queued": Outcome.DEFERRED.value,
    "blocked_injection": Outcome.BLOCKED_INJECTION.value,
    **{value: value for value in sorted(INERT_OUTCOMES)},
}

LEDGER_WEIGHT_OUTCOMES: frozenset[str] = frozenset(
    {Outcome.DEFERRED.value, Outcome.BLOCKED_INJECTION.value} | set(INERT_OUTCOMES)
)

HOOK_STATUS_TO_OUTCOME: dict[str, str] = {
    "ok": Outcome.RAN.value,
    "success": Outcome.RAN.value,
    "error": Outcome.FAILED.value,
    "failure": Outcome.FAILED.value,
    "timeout": Outcome.FAILED.value,
    "blocked": Outcome.REFUSED.value,
    "advisory": Outcome.RAN.value,
    "queued": Outcome.DEFERRED.value,
    "launched": Outcome.DEFERRED.value,
    "skipped_incident": Outcome.SKIPPED_GATE.value,
    "held_for_rung": Outcome.SKIPPED_GATE.value,
}


def _redact(text: str) -> str:
    try:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        current = text or ""
        for redact in (redact_exfiltration_urls, redact_credentials):
            current, _ = redact(current)
        return current
    except Exception:
        logger.debug("history redaction unavailable", exc_info=True)
        return text or ""


def _iso(ts: Any) -> str:
    if ts and not isinstance(ts, str):
        try:
            stamp = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            return stamp.isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            return ""
    return ts if ts else ""


def _weight(outcome: str) -> str:
    return (
        RunWeight.LEDGER if outcome in LEDGER_WEIGHT_OUTCOMES else RunWeight.FULL
    ).value


def _mapped_status(
    status: str,
    mapping: dict[str, str],
    table: str,
    label: str,
    *,
    empty: str = Outcome.FAILED.value,
) -> str:
    if status in mapping:
        return mapping[status]
    if not status:
        return empty
    logger.warning(
        "%s status %r is not in %s; recorded as %s. Map it in triggers/history.py — a status this build cannot classify is shown as a failure.",
        label,
        status,
        table,
        Outcome.FAILED.value,
    )
    return Outcome.FAILED.value


@dataclass(frozen=True)
class ScheduleProjection:
    source: dict[str, Any]
    trigger_id: str

    def reason(self, status: str, outcome: str) -> str:
        reason = str(self.source.get("error") or "") or str(
            self.source.get("summary") or ""
        )
        if status == "timeout":
            return f"timed out: {reason}" if reason else "timed out"
        if reason:
            return reason
        explanations = {
            "launched": "action launched a background turn; outcome not yet known",
            "queued": "queued behind a run already in flight; it starts when that one ends",
        }
        if status in explanations:
            return explanations[status]
        if outcome == Outcome.BLOCKED_INJECTION.value:
            return "payload blocked by the injection screen; never retried"
        if outcome in INERT_OUTCOMES:
            return f"suppressed: {status.replace('_', ' ')}"
        return ""

    def project(self) -> FireRecord:
        source = self.source
        status = str(source.get("status", "") or "")
        outcome = _mapped_status(
            status, SCHEDULE_STATUS_TO_OUTCOME, "SCHEDULE_STATUS_TO_OUTCOME", "run"
        )
        job_id, run_id = (
            str(source.get(key, "") or "") for key in ("job_id", "run_id")
        )
        return FireRecord(
            id=run_id,
            trigger_id=self.trigger_id or (f"schedule:{job_id}" if job_id else ""),
            outcome=outcome,
            reason=_redact(self.reason(status, outcome))[:200],
            weight=_weight(outcome),
            started_at=_iso(source.get("started_at")),
            finished_at=_iso(source.get("finished_at")),
            duration_secs=round(float(source.get("duration_ms") or 0) / 1000.0, 3),
            run_id=run_id,
        )


def schedule_run_to_record(run: dict[str, Any], *, trigger_id: str = "") -> FireRecord:
    return ScheduleProjection(run or {}, trigger_id).project()


def _hook_reason(status: str, outcome: str) -> str:
    if status == "advisory":
        return "reported only: the hook asked to block, and the tool it objected to already ran"
    if outcome == Outcome.RAN.value:
        return ""
    descriptions = {
        "launched": "action launched a background turn; outcome not yet known",
        "queued": "queued behind a run already in flight; it starts when that one ends",
        "skipped_incident": "suppressed: incident mode is active; automated actions resume when it clears",
    }
    return descriptions.get(
        status, f"hook last reported {status or 'an unknown status'}"
    )


@dataclass(frozen=True)
class CounterProjection:
    source: Any
    count_field: str
    time_field: str

    def values(self) -> tuple[int, Any]:
        return int(getattr(self.source, self.count_field, 0) or 0), getattr(
            self.source, self.time_field, None
        )

    def hook(self) -> FireRecord | None:
        count, latest = self.values()
        if count <= 0 and not latest:
            return None
        status = str(getattr(self.source, "last_status", "") or "")
        outcome = _mapped_status(
            status.lower(),
            HOOK_STATUS_TO_OUTCOME,
            "HOOK_STATUS_TO_OUTCOME",
            "hook",
            empty=Outcome.RAN.value if latest else Outcome.FAILED.value,
        )
        identity = "lifecycle:" + str(getattr(self.source, "id", "") or "")
        timestamp = _iso(latest)
        return FireRecord(
            id=identity + ":last",
            trigger_id=identity,
            outcome=outcome,
            reason=_redact(_hook_reason(status, outcome)),
            weight=_weight(outcome),
            started_at=timestamp,
            finished_at=timestamp,
            run_id="",
            counters={"run_count": count},
            incomplete=count > 1,
        )

    def event(self) -> FireRecord | None:
        count, latest = self.values()
        if count <= 0 and not latest:
            return None
        identity = "event:" + str(getattr(self.source, "id", "") or "")
        timestamp = _iso(latest)
        return FireRecord(
            id=identity + ":summary",
            trigger_id=identity,
            outcome=Outcome.RAN.value,
            reason=f"{count} fire(s) recorded; this store keeps a counter, not per-fire rows",
            weight=RunWeight.LEDGER.value,
            started_at=timestamp,
            finished_at=timestamp,
            counters={"fire_count": count},
            incomplete=True,
        )


def hook_to_record(hook: Any) -> FireRecord | None:
    return CounterProjection(hook, "run_count", "last_run").hook()


def event_trigger_to_record(trigger: Any) -> FireRecord | None:
    return CounterProjection(trigger, "fire_count", "last_fired_at").event()


@dataclass
class HistoryFeed:
    records: list[FireRecord]

    def add(
        self,
        sources: list[Any],
        project: Callable[[Any], FireRecord | None],
        label: str,
    ) -> None:
        for source in sources:
            try:
                record = project(source)
            except Exception:
                logger.debug("could not project %s", label, exc_info=True)
                continue
            if record is not None:
                self.records.append(record)

    def latest(self, limit: int) -> list[FireRecord]:
        timed, undated = [], []
        for record in self.records:
            (timed if record.started_at else undated).append(record)
        timed.sort(key=lambda row: (row.started_at, row.id), reverse=True)
        undated.sort(key=lambda row: row.id)
        return (timed + undated)[: max(1, limit)]

    def partition(self) -> tuple[list[FireRecord], list[FireRecord]]:
        buckets: tuple[list[FireRecord], list[FireRecord]] = ([], [])
        for record in self.records:
            buckets[int(is_inert(record))].append(record)
        return buckets

    def response(self, total: int | None) -> dict[str, Any]:
        rows = [row.to_dict() for row in self.records]
        did, suppressed = partition_inert(self.records)
        return {
            "runs": rows,
            "total": len(rows) if total is None else total,
            "kinds": sorted(
                {
                    row.trigger_id.partition(":")[0]
                    for row in self.records
                    if row.trigger_id
                }
            ),
            "summaries": sum(bool(row.incomplete) for row in self.records),
            "did_ids": [row.id for row in did],
            "suppressed_ids": [row.id for row in suppressed],
            "suppressed": len(suppressed),
        }


def unified_feed(
    *,
    schedule_runs: list[dict[str, Any]] | None = None,
    hooks: list[Any] | None = None,
    event_triggers: list[Any] | None = None,
    limit: int = 50,
) -> list[FireRecord]:
    feed = HistoryFeed([])
    projections = (
        (schedule_runs, schedule_run_to_record, "a schedule run"),
        (hooks, hook_to_record, "a hook"),
        (event_triggers, event_trigger_to_record, "an event trigger"),
    )
    for source, project, label in projections:
        feed.add(source or [], project, label)
    return feed.latest(limit)


def feed_response(
    records: list[FireRecord], *, total: int | None = None
) -> dict[str, Any]:
    return HistoryFeed(records).response(total)


def is_inert(record: FireRecord) -> bool:
    return record.outcome in INERT_OUTCOMES


def partition_inert(
    records: list[FireRecord],
) -> tuple[list[FireRecord], list[FireRecord]]:
    return HistoryFeed(records).partition()


def outcome_counts(records: list[FireRecord]) -> dict[str, int]:
    counts = Counter(record.outcome for record in records)
    return {kind: counts[kind] for kind in FIRE_OUTCOMES if counts[kind]}
