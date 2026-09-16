"""Close due ledger questions and propose lessons from measured decision outcomes."""

from __future__ import annotations

import calendar
import json
import logging
import time
from typing import Any

from gideon.assurance.ledger import outcomes

logger = logging.getLogger(__name__)
_MAX_RUNS = 200
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_PROPOSING_PRODUCERS = frozenset({outcomes.PRODUCER_DECISION})


def _epoch(ts: str) -> float | None:
    try:
        utc = time.strptime(str(ts), _TS_FMT)
        seconds = calendar.timegm(utc)
    except (ValueError, TypeError):
        return None
    return float(seconds)


def _read_metric(service: Any, metric: str) -> float | None:
    try:
        record = service.get_semantic(metric)
    except Exception:
        logger.debug(
            "outcome-resolver: metric read failed for %r", metric, exc_info=True
        )
        return None
    if not record:
        return None
    value = record.get("value_json", record) if isinstance(record, dict) else record
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, dict):
        key = next(
            (key for key in ("value", "score", "measured") if key in value), None
        )
        if key is not None:
            value = value[key]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lesson(
    run: Any, question: outcomes.OutcomeQuestion, resolved: dict[str, Any]
) -> str:
    context = dict(run=run.id, subject=question.subject, metric=question.metric)
    if resolved["resolution"] != outcomes.MEASURED:
        return (
            "Run `{run}` decided {subject}, but its metric `{metric}` could not "
            "be measured after the horizon, so the decision's outcome is inconclusive. Treat this bet "
            "as unconfirmed rather than validated."
        ).format(**context)
    context.update(
        measured=resolved["measured"],
        baseline=question.baseline,
        score=resolved["score"],
        template=run.workflow_name or "this template",
    )
    return (
        "Run `{run}` decided {subject}. After its horizon the metric "
        "`{metric}` measured {measured:.4g} against a baseline of "
        "{baseline:.4g} (benchmark-relative score {score:+.2f}). Weight "
        "future runs of `{template}` toward decisions this outcome supports."
    ).format(**context)


class _RunQuestions:
    def __init__(self, run, events, resolver):
        self.run, self.events, self.resolver = run, events, resolver
        self.journal = None

    def ready(self, question):
        due = outcomes.is_due(
            question, opened_epoch=_epoch(question.ts), now=self.resolver.now
        )
        if not due:
            return False
        return (
            question.metric_source != outcomes.SOURCE_MEMORY or self.resolver.has_vector
        )

    def measure(self, question):
        readers = (
            (
                outcomes.SOURCE_LEDGER,
                lambda: outcomes.measure_from_events(question, self.events),
            ),
            (
                outcomes.SOURCE_CONSUMPTION,
                lambda: self.resolver.consumption.measure_consumption(question),
            ),
        )
        read = next(
            (read for source, read in readers if question.metric_source == source),
            lambda: _read_metric(self.resolver.service, question.metric),
        )
        return read()

    def close(self, question, measured, score, resolution):
        if self.journal is None:
            self.journal = self.resolver.journals.Journal(self.run.id)
        identity = [
            str(question.record.get(field) or "")
            for field in ("instance_path", "node_id")
        ]
        fields = dict(
            pending_event_id=question.event_id,
            producer=question.producer,
            subject=question.subject,
            metric=question.metric,
            baseline=question.baseline,
            measured=measured,
            score=score,
            resolution=resolution,
        )
        record = self.journal.outcome_resolved(*identity, **fields)
        self.events.append(record)
        counter = "resolved" if resolution == outcomes.MEASURED else "inconclusive"
        self.resolver.counts[counter] += 1
        return record

    def propose(self, question, resolved, score, resolution):
        if question.producer not in _PROPOSING_PRODUCERS:
            return
        queue = self.resolver.proposals
        fields = dict(
            kind=queue.Kind.LESSON_BATCH.value,
            title=f"Outcome of {self.run.workflow_name or 'run'}: {question.subject}"[
                :120
            ],
            body=_lesson(self.run, question, resolved),
            target=f"outcome.{self.run.id}.{question.event_id}",
            provenance="inferred",
            source_cadence="run_end",
            run_id=str(self.run.id),
            evidence_strength=outcomes.evidence_strength(resolution),
            confidence=outcomes.confidence(resolution, score),
            tags=["run_end", "outcome", resolution],
            occurrences=1,
            min_evidence=1,
        )
        _verdict, proposal = queue.enqueue(**fields)
        if proposal is not None:
            self.resolver.counts["proposed"] += 1

    def resolve(self, questions):
        for question in questions:
            if not self.ready(question):
                self.resolver.counts["pending"] += 1
                continue
            measured = self.measure(question)
            resolution = outcomes.resolution_for(measured)
            score = (
                outcomes.score(measured, question.baseline)
                if measured is not None
                else 0.0
            )
            record = self.close(question, measured, score, resolution)
            self.propose(question, record, score, resolution)


class _OutcomePass:
    def __init__(
        self, service, now, has_vector, journals, proposals, consumption, counts
    ):
        self.service, self.now, self.has_vector = service, now, has_vector
        self.journals, self.proposals, self.consumption = (
            journals,
            proposals,
            consumption,
        )
        self.counts = counts

    def visit(self, run):
        try:
            events = self.journals.ledger(run.id)
        except Exception:
            logger.debug(
                "outcome-resolver: ledger read failed for %s", run.id, exc_info=True
            )
            return
        if events:
            questions = outcomes.open_questions(events)
            if questions:
                _RunQuestions(run, events, self).resolve(questions)


def resolve(
    service: Any, *, now: float | None = None, max_runs: int = _MAX_RUNS
) -> dict[str, int]:
    counts = dict(resolved=0, inconclusive=0, pending=0, proposed=0)
    if service is None:
        return counts
    has_vector = bool(getattr(service, "has_vector", False))
    from gideon.automation.workflows import journal as journal_mod
    from gideon.automation.workflows import store as store_mod
    from gideon.cognition.learning import consumer_liveness, proposals

    stamp = time.time() if now is None else now
    try:
        runs, _total = store_mod.list_runs(limit=max_runs)
    except Exception:
        logger.debug("outcome-resolver: could not list runs", exc_info=True)
        return counts
    sweep = _OutcomePass(
        service, stamp, has_vector, journal_mod, proposals, consumer_liveness, counts
    )
    for run in runs:
        sweep.visit(run)
    return counts
