"""Pure change attribution, proposed reversions and proposer calibration."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    EFFECTIVE = "EFFECTIVE"
    PARTIALLY_EFFECTIVE = "PARTIALLY_EFFECTIVE"
    INEFFECTIVE = "INEFFECTIVE"
    MIXED = "MIXED"
    HARMFUL = "HARMFUL"
    PENDING = "PENDING"


VERDICTS: tuple[str, ...] = tuple(v.value for v in Verdict)
REVERT_VERDICTS: frozenset[str] = frozenset({Verdict.HARMFUL.value})
MIN_RUNS = 3
DELTA_EPS = 0.02


@dataclass
class Outcome:
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    runs_after: int = 0

    def delta(self, cluster: str) -> float:
        previous, current = (
            float(values.get(cluster, 0.0)) for values in (self.before, self.after)
        )
        return previous - current

    @property
    def fixed(self) -> list[str]:
        changes = ((cluster, self.delta(cluster)) for cluster in self.before)
        return sorted(cluster for cluster, delta in changes if delta >= DELTA_EPS)

    @property
    def regressed(self) -> list[str]:
        changes = {cluster: self.delta(cluster) for cluster in self.before}
        worsened = {
            cluster for cluster, delta in changes.items() if delta <= -DELTA_EPS
        }
        additions = (
            cluster
            for cluster, rate in self.after.items()
            if cluster not in self.before and float(rate) >= DELTA_EPS
        )
        worsened.update(additions)
        return sorted(worsened)

    def to_dict(self) -> dict[str, Any]:
        return dict(
            runs_after=self.runs_after, fixed=self.fixed, regressed=self.regressed
        )


@dataclass
class Attribution:
    verdict: str
    predicted: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    regressed: list[str] = field(default_factory=list)
    unfulfilled: list[str] = field(default_factory=list)
    unattributed_regressions: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def owes_revert(self) -> bool:
        return self.verdict in REVERT_VERDICTS

    @property
    def precision(self) -> float:
        if not self.predicted:
            return 0.0
        matched = set(self.predicted).intersection(self.fixed)
        return len(matched) / len(self.predicted)

    def to_dict(self) -> dict[str, Any]:
        values = dict(verdict=self.verdict)
        for name in (
            "predicted",
            "fixed",
            "regressed",
            "unfulfilled",
            "unattributed_regressions",
        ):
            values[name] = list(getattr(self, name))
        values.update(
            precision=round(self.precision, 4),
            owes_revert=self.owes_revert,
            reason=self.reason,
        )
        return values


class _AttributionCase:
    def __init__(self, predictions, outcome, minimum):
        self.outcome, self.minimum = outcome, minimum
        self.predicted = sorted(
            {str(value) for value in (predictions or []) if str(value).strip()}
        )
        self.fixed, self.regressed = outcome.fixed, outcome.regressed
        self.unfulfilled = sorted(set(self.predicted).difference(self.fixed))
        self.unattributed = sorted(set(self.regressed).difference(self.predicted))
        self.landed = sorted(set(self.predicted).intersection(self.fixed))

    def verdict(self):
        if self.outcome.runs_after < max(1, self.minimum):
            return Verdict.PENDING
        if self.regressed:
            return Verdict.MIXED if self.landed else Verdict.HARMFUL
        if not self.fixed:
            return Verdict.INEFFECTIVE
        return (
            Verdict.EFFECTIVE
            if self.predicted and not self.unfulfilled
            else Verdict.PARTIALLY_EFFECTIVE
        )

    def reason(self, verdict):
        if verdict == Verdict.PENDING:
            return (
                f"only {self.outcome.runs_after} run(s) since acceptance; {self.minimum} are needed before a "
                "verdict is evidence rather than an anecdote"
            )
        if verdict == Verdict.HARMFUL:
            base = f"{len(self.regressed)} cluster(s) regressed and none of the predicted fixes landed"
            return base + (
                f"; {len(self.unattributed)} regression(s) nobody predicted"
                if self.unattributed
                else ""
            )
        reasons = {
            Verdict.MIXED: lambda: (
                f"{len(self.landed)} predicted fix(es) landed but {len(self.regressed)} regressed — the "
                "change did something wanted, so reverting is the user's call, not automatic"
            ),
            Verdict.INEFFECTIVE: lambda: "nothing measurably moved in either direction; clutter rather than damage",
            Verdict.EFFECTIVE: lambda: f"every predicted fix landed ({len(self.landed)}) and nothing regressed",
            Verdict.PARTIALLY_EFFECTIVE: lambda: (
                (
                    f"{len(self.landed)} of {len(self.predicted)} predicted fixes landed"
                    if self.predicted
                    else f"{len(self.fixed)} cluster(s) improved, none of them predicted"
                )
                + " and nothing regressed"
            ),
        }
        return reasons[verdict]()

    def result(self):
        verdict = self.verdict()
        return Attribution(
            verdict=verdict.value,
            predicted=self.predicted,
            fixed=self.fixed,
            regressed=self.regressed,
            unfulfilled=self.unfulfilled,
            unattributed_regressions=self.unattributed,
            reason=self.reason(verdict),
        )


def attribute(
    *, predicted_fixes: list[str] | None, outcome: Outcome, min_runs: int = MIN_RUNS
) -> Attribution:
    return _AttributionCase(predicted_fixes, outcome, min_runs).result()


@dataclass
class RevertProposal:
    target: str
    kind: str = "retirement"
    title: str = ""
    body: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    provenance: str = "accountability"

    def to_dict(self) -> dict[str, Any]:
        return {
            name: list(value) if name == "evidence_refs" else value
            for name in (
                "target",
                "kind",
                "title",
                "body",
                "evidence_refs",
                "provenance",
            )
            for value in (getattr(self, name),)
        }


def revert_proposal(
    *, target: str, attribution: Attribution, run_ids: list[str] | None = None
) -> RevertProposal | None:
    if not attribution.owes_revert:
        return None
    summary = [
        f"Accepted change to {target} scored {attribution.verdict}.",
        f"Regressed: {', '.join(attribution.regressed) or 'none recorded'}.",
    ]
    details = (
        (
            attribution.unattributed_regressions,
            "Nobody predicted: {} — these are the regressions no proposer anticipated.",
        ),
        (attribution.unfulfilled, "Predicted but never landed: {}."),
    )
    summary.extend(
        template.format(", ".join(clusters))
        for clusters, template in details
        if clusters
    )
    evidence = sorted(set(map(str, run_ids or [])))[:20]
    return RevertProposal(
        target,
        title=f"Revert {target} — scored {attribution.verdict}",
        body=" ".join(summary),
        evidence_refs=evidence,
    )


@dataclass
class ProposerTrust:
    source: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def decided(self) -> int:
        return self.total - self.counts.get(Verdict.PENDING.value, 0)

    def _rate(self, *verdicts):
        count = self.decided
        if count <= 0:
            return 0.0
        return sum(self.counts.get(verdict, 0) for verdict in verdicts) / count

    @property
    def harm_rate(self) -> float:
        return self._rate(Verdict.HARMFUL.value)

    @property
    def effective_rate(self) -> float:
        return self._rate(Verdict.EFFECTIVE.value, Verdict.PARTIALLY_EFFECTIVE.value)

    def to_dict(self) -> dict[str, Any]:
        result = dict(source=self.source, counts=dict(sorted(self.counts.items())))
        result.update(
            total=self.total,
            decided=self.decided,
            harm_rate=round(self.harm_rate, 4),
            effective_rate=round(self.effective_rate, 4),
        )
        return result


def proposer_trust(records: list[tuple[str, str]]) -> list[ProposerTrust]:
    populations = {}
    for source, verdict in records or []:
        name = str(source)
        populations.setdefault(name, Counter())[str(verdict)] += 1
    results = [
        ProposerTrust(name, dict(counts)) for name, counts in populations.items()
    ]
    results.sort(key=lambda result: (-result.harm_rate, -result.total, result.source))
    return results


def assert_gate_covers_cadences() -> list[str]:
    import re
    from pathlib import Path

    from gideon.cognition.learning.gate import Cadence

    current = Path(__file__).resolve()
    unseen = {cadence.name for cadence in Cadence}
    expression = re.compile(r"Cadence\.([A-Z_]+)")
    for candidate in current.parent.parent.rglob("*.py"):
        excluded = candidate.resolve() == current or (
            candidate.name == "gate.py" and candidate.parent.name == "learning"
        )
        if excluded:
            continue
        try:
            source = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        unseen.difference_update(expression.findall(source))
    return sorted(unseen)
