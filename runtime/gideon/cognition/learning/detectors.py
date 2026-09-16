"""Plan scoring and failure classification contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .candidate_screening import FailureSignals, PlanAdmission, PlanShape

MIN_PLAN_STEPS = 2

MAX_BUDGET_BURN = 0.80

AUTO_FILE_SCORE = 0.70

DROP_SCORE = 0.35

HARDCODED_PENALTY = 1.0

SIMILARITY_THRESHOLD = 0.85
SIMILARITY_MIN_PRIORS = 2
SIMILARITY_WINDOW_DAYS = 30

_ACTION_VERBS = re.compile(
    r"\b(build|compile|run|test|deploy|fetch|read|write|update|create|delete|review|"
    r"summari[sz]e|analy[sz]e|validate|verify|publish|migrate|sync|notify|render|"
    r"extract|transform|load|clean|format|lint|commit|push|tag|release)\b",
    re.IGNORECASE,
)

_HARDCODED = re.compile(
    r"(/(?:Users|home|var|tmp|opt)/[\w./-]+)"
    r"|(https?://[\w./-]+)"
    r"|(\b[0-9a-f]{12,}\b)"
    r"|(\b[\w.-]+@[\w.-]+\.\w+\b)",
    re.IGNORECASE,
)

_SLOT = re.compile(r"(\{\{[^}]+\}\})|(\$[A-Za-z_][\w]*)|(<[a-z_][\w ]{2,}>)")


class Skip(str, Enum):
    """Why a candidate was declined. §3.2 requires a row for EVERY negative decision.

    Typed rather than prose because these are what thresholds get tuned against: "declined" is
    unfilterable, while a count per reason says which gate is doing the work and which is dead
    weight.
    """

    TOO_FEW_STEPS = "too_few_steps"
    TEMPLATE_EXISTS = "template_exists"
    BUDGET_BURN = "budget_burn"
    LOW_SCORE = "low_score"
    NO_SLOTS = "no_slots"
    TOO_FEW_PRIORS = "too_few_priors"
    STALE_PRIORS = "stale_priors"


@dataclass
class Candidate:
    """One ad-hoc run being considered for templating."""

    run_id: str
    steps: list[str] = field(default_factory=list)
    budget_burn: float = 0.0
    template_surfaced: bool = False
    intent: str = ""

    @property
    def text(self) -> str:
        return " \n".join(self.steps)


@dataclass
class Score:
    """The deterministic structural score, with its components visible.

    Components rather than one number, because §3.2 tunes thresholds from data and a scalar cannot
    say WHICH signal was weak. A candidate rejected for having no slots needs a different fix from
    one rejected for repeating a single verb.
    """

    verb_diversity: float = 0.0
    dependencies: float = 0.0
    slots: float = 0.0
    hardcoded: int = 0

    @property
    def total(self) -> float:
        """Weighted sum, clamped to [0, 1].

        Slots weigh most: a plan with no parameterizable slot cannot be reused however
        well-structured it is, so it is closest to the real question.
        """
        raw = 0.30 * self.verb_diversity + 0.25 * self.dependencies + 0.45 * self.slots
        return max(0.0, min(1.0, raw - HARDCODED_PENALTY * self.hardcoded * 0.1))

    def to_dict(self) -> dict[str, Any]:
        values = {
            name: round(getattr(self, name), 4)
            for name in ("verb_diversity", "dependencies", "slots")
        }
        values.update(hardcoded=self.hardcoded, total=round(self.total, 4))
        return values


def structural_score(candidate: Candidate) -> Score:
    return PlanShape().score(candidate)


class Action(str, Enum):
    """What the gate chain decided.

    `CONSULT` is the only branch that costs anything, the whole design: free at both extremes,
    paid only in the ambiguous middle.
    """

    AUTO_FILE = "auto_file"
    CONSULT = "consult"
    SKIP = "skip"


@dataclass
class GateDecision:
    """The chain's verdict for one candidate."""

    action: str
    score: Score = field(default_factory=Score)
    reason: str = ""
    skip_reason: str = ""

    @property
    def costs_a_model_call(self) -> bool:
        return self.action == Action.CONSULT.value

    def to_dict(self) -> dict[str, Any]:
        fields = dict(
            action=self.action, score=self.score.to_dict(), reason=self.reason
        )
        if self.skip_reason:
            fields.update(skip_reason=self.skip_reason)
        return fields


def gate(candidate: Candidate) -> GateDecision:
    return PlanAdmission().decide(candidate)


def similarity_verdict(
    *,
    matches: list[tuple[str, float, float]],
    now: float,
    threshold: float = SIMILARITY_THRESHOLD,
    min_priors: int = SIMILARITY_MIN_PRIORS,
    window_days: float = SIMILARITY_WINDOW_DAYS,
) -> GateDecision:
    return PlanAdmission().repeated(matches, threshold, min_priors, window_days)


