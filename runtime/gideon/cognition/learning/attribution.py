"""Persist accepted predictions, measure target runs and propose harmful-change reversions."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.core.record_ids import record_path

logger = logging.getLogger(__name__)
_DIRNAME = "attribution"
_MAX_RUNS = 200
_MAX_RECORDS_PER_TICK = 50
_MAX_PENDING = 200
_RESOLVED_KEEP = 200
_TALLY = {
    "HARMFUL": "harmful",
    "EFFECTIVE": "effective",
    "MIXED": "mixed",
    "INEFFECTIVE": "ineffective",
    "PARTIALLY_EFFECTIVE": "partial",
}


@dataclass
class AcceptedChange:
    id: str
    target: str
    source: str
    kind: str
    predicted_fixes: list[str] = field(default_factory=list)
    before: dict[str, float] = field(default_factory=dict)
    baseline_run_ids: list[str] = field(default_factory=list)
    accepted_at: str = ""
    verdict: str = "PENDING"
    resolved: bool = False
    graded_at: str = ""
    after: dict[str, float] = field(default_factory=dict)
    runs_after: int = 0
    reason: str = ""
    revert_proposal_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dir() -> Path:
    from gideon.core.config.loader import config_dir

    return Path(config_dir()).joinpath("learning", _DIRNAME)


def _path(record_id: object) -> Path:
    root = _dir()
    return record_path(root, record_id, kind="record_id")


def _now() -> str:
    current = datetime.now(timezone.utc)
    return current.isoformat()


class _AcceptedFiles:
    @staticmethod
    def decode(path):
        content = path.read_text(encoding="utf-8")
        fields = json.loads(content)
        return AcceptedChange(**fields)

    def records(self):
        root = _dir()
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                try:
                    record = self.decode(path)
                except (OSError, ValueError, TypeError):
                    continue
                yield record

    def discard(self, records, *, pending=False):
        deleted = 0
        for record in records:
            try:
                _path(record.id).unlink()
            except OSError:
                message = (
                    "attribution: expire failed for %s"
                    if pending
                    else "attribution: resolved prune failed for %s"
                )
                logger.debug(message, record.id, exc_info=True)
            else:
                deleted += 1
                if pending:
                    logger.info(
                        "attribution: store full; expired oldest pending record %s",
                        record.id,
                    )
        return deleted


def _load(rid: str) -> AcceptedChange | None:
    try:
        return _AcceptedFiles.decode(_path(rid))
    except (OSError, ValueError, TypeError):
        return None


def _save(rec: AcceptedChange) -> bool:
    from gideon.core.atomic_write import atomic_write

    try:
        path = _path(rec.id)
        document = json.dumps(rec.to_dict(), indent=2)
        atomic_write(path, document)
    except OSError:
        logger.debug("attribution record write failed for %s", rec.id, exc_info=True)
        return False
    return True


def _all() -> list[AcceptedChange]:
    return list(_AcceptedFiles().records())


def _attribution_enabled() -> bool:
    try:
        from gideon.core.config.loader import AppConfig

        settings = AppConfig.load().learning
        for field_name in ("enabled", "attribution_enabled"):
            if not bool(getattr(settings, field_name, True)):
                return False
    except Exception:
        logger.debug(
            "attribution config read failed; treating as enabled", exc_info=True
        )
    return True


def _terminal_runs_for(target: str, *, limit: int = _MAX_RUNS) -> list[Any]:
    if not target:
        return []
    from gideon.automation.workflows import store as store_mod

    try:
        recent, _total = store_mod.list_runs(workflow_name=target, limit=limit)
    except Exception:
        logger.debug("attribution: could not list runs for %r", target, exc_info=True)
        return []
    return list(filter(lambda run: getattr(run, "is_terminal", False), recent))


class _FailureTally:
    def __init__(self, journal, terminal_failures, classify, excluded):
        self.journal, self.terminal_failures = journal, terminal_failures
        self.classify, self.excluded = classify, excluded
        self.counts = Counter()

    def read(self, run):
        try:
            events = self.journal.ledger(run.id, kinds={self.journal.STEP_FAILED})
        except Exception:
            logger.debug(
                "attribution: ledger read failed for %s", run.id, exc_info=True
            )
            return
        seen = set()
        for failure in self.terminal_failures(events).values():
            mode = self.classify(str(failure.get("error") or ""))
            if mode and mode not in self.excluded:
                seen.add(mode)
        self.counts.update(seen)


def _failure_rates(runs: list[Any]) -> dict[str, float]:
    if not runs:
        return {}
    from gideon.automation.workflows import journal as journal_mod
    from gideon.cognition.learning.detectors import NON_LESSON_MODES, classify_failure
    from gideon.cognition.learning.run_end import _terminal_failures

    population = _FailureTally(
        journal_mod, _terminal_failures, classify_failure, NON_LESSON_MODES
    )
    for run in runs:
        population.read(run)
    return {mode: count / len(runs) for mode, count in population.counts.items()}


class _AcceptedSnapshot:
    def __init__(self, proposal):
        self.proposal = proposal

    def read(self):
        target = str(getattr(self.proposal, "target", "") or "")
        if not target:
            return None
        manifest = getattr(self.proposal, "change_manifest", None) or {}
        predictions = (
            manifest.get("predicted_fixes", []) if isinstance(manifest, dict) else []
        )
        predictions = sorted(
            {str(value) for value in predictions if str(value).strip()}
        )
        identity = str(
            getattr(self.proposal, "fingerprint", "")
            or getattr(self.proposal, "id", "")
            or ""
        )
        if not identity:
            return None
        runs = _terminal_runs_for(target)
        return AcceptedChange(
            id=identity,
            target=target,
            source=str(getattr(self.proposal, "provenance", "") or "inferred"),
            kind=str(getattr(self.proposal, "kind", "") or ""),
            predicted_fixes=predictions,
            before=_failure_rates(runs),
            baseline_run_ids=sorted(str(run.id) for run in runs),
            accepted_at=_now(),
        )


def record_accepted_change(prop: Any) -> AcceptedChange | None:
    if not _attribution_enabled():
        return None
    try:
        snapshot = _AcceptedSnapshot(prop).read()
        if snapshot is None:
            return None
        _expire_over_cap()
        if _save(snapshot):
            logger.info(
                "attribution: recorded accepted change to %s for later grading",
                snapshot.target,
            )
            return snapshot
    except Exception:
        logger.debug("attribution: record_accepted_change failed", exc_info=True)
    return None


def _expire_over_cap() -> None:
    records = [record for record in _all() if not record.resolved]
    records.sort(key=lambda record: record.accepted_at)
    excess = max(0, len(records) - _MAX_PENDING + 1)
    _AcceptedFiles().discard(records[:excess], pending=True)


class _ChangeGrade:
    def __init__(self, record, accountability, floor):
        self.record, self.rules, self.floor = record, accountability, floor
        self.runs = []
        self.attribution = None

    def observe(self):
        terminal = _terminal_runs_for(self.record.target)
        baseline = set(self.record.baseline_run_ids)
        self.runs = [run for run in terminal if str(run.id) not in baseline]
        measured = self.rules.Outcome(
            before=dict(self.record.before),
            after=_failure_rates(self.runs),
            runs_after=len(self.runs),
        )
        self.attribution = self.rules.attribute(
            predicted_fixes=self.record.predicted_fixes,
            outcome=measured,
            min_runs=self.floor,
        )
        record = self.record
        record.after, record.runs_after = dict(measured.after), measured.runs_after
        record.verdict, record.reason = (
            self.attribution.verdict,
            self.attribution.reason,
        )
        record.graded_at = _now()

    def handle_harm(self, report):
        proposal_id = _file_revert(
            self.record, self.attribution, [str(run.id) for run in self.runs]
        )
        if proposal_id:
            self.record.revert_proposal_id = proposal_id
            report["reverts"] += 1
        try:
            from gideon.security.guardrails.ladder import revoke_granted_scopes

            revoke_granted_scopes(
                cause=f"A HARMFUL verdict landed on the accepted change to {self.record.target}: {self.attribution.reason}",
                evidence_id=f"attribution:{self.record.id}",
                source="attribution",
            )
        except Exception:
            logger.warning("attribution: autonomy revocation failed", exc_info=True)

    def commit(self, report):
        pending = self.attribution.verdict == self.rules.Verdict.PENDING.value
        if pending:
            report["pending"] += 1
        else:
            self.record.resolved = True
            if self.attribution.owes_revert:
                self.handle_harm(report)
        _save(self.record)
        if not pending:
            report["graded"] += 1
            counter = _TALLY.get(self.attribution.verdict)
            if counter:
                report[counter] += 1


def grade_accepted_changes(
    *, min_runs: int | None = None, max_records: int = _MAX_RECORDS_PER_TICK
) -> dict[str, int]:
    report = dict.fromkeys(
        (
            "graded",
            "pending",
            "harmful",
            "effective",
            "mixed",
            "ineffective",
            "partial",
            "reverts",
        ),
        0,
    )
    if not _attribution_enabled():
        return report
    from gideon.cognition.learning import accountability

    floor = accountability.MIN_RUNS if min_runs is None else min_runs
    pending = [record for record in _all() if not record.resolved]
    for record in pending[:max_records]:
        try:
            grading = _ChangeGrade(record, accountability, floor)
            grading.observe()
            grading.commit(report)
        except Exception:
            logger.debug("attribution: grading failed for %s", record.id, exc_info=True)
    _prune_resolved()
    return report


def _file_revert(rec: AcceptedChange, attr: Any, run_ids: list[str]) -> str:
    from gideon.cognition.learning import accountability, proposals

    revert = accountability.revert_proposal(
        target=rec.target, attribution=attr, run_ids=run_ids
    )
    if revert is None:
        return ""
    fields = dict(
        kind=proposals.Kind.RETIREMENT.value,
        title=revert.title,
        body=revert.body,
        target=revert.target,
        provenance="inferred",
        evidence_refs=list(revert.evidence_refs),
        evidence_strength="correlated",
        confidence=0.5,
        tags=["attribution", "revert", rec.verdict.lower()],
        occurrences=1,
        min_evidence=1,
    )
    _verdict, proposal = proposals.enqueue(**fields)
    return "" if proposal is None else proposal.id


def _prune_resolved(keep: int = _RESOLVED_KEEP) -> int:
    records = [record for record in _all() if record.resolved]
    records.sort(key=lambda record: record.graded_at or record.accepted_at)
    excess = max(0, len(records) - max(0, keep))
    return _AcceptedFiles().discard(records[:excess])


def verdict_history() -> list[tuple[str, str]]:
    resolved = filter(lambda record: record.resolved, _all())
    return [(record.source or "unknown", record.verdict) for record in resolved]


def proposer_trust_report() -> list[dict[str, Any]]:
    from gideon.cognition.learning import accountability

    history = verdict_history()
    return list(
        map(lambda trust: trust.to_dict(), accountability.proposer_trust(history))
    )
