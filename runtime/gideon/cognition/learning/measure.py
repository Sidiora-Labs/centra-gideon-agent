"""Is the flywheel working? Per-arm precision + Beta-Binomial trust (LEARN-R4 / §2.5 — S71).

§7's criterion 7 is the bar: per-arm surfaced-vs-used precision is REPORTABLE per entity kind,
threshold profiles are TUNABLE FROM DATA, and a muted chip visibly LOWERS an entity's trust
posterior. All three are measurements, not opinions, and the plan is explicit about why:
"unenforced 'helpful' scores stay ornamental forever".

**Measured before writing.** `learning/surfacing.py` already carries `THRESHOLD_PROFILES` with the
0.55/0.62 split the plan wants preserved, and `learning/usage.py` already persists
`surfaced`/`used`/`successes`/`failures` per entity — the exact counts a posterior needs. What is
missing is the middle: `Candidate` has no `arm` field, so nothing can attribute a surfacing to the
match path that produced it, and nothing computes a posterior from the counts that exist.

So this module adds the attribution and the statistics, and deliberately does NOT add a second
threshold table or a second usage store.

**Why per-arm and not one scalar.** §2.5: "a single scalar can't be calibrated per-arm". An
exact-name match and an embedding match that both score 0.7 are not equally trustworthy, and
averaging them produces a threshold too permissive for one and too strict for the other. Precision
is therefore reported per `(kind, arm)` pair — the unit a threshold can actually be tuned on.

**Why Beta-Binomial and not a ratio.** A raw used/surfaced ratio says 1.0 after a single lucky hit
and 0.0 after a single miss — so a brand-new entity outranks a proven one, and one bad turn
condemns a good lesson. The Beta posterior encodes "we have barely any evidence" as a wide interval
around the prior, which is what makes the tie-break honest. `LOWER_BOUND_Z` shrinks toward the
prior in proportion to ignorance; ranking on it is what stops a 1-for-1 entity leading the board.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.cognition.memory_push import ARM_CONFIDENCE as _SHIPPED_ARMS
from gideon.cognition.memory_push import RECENCY_BONUS as RECENCY_BONUS

ARM_CONFIDENCE: dict[str, float] = {
    **_SHIPPED_ARMS,
    "exact_title": 0.80,
    "path": 0.75,
    "keyword": 0.70,
    "embedding": 0.60,
}


DEFAULT_ARM = "embedding"

ARMS: tuple[str, ...] = tuple(sorted(ARM_CONFIDENCE))

PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0

LOWER_BOUND_Z = 1.0

MUTE_WEIGHT = 1.0

MIN_SAMPLES_FOR_TUNING = 20


class Precision(str, Enum):
    """How a per-arm precision figure should be read.

    `INSUFFICIENT` is a distinct state rather than a low number, because the two demand opposite
    responses: a low precision means tighten the threshold, while insufficient evidence means leave
    it alone and collect more. Collapsing them is how a threshold gets tuned on noise.
    """

    GOOD = "good"
    POOR = "poor"
    INSUFFICIENT = "insufficient"


def arm_confidence(arm: str, *, recent: bool = False) -> float:
    """The base confidence for one match arm, plus the recency bonus.

    An unknown arm gets `DEFAULT_ARM`'s confidence, not zero: a zero would suppress the candidate
    entirely and make an un-instrumented match path silently invisible instead of merely weak.
    """
    base = ARM_CONFIDENCE.get(arm, ARM_CONFIDENCE[DEFAULT_ARM])
    return min(1.0, base + (RECENCY_BONUS if recent else 0.0))


@dataclass
class ArmStats:
    """Surfaced-vs-used counts for one `(kind, arm)` pair.

    `used` is derived MECHANICALLY by the caller (a skill body loaded, a template run started, a
    lesson cited) — never from a voluntary model "was this helpful" call. §2.5 is explicit:
    helpfulness scores stay ornamental forever, so this dataclass only ever receives observed facts.
    """

    kind: str
    arm: str
    surfaced: int = 0
    used: int = 0

    @property
    def precision(self) -> float:
        """Used ÷ surfaced, or 0.0 with no surfacings.

        A bare ratio here is correct — this is the descriptive statistic. The Beta posterior below
        is what handles small samples; conflating the two would leave no way to see the raw rate.
        """
        return (self.used / self.surfaced) if self.surfaced else 0.0

    @property
    def verdict(self) -> str:
        if self.surfaced < MIN_SAMPLES_FOR_TUNING:
            return Precision.INSUFFICIENT.value
        return Precision.GOOD.value if self.precision >= 0.5 else Precision.POOR.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "arm": self.arm,
            "surfaced": self.surfaced,
            "used": self.used,
            "precision": round(self.precision, 4),
            "verdict": self.verdict,
        }


def per_arm_precision(events: list[dict[str, Any]]) -> list[ArmStats]:
    return _UsageEvidence(events).arm_rows()


@dataclass
class Posterior:
    """A Beta-Binomial usefulness posterior for one entity.

    Carries `alpha`/`beta` rather than just the mean so a caller can update incrementally and so the
    interval width — the "how much do we actually know" part — stays inspectable.
    """

    kind: str
    entity: str
    alpha: float = PRIOR_ALPHA
    beta: float = PRIOR_BETA

    @property
    def mean(self) -> float:
        """The posterior mean: the expected probability this entity is useful when surfaced."""
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    @property
    def observations(self) -> float:
        """Evidence gathered, excluding the prior. What distinguishes 1-for-1 from 30-for-30."""
        return (self.alpha - PRIOR_ALPHA) + (self.beta - PRIOR_BETA)

    def precision_ratio(self) -> float:
        """The NAIVE used/surfaced ratio, prior removed.

        Exposed deliberately, and deliberately not used for ranking: it is the number a reader
        expects, and having it here is what makes the difference visible. A lucky 1-of-1 scores 1.0
        on this and still ranks below a 27-of-30 entity on `lower_bound` — the whole reason this is
        a posterior rather than a division.
        """
        used = self.alpha - PRIOR_ALPHA
        total = used + (self.beta - PRIOR_BETA)
        return (used / total) if total > 0 else 0.0

    @property
    def stdev(self) -> float:
        total = self.alpha + self.beta
        if total <= 1:
            return 0.5
        return math.sqrt((self.alpha * self.beta) / (total * total * (total + 1)))

    @property
    def lower_bound(self) -> float:
        """The ranking score: mean minus `LOWER_BOUND_Z` standard deviations, clamped to [0, 1].

        Ranking on this rather than the mean is what makes the tie-break honest. A brand-new entity
        with one lucky hit has a mean of 0.67 and a huge stdev, so its lower bound sits near the
        prior — while a proven entity's interval is tight and its lower bound is close to its mean.
        """
        return max(0.0, min(1.0, self.mean - LOWER_BOUND_Z * self.stdev))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entity": self.entity,
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "mean": round(self.mean, 4),
            "lower_bound": round(self.lower_bound, 4),
            "observations": round(self.observations, 2),
        }


def posterior_from_counts(
    *, kind: str, entity: str, surfaced: int, used: int, mutes: int = 0
) -> Posterior:
    counts = _UsageEvidence.normalize(surfaced, used, mutes)
    return Posterior(
        kind=kind,
        entity=entity,
        alpha=PRIOR_ALPHA + counts[0],
        beta=PRIOR_BETA + counts[1] + MUTE_WEIGHT * counts[2],
    )


def apply_mute(posterior: Posterior, *, count: int = 1) -> Posterior:
    values = {
        name: getattr(posterior, name) for name in ("kind", "entity", "alpha", "beta")
    }
    values["beta"] += MUTE_WEIGHT * max(0, int(count or 0))
    return Posterior(**values)


def rank_by_trust(posteriors: list[Posterior]) -> list[Posterior]:
    scores = [
        ((-item.lower_bound, -item.observations, item.entity), item)
        for item in posteriors
    ]
    scores.sort(key=lambda entry: entry[0])
    return [item for _, item in scores]


@dataclass
class ThresholdProposal:
    """A proposed change to one entity kind's threshold, with the evidence behind it.

    A PROPOSAL, never an applied change. §2.5 says recalibration happens "empirically, not by
    taste" — and the corollary is that it also does not happen automatically: the 0.55/0.62 split
    was deliberately calibrated, so overwriting it from a week of data would discard a real
    decision. The caller decides; this says what the data supports.
    """

    kind: str
    current: float
    proposed: float
    reason: str
    samples: int

    @property
    def changed(self) -> bool:
        return abs(self.proposed - self.current) > 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "current": round(self.current, 4),
            "proposed": round(self.proposed, 4),
            "reason": self.reason,
            "samples": self.samples,
            "changed": self.changed,
        }


MAX_THRESHOLD_STEP = 0.05

POOR_BELOW = 0.35
RICH_ABOVE = 0.85


def propose_thresholds(
    stats: list[ArmStats], *, current: dict[str, float] | None = None
) -> list[ThresholdProposal]:
    from gideon.cognition.learning.surfacing import THRESHOLD_PROFILES

    baseline = dict(current if current is not None else THRESHOLD_PROFILES)
    return _ThresholdCalibration(baseline, stats or []).proposals()


ARM_SPREAD_ALERT = 0.4


def _arm_spread(stats: list[ArmStats]) -> str:
    floor = max(3, MIN_SAMPLES_FOR_TUNING // 4)
    selected = list(filter(lambda item: item.surfaced >= floor, stats))
    if len(selected) < 2:
        return ""
    best = worst = selected[0]
    for item in selected[1:]:
        if item.precision > best.precision:
            best = item
        if item.precision < worst.precision:
            worst = item
    if best.precision - worst.precision < ARM_SPREAD_ALERT:
        return ""
    return (
        f"NOTE: the arms disagree sharply — {best.arm} is at {best.precision:.0%} while "
        f"{worst.arm} is at {worst.precision:.0%}; the aggregate hides that, and a weak "
        f"arm is its own confidence, not this threshold"
    )


@dataclass
class FlywheelReport:
    """The answer to "is the flywheel working", as one renderable object.

    Exists because §7's criterion is about REPORTABILITY: three numbers scattered across three
    modules do not answer it, and callers assembling them ad hoc would each answer it differently.
    """

    arms: list[ArmStats] = field(default_factory=list)
    proposals: list[ThresholdProposal] = field(default_factory=list)
    trusted: list[Posterior] = field(default_factory=list)

    @property
    def overall_precision(self) -> float:
        surfaced = sum(a.surfaced for a in self.arms)
        used = sum(a.used for a in self.arms)
        return (used / surfaced) if surfaced else 0.0

    @property
    def actionable(self) -> list[ThresholdProposal]:
        return [p for p in self.proposals if p.changed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_precision": round(self.overall_precision, 4),
            "surfaced": sum(a.surfaced for a in self.arms),
            "used": sum(a.used for a in self.arms),
            "arms": [a.to_dict() for a in self.arms],
            "proposals": [p.to_dict() for p in self.proposals],
            "actionable": [p.to_dict() for p in self.actionable],
            "trusted": [p.to_dict() for p in self.trusted],
        }


def build_report(
    events: list[dict[str, Any]],
    *,
    usage: list[dict[str, Any]] | None = None,
    top: int = 20,
) -> FlywheelReport:
    arms = per_arm_precision(events)
    trust = _UsageEvidence.posteriors(usage or [])
    proposals = propose_thresholds(arms)
    ranked = rank_by_trust(trust)
    return FlywheelReport(arms, proposals, ranked[: max(1, top)])


UTILIZATION_IDEAL_LOW = 0.50
UTILIZATION_IDEAL_HIGH = 0.80

HEALTH_WEIGHTS: dict[str, float] = {
    "precision": 0.4,
    "capture": 0.3,
    "utilization": 0.2,
    "judge": 0.1,
}


def _band_score(value: float) -> float:
    """Score a utilization figure against the ideal band. 1.0 inside, falling outside.

    Falls off LINEARLY toward each end rather than dropping to zero: 45% is nearly
    ideal and 5% is not, and a cliff at the band edge would report those as the same
    failure.
    """
    if UTILIZATION_IDEAL_LOW <= value <= UTILIZATION_IDEAL_HIGH:
        return 1.0
    if value < UTILIZATION_IDEAL_LOW:
        return max(0.0, value / UTILIZATION_IDEAL_LOW)
    return max(0.0, (1.0 - value) / (1.0 - UTILIZATION_IDEAL_HIGH))


def health_composite(
    *,
    precision: float | None,
    capture_passes: int,
    capture_errors: int,
    utilization: float | None,
    judge_false_pass_rate: float | None,
) -> dict[str, Any]:
    readings = _HealthComposite.readings(
        precision, capture_passes, capture_errors, utilization, judge_false_pass_rate
    )
    return _HealthComposite(readings).payload()


class _UsageEvidence:
    def __init__(self, events):
        self.events = events

    def arm_rows(self):
        from collections import Counter

        surfaced = Counter()
        used = Counter()
        for event in self.events or []:
            if isinstance(event, dict):
                identity = (
                    str(event.get("kind", "") or "unknown"),
                    str(event.get("arm", "") or DEFAULT_ARM),
                )
                surfaced[identity] += 1
                if event.get("used"):
                    used[identity] += 1
        return [
            ArmStats(kind, arm, surfaced[(kind, arm)], used[(kind, arm)])
            for kind, arm in sorted(surfaced)
        ]

    @staticmethod
    def normalize(surfaced, used, mutes):
        total = max(0, int(surfaced or 0))
        accepted = max(0, min(int(used or 0), total))
        missed = total - accepted
        muted = max(0, int(mutes or 0))
        return accepted, missed, muted

    @staticmethod
    def posteriors(rows):
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            fields: dict = {}
            for name in ("kind", "entity"):
                fields[name] = str(row.get(name, "") or "")
            for name in ("surfaced", "used", "mutes"):
                fields[name] = int(row.get(name, 0) or 0)
            results.append(posterior_from_counts(**fields))
        return results


class _ThresholdCalibration:
    def __init__(self, baseline, rows):
        self.baseline = baseline
        self.members = {}
        for row in rows:
            self.members.setdefault(row.kind, []).append(row)

    def proposals(self):
        kinds = sorted(set(self.baseline) | set(self.members))
        return [self.for_kind(kind) for kind in kinds]

    def for_kind(self, kind):
        current = float(self.baseline.get(kind, 0.55))
        rows = self.members.get(kind, [])
        samples = sum(item.surfaced for item in rows)
        used = sum(item.used for item in rows)
        proposed = current
        if samples < MIN_SAMPLES_FOR_TUNING:
            reason = (
                f"only {samples} surfacings; {MIN_SAMPLES_FOR_TUNING} needed before "
                "tuning a threshold on them"
            )
        else:
            proposed, reason = self.adjust(current, used / samples)
            warning = _arm_spread(rows)
            if warning:
                reason = ". ".join((reason, warning))
        return ThresholdProposal(kind, current, proposed, reason, samples)

    @staticmethod
    def adjust(current, precision):
        if precision < POOR_BELOW:
            movement = min(0.95, current + MAX_THRESHOLD_STEP)
            explanation = f"{precision:.0%} of surfaced items were used; raising the bar to cut noise"
        elif precision > RICH_ABOVE:
            movement = max(0.30, current - MAX_THRESHOLD_STEP)
            explanation = (
                f"{precision:.0%} of surfaced items were used; the threshold is likely too strict "
                "and hiding useful items"
            )
        else:
            movement = current
            explanation = (
                f"{precision:.0%} used — inside the healthy band, leave it alone"
            )
        return movement, explanation


class _HealthComposite:
    def __init__(self, readings):
        self.items = readings

    @staticmethod
    def readings(precision, capture_passes, capture_errors, utilization, judge_rate):
        yield (
            "precision",
            precision,
            (
                "unmeasured — nothing surfaced yet"
                if precision is None
                else f"{precision:.0%} of surfacings were used"
            ),
        )
        if capture_passes <= 0:
            yield "capture", None, "unmeasured — no capture pass has run"
        else:
            clean = capture_passes - capture_errors
            yield "capture", max(
                0.0, clean / capture_passes
            ), f"{clean} of {capture_passes} pass(es) clean"
        yield (
            "utilization",
            None if utilization is None else _band_score(utilization),
            (
                "unmeasured — no ambient render recorded"
                if utilization is None
                else (
                    f"{utilization:.0%} of the context budget used "
                    f"(ideal {UTILIZATION_IDEAL_LOW:.0%}-{UTILIZATION_IDEAL_HIGH:.0%})"
                )
            ),
        )
        yield (
            "judge",
            None if judge_rate is None else 1.0 - judge_rate,
            (
                "unmeasured — no judge verdicts with human labels"
                if judge_rate is None
                else f"{judge_rate:.0%} of judged work was wrongly passed"
            ),
        )

    def payload(self):
        components = []
        measured = []
        for name, value, detail in self.items:
            score = None
            if value is not None:
                score = round(max(0.0, min(1.0, value)) * 100, 1)
            component = dict(
                name=name, score=score, weight=HEALTH_WEIGHTS[name], detail=detail
            )
            components.append(component)
            if score is not None:
                measured.append(component)
        denominator = sum(float(item["weight"]) for item in measured)
        score = None
        if denominator > 0:
            numerator = sum(
                float(item["score"]) * float(item["weight"]) for item in measured
            )
            score = round(numerator / denominator, 1)
        return dict(
            score=score,
            components=components,
            measured=len(measured),
            of=len(components),
            ideal_band=[UTILIZATION_IDEAL_LOW, UTILIZATION_IDEAL_HIGH],
        )