class FailureMode(str, Enum):
    """The first-class failure dimension §3.3 (LEARN-R8a) puts on the Run Ledger.

    A closed enum, so `failure_distribution` is computable and the refiner targets the DOMINANT
    mode. Prose failure text cannot be counted, and a refiner that cannot count cannot choose..
    """

    SCHEMA_VIOLATION = "schema_violation"
    CONSTRAINT_VIOLATION = "constraint_violation"
    SPEC_MISMATCH = "spec_mismatch"
    TIMEOUT = "timeout"
    ENVIRONMENT = "environment"
    CODE = "code"
    CONFIG = "config"
    DATA = "data"
    INFRA = "infra"
    DEPENDENCY = "dependency"
    PROCESS = "process"
    UNKNOWN = "unknown"


FAILURE_MODES: tuple[str, ...] = tuple(m.value for m in FailureMode)

NON_LESSON_MODES: frozenset[str] = frozenset(
    {FailureMode.ENVIRONMENT.value, FailureMode.INFRA.value, FailureMode.TIMEOUT.value}
)

_MODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        FailureMode.SCHEMA_VIOLATION.value,
        re.compile(
            r"\b(schema|json ?decode|validation ?error|invalid (?:json|payload|shape)|"
            r"unexpected (?:field|key)|pydantic|missing required (?:field|key))\b",
            re.I,
        ),
    ),
    (
        FailureMode.CONSTRAINT_VIOLATION.value,
        re.compile(
            r"\b(constraint|unique|foreign key|not null|integrity ?error|"
            r"violates|assertion ?(?:error|failed))\b",
            re.I,
        ),
    ),
    (
        FailureMode.TIMEOUT.value,
        re.compile(r"\b(timed? ?out|timeout|deadline exceeded|took too long)\b", re.I),
    ),
    (
        FailureMode.DEPENDENCY.value,
        re.compile(
            r"\b(no module named|import ?error|module not found|package .* not|"
            r"unresolved (?:import|dependency)|version conflict)\b",
            re.I,
        ),
    ),
    (
        FailureMode.CONFIG.value,
        re.compile(
            r"\b(not configured|missing (?:env|environment|config|setting)|"
            r"unset (?:variable|env)|no such (?:profile|credential))\b",
            re.I,
        ),
    ),
    (
        FailureMode.SPEC_MISMATCH.value,
        re.compile(
            r"\b(does not match the (?:spec|contract)|output ?contract|"
            r"expected .* but (?:got|received))\b",
            re.I,
        ),
    ),
    (
        FailureMode.DATA.value,
        re.compile(
            r"\b(empty (?:result|response|file)|no rows|malformed (?:row|record|csv)|"
            r"encoding ?error|unicode ?(?:decode|error))\b",
            re.I,
        ),
    ),
    (
        FailureMode.CODE.value,
        re.compile(
            r"\b(traceback|attribute ?error|type ?error|name ?error|index ?error|"
            r"key ?error|zero ?division)\b",
            re.I,
        ),
    ),
)


def classify_failure(text: str) -> str:
    return FailureSignals().classify(text)


def failure_distribution(failures: list[str]) -> dict[str, int]:
    return FailureSignals().distribution(failures)


def dominant_mode(failures: list[str]) -> str:
    return FailureSignals().dominant(failures)


def lesson_worthy(text: str) -> tuple[bool, str]:
    return FailureSignals().worthiness(text)


@dataclass
class LessonKey:
    """The (template, failure_mode) key §3.3b stores failed-stage lessons under.

    Keyed rather than free-floating so the lesson can be RE-INJECTED on future runs of the same
    template — §3.3 calls a lesson "a persistent mutation hint", and a hint nobody can look up by
    template is a note in a drawer.
    """

    template: str
    mode: str
    signature: str = ""

    @property
    def key(self) -> str:
        template = self.template or "unknown"
        mode = self.mode or FailureMode.UNKNOWN.value
        suffix = [self.signature] if self.signature else []
        return "lesson." + ":".join([template, mode, *suffix])

    def to_dict(self) -> dict[str, Any]:
        fields = {
            name: getattr(self, name) for name in ("template", "mode", "signature")
        }
        return dict(fields, key=self.key)


def dedupe_signature(text: str, *, limit: int = 10) -> str:
    """The collapsed signature §3.3b keys lessons by.

    Reuses the refiner's noise-stripping so the same failure produces the same signature in BOTH
    spokes. Two signature schemes would make a clustered failure and its lesson un-joinable — the
    refiner would target a cluster whose lesson it could not find.
    """
    from gideon.cognition.learning.refiner import failure_signature

    return " ".join(failure_signature(text).split()[:limit])
