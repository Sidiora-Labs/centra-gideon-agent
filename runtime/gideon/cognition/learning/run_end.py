from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
_MAX_EVIDENCE = 3
_EVIDENCE_CLIP = 400
_MAX_TRACE_DRAFTS = 2


@dataclass
class FailureCapsule:
    template: str
    step: str
    mode: str
    signature: str
    repro_command: str
    forbidden_success_modes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def render(self) -> str:
        fields = (
            ("template/step", f"{self.template}/{self.step}"),
            ("failure mode", self.mode),
            ("signature", self.signature or "(none)"),
            ("reproduce", self.repro_command),
        )
        sections = ["Failure capsule (replay to verify this lesson still applies):"]
        sections += [f"- {label}: {value}" for label, value in fields]
        if self.forbidden_success_modes:
            sections += [
                "- must NOT be called success: "
                + "; ".join(self.forbidden_success_modes)
            ]
        if self.evidence:
            sections += ["- evidence:", *[f"    {sample}" for sample in self.evidence]]
        return "\n".join(sections)


def _terminal_failures(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    histories: dict[str, list[Any]] = {}
    exhausted_order: list[str] = []
    for event in events:
        node = str(event.get("node_id") or "")
        if node:
            state = histories.setdefault(node, [None, None])
            state[0] = event
            if event.get("retries_exhausted"):
                if state[1] is None:
                    exhausted_order.append(node)
                state[1] = event
    order = exhausted_order + [
        node for node, state in histories.items() if state[1] is None
    ]
    return {
        node: (
            histories[node][1] if histories[node][1] is not None else histories[node][0]
        )
        for node in order
    }


def _repro_command(run: Any) -> str:
    return f'workflow_start(name="diagnose-run", inputs={{"run_id": "{run.id}"}})'


class _RunMining:
    def __init__(
        self,
        run: Any,
        service: Any,
        journal: Any,
        mining: Any,
        action: Any,
        verdict: Any,
    ) -> None:
        self.run, self.service, self.journal = run, service, journal
        self.mining, self.action, self.verdict = mining, action, verdict
        self.filed = 0

    def invert(self) -> None:
        result = self.mining.invert_intent(self.run, journal=self.journal)
        if result.inverted:
            logger.info(
                "run %s: intent inversion — drift %.2f, unaddressed %s",
                result.run_id,
                result.drift,
                ", ".join(result.unaddressed[:5]) or "(none)",
            )

    def index(self) -> None:
        self.mining.index_run_spec(self.run, self.service, journal=self.journal)

    def compare(self) -> None:
        matches = self.mining.similar_run_matches(
            self.run, self.service, journal=self.journal
        )
        if matches.blind:
            logger.debug(
                "run %s: similarity detector blind (%s)", self.run.id, matches.miss
            )
            return
        decision = self.verdict(matches=matches.matches, now=time.time())
        if decision.action != self.action.AUTO_FILE.value:
            logger.debug(
                "run %s: similarity declined (%s)",
                self.run.id,
                decision.skip_reason or "n/a",
            )
            return
        self.filed += int(bool(_file_similarity_draft(self.run, matches, decision)))

    def trace(self) -> None:
        matches, _ = self.mining.positive_path_candidates(
            workflow_name=str(getattr(self.run, "workflow_name", "") or "")
        )
        for match in matches[:_MAX_TRACE_DRAFTS]:
            accepted = self.mining.file_positive_trace(
                match,
                session_key=str(
                    getattr(getattr(self.run, "origin", None), "session_key", "") or ""
                ),
            )
            if accepted:
                self.filed += 1

    def execute(self) -> int:
        stages = (
            (self.invert, "intent inversion"),
            (self.index, "spec indexing"),
            (self.compare, "similarity pass"),
            (self.trace, "positive-path mining"),
        )
        for operation, label in stages:
            try:
                operation()
            except Exception:
                logger.debug("run-end: %s failed", label, exc_info=True)
        return self.filed


def _mine_positive_signals(run: Any, service: Any, *, journal: Any) -> int:
    try:
        from gideon.cognition.learning import mining
        from gideon.cognition.learning.detectors import Action, similarity_verdict
    except Exception:
        logger.debug("run-end: mining unavailable", exc_info=True)
        return 0
    return _RunMining(
        run, service, journal, mining, Action, similarity_verdict
    ).execute()


def _file_similarity_draft(run: Any, found: Any, verdict: Any) -> bool:
    from gideon.cognition.learning import proposals

    name = str(getattr(run, "workflow_name", "") or "ad-hoc work")
    prior_ids = ", ".join(match[0] for match in found.matches[:8])
    paragraphs = [
        f"{len(found.matches)} recent run(s) match this plan's shape closely enough to be the same procedure.",
        str(verdict.reason),
        f"Prior runs: {prior_ids}",
        "Naming it as a template makes the next one a single call instead of a re-plan.",
    ]
    request: dict = dict(
        kind=proposals.Kind.TEMPLATE.value,
        title=f"Repeated plan shape in {name}"[:120],
        body="\n\n".join(paragraphs),
        target=f"similar:{name}",
        provenance="inferred",
        source_cadence="run_end",
        run_id=str(getattr(run, "id", "") or ""),
        evidence_strength="correlated",
        confidence=0.55,
        tags=["plan_similarity", "run_end"],
        occurrences=len(found.matches),
        min_evidence=1,
    )
    return proposals.enqueue(**request)[1] is not None


class _FailureLessons:
    def __init__(self, run: Any, service: Any, report: dict[str, int]) -> None:
        from gideon.cognition.after_turn_review import is_environment_failure_claim
        from gideon.cognition.learning import proposals
        from gideon.cognition.learning.detectors import (
            LessonKey,
            classify_failure,
            dedupe_signature,
            lesson_worthy,
        )

        self.environment = is_environment_failure_claim
        self.proposals = proposals
        self.key_type, self.classify, self.signature, self.worthy = (
            LessonKey,
            classify_failure,
            dedupe_signature,
            lesson_worthy,
        )
        self.run, self.service, self.report = run, service, report
        self.template = str(getattr(run, "workflow_name", "") or "unknown")
        self.scope = str(getattr(run, "project_id", "") or "") or str(
            getattr(run, "id", "") or ""
        )
        self.reproduce = _repro_command(run)
        self.filed: set[str] = set()

    def eligible(self, node: str, text: str) -> bool:
        if self.environment(text):
            return False
        worthy, reason = self.worthy(text)
        if not worthy:
            logger.debug(
                "run-end: skipping failure at %s/%s — %s", self.template, node, reason
            )
        return bool(worthy)

    def prior(self, node: str) -> None:
        identity = f"workflow:{self.template}/{node}"
        try:
            saved = self.service.record_procedural(
                tool=identity,
                task_shape=identity,
                outcome="failed",
                scope_ref=self.scope,
            )
            if saved:
                self.report["procedural"] += 1
        except Exception:
            logger.debug(
                "run-end: procedural capture failed at %s/%s",
                self.template,
                node,
                exc_info=True,
            )

    def propose(self, node: str, text: str) -> None:
        mode, signature = self.classify(text), self.signature(text)
        key = self.key_type(template=self.template, mode=mode, signature=signature)
        if key.key in self.filed:
            return
        if self.proposals.quota_remaining(self.report["proposed"]) <= 0:
            self.report["skipped"] += 1
            return
        capsule = FailureCapsule(
            template=self.template,
            step=node,
            mode=mode,
            signature=signature,
            repro_command=self.reproduce,
            forbidden_success_modes=[
                f"reporting success while the {mode} failure at {node} is unaddressed"
            ],
            evidence=[text[:_EVIDENCE_CLIP]][:_MAX_EVIDENCE],
        )
        request: dict = {
            "kind": self.proposals.Kind.LESSON_BATCH.value,
            "title": f"Run failure in {self.template}/{node}: {mode}"[:120],
            "body": f"Step `{node}` of template `{self.template}` failed ({mode}). A future run of this "
            f"template should account for this failure mode.\n\n{capsule.render()}",
            "target": key.key,
            "provenance": "inferred",
            "source_cadence": "run_end",
            "run_id": str(getattr(self.run, "id", "") or ""),
            "source_excerpt": text,
            "evidence_strength": "correlated",
            "confidence": 0.5,
            "tags": ["run_end", "workflow_run", mode],
            "occurrences": 1,
            "min_evidence": 1,
        }
        proposal = self.proposals.enqueue(**request)[1]
        if proposal is None:
            self.report["skipped"] += 1
            return
        self.filed.add(key.key)
        self.report["proposed"] += 1

    def consume(self, events: list[dict[str, Any]]) -> None:
        for node, record in _terminal_failures(events).items():
            text = str(record.get("error") or "")
            if self.eligible(node, text):
                self.prior(node)
                self.propose(node, text)
            else:
                self.report["filtered"] += 1


def capture(run: Any, service: Any, *, journal: Any = None) -> dict[str, int]:
    report = dict.fromkeys(
        ("proposed", "procedural", "filtered", "skipped", "mined"), 0
    )
    if service is None or not getattr(service, "has_vector", False):
        return report
    if journal is None:
        from gideon.automation.workflows import journal
    report["mined"] = _mine_positive_signals(run, service, journal=journal)
    try:
        records = journal.ledger(run.id, kinds={journal.STEP_FAILED})
    except Exception:
        logger.debug(
            "run-end: could not read ledger for %s",
            getattr(run, "id", "?"),
            exc_info=True,
        )
        return report
    if records:
        _FailureLessons(run, service, report).consume(records)
    return report
