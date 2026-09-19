"""The capped self-model — reinforcement-promoted, propose-don't-write (LEARN-R21 / §2.6 — S72).

The flywheel's ONLY mechanism that learns from what quietly WORKS. Every other cadence learns from
corrections and failures, which means the system can only ever discover what it did wrong; this one
notices a habit that keeps succeeding and offers to make it a principle.

That asymmetry is also what makes it dangerous, so §2.6 constrains it three ways and this module
enforces all three mechanically rather than by convention:

1. **Propose, never install.** A crossed threshold produces a PROPOSAL in the unified queue, exactly
   like a lesson. Nothing here writes a principle into memory. A system that promotes its own
   behavioural rules is a system whose behaviour the user cannot predict, and the plan says "never
   self-installed" for that reason.
2. **Bounded by construction.** ~6 active principles, ~4 working theories, ~4 current-focus entries,
   a small retrospection ring. Promotion into a FULL cap requires DISPLACING an existing entry, so
   bloat is impossible at the schema level rather than policed by a later cleanup that may not run.
3. **Only a compact snapshot injects.** One budgeted slot in §2.4's allocator, never the history.

**Measured before writing.** `user.selfmodel.*` was NOT in `_NON_FACT_KEY_CLAUSE`, so a principle
the harness observed about its own working patterns would have rendered as a FACT ABOUT THE USER — a
category error and a leak. The exclusion landed with this session. `user.*` was already in
`_BUILTIN_PREFIXES`, so no allowlist change was needed; measuring both saved inventing one.

Pure decisions over records. The observer's writes go through `MemoryService`, and the proposals go
through `learning.proposals` — this module builds neither store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

KEY_PREFIX = "user.selfmodel"

MIN_SEEN_COUNT = 2
MIN_CONFIDENCE = 0.72

MIN_SEEN_BY_FACET: dict[str, int] = {
    "principle": 3,
}


def min_seen_for(facet: str) -> int:
    """The reinforcement count *facet* needs before promotion is even considered."""
    return MIN_SEEN_BY_FACET.get(facet, MIN_SEEN_COUNT)


CAPS: dict[str, int] = {
    "principle": 6,
    "theory": 4,
    "focus": 4,
    "retrospection": 8,
}


class Facet(str, Enum):
    """What kind of self-knowledge an entry holds.

    Four kinds rather than one bag, because they have different lifetimes and different authority. A
    principle is always-on and constraint-like; a theory is explicitly provisional; a focus is
    short-lived; a retrospection is history. Collapsing them would let a guess inject with the
    weight of a rule.
    """

    PRINCIPLE = "principle"
    THEORY = "theory"
    FOCUS = "focus"
    RETROSPECTION = "retrospection"


FACETS: tuple[str, ...] = tuple(f.value for f in Facet)


class Reaction(str, Enum):
    """The user's observed response to a turn — the reinforcement signal.

    Mechanically observed, never asked for. §2.5's rule applies here too: a voluntary "was that
    good?" is ornamental, so the signal has to be something the user DID.
    """

    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    ABANDONED = "abandoned"
    NEUTRAL = "neutral"


REACTION_WEIGHT: dict[str, float] = {
    Reaction.ACCEPTED.value: 1.0,
    Reaction.CORRECTED.value: -2.0,
    Reaction.ABANDONED.value: -0.5,
    Reaction.NEUTRAL.value: 0.0,
}


@dataclass
class Observation:
    """One recorded turn: what the harness did, and what happened next.

    The tuple §2.6 names — route, tools, outcome, reaction. Deliberately NOT the turn's content: the
    self-model is about working patterns, and storing prompts would make it a transcript with a cap.
    """

    pattern: str
    route: str = ""
    tools: tuple[str, ...] = ()
    succeeded: bool = True
    reaction: str = Reaction.NEUTRAL.value
    at: str = ""

    @property
    def evidence(self) -> float:
        """This observation's contribution to a pattern's confidence.

        A FAILED turn contributes nothing positive even when the user accepted the result — they may
        have accepted a partial answer and moved on. Reading acceptance-after-failure as
        reinforcement is how a broken habit gets promoted.
        """
        weight = REACTION_WEIGHT.get(self.reaction, 0.0)
        if not self.succeeded:
            return min(0.0, weight)
        return weight

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "route": self.route,
            "tools": list(self.tools),
            "succeeded": self.succeeded,
            "reaction": self.reaction,
            "at": self.at,
        }


@dataclass
class Reinforcement:
    """The accumulated evidence for one candidate pattern."""

    pattern: str
    seen_count: int = 0
    score: float = 0.0
    observations: list[Observation] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return _EvidenceWindow(self.observations).confidence()

    @property
    def promotable(self) -> bool:
        """Both §2.6 thresholds, as a conjunction.

        `seen_count` alone promotes a coincidence that happened twice; confidence alone promotes one
        strongly-felt observation. Neither is evidence of a habit on its own.
        """
        return self.seen_count >= MIN_SEEN_COUNT and self.confidence >= MIN_CONFIDENCE

    def promotable_for(self, facet: str) -> bool:
        """The same conjunction, at *facet*'s own `seen_count` bar (MGAV-8).

        `promotable` keeps the floor so existing callers are unchanged; a principle is checked
        through here because its bar is higher — see `MIN_SEEN_BY_FACET`.
        """
        return (
            self.seen_count >= min_seen_for(facet) and self.confidence >= MIN_CONFIDENCE
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "seen_count": self.seen_count,
            "confidence": round(self.confidence, 4),
            "promotable": self.promotable,
            "observations": len(self.observations),
        }


def reinforce(
    existing: Reinforcement | None, observation: Observation
) -> Reinforcement:
    return _EvidenceWindow.advance(existing, observation)


@dataclass
class Entry:
    """One live self-model entry.

    `evidence` carries the reinforcement provenance §2.6 requires: an accepted principle must be
    able to show WHY it exists, because "the system decided this about itself" is not an auditable
    explanation.
    """

    facet: str
    key: str
    body: str
    seen_count: int = 0
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    created_at: str = ""
    last_seen_at: str = ""

    @property
    def memory_key(self) -> str:
        return f"{KEY_PREFIX}.{self.facet}.{self.key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "facet": self.facet,
            "key": self.key,
            "body": self.body,
            "seen_count": self.seen_count,
            "confidence": round(self.confidence, 4),
            "evidence": list(self.evidence),
            "memory_key": self.memory_key,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class PromotionPlan:
    """What promoting a pattern would do — including who gets displaced.

    A PLAN rather than an action, for the same reason the proposal is a proposal: displacing an
    existing principle is a real loss, and the user should see it named before it happens.
    """

    facet: str
    pattern: str
    allowed: bool
    reason: str = ""
    displaces: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "facet": self.facet,
            "pattern": self.pattern,
            "allowed": self.allowed,
            "reason": self.reason,
            "displaces": self.displaces,
        }


def plan_promotion(
    *, facet: str, reinforcement: Reinforcement, current: list[Entry]
) -> PromotionPlan:
    return _FacetPopulation(current).promotion(facet, reinforcement)


def over_cap(entries: list[Entry]) -> dict[str, int]:
    return _FacetPopulation(entries or []).overflow()


def trim_ring(
    entries: list[Entry], facet: str = Facet.RETROSPECTION.value
) -> list[Entry]:
    return _FacetPopulation(entries or []).bounded(facet)


PROPOSAL_KIND = "lesson_batch"

PROVENANCE = "observed-reinforcement"


@dataclass
class PrincipleProposal:
    """A behavioural principle offered for review. Never applied by this module."""

    facet: str
    pattern: str
    body: str
    seen_count: int
    confidence: float
    evidence: list[str] = field(default_factory=list)
    displaces: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": PROPOSAL_KIND,
            "provenance": PROVENANCE,
            "facet": self.facet,
            "pattern": self.pattern,
            "body": self.body,
            "seen_count": self.seen_count,
            "confidence": round(self.confidence, 4),
            "evidence": list(self.evidence),
            "displaces": self.displaces,
        }


def build_proposal(
    *, facet: str, reinforcement: Reinforcement, plan: PromotionPlan, body: str = ""
) -> PrincipleProposal | None:
    if plan.allowed:
        return _EvidenceWindow(reinforcement.observations).proposal(
            facet, reinforcement, plan.displaces, body
        )
    return None


def proposal_fingerprint(proposal: PrincipleProposal) -> str:
    """The dedup fingerprint, via the shared `proposals.content_fingerprint`.

    Reuses the existing function rather than hashing here, so a re-proposed principle collides with
    its own prior ACCEPTED/REJECTED decision in the shared store. A second hashing scheme would
    make the self-model the one proposer that can re-file something the user already declined.
    """
    from gideon.cognition.learning.proposals import content_fingerprint

    return content_fingerprint(
        PROPOSAL_KIND, f"{KEY_PREFIX}.{proposal.facet}", proposal.body
    )


SNAPSHOT_MAX_CHARS = 700

SNAPSHOT_FACETS: tuple[str, ...] = (
    Facet.PRINCIPLE.value,
    Facet.FOCUS.value,
    Facet.THEORY.value,
)


def snapshot(entries: list[Entry], *, limit: int = SNAPSHOT_MAX_CHARS) -> str:
    if entries:
        return _FacetPopulation(entries).render(limit)
    return ""


class _EvidenceWindow:
    def __init__(self, observations):
        self.observations = observations

    def confidence(self):
        graded = tuple(
            item
            for item in self.observations
            if item.reaction != Reaction.NEUTRAL.value
        )
        if not graded:
            return 0.0
        totals = []
        for transform in (
            lambda value: max(0.0, value),
            lambda value: abs(min(0.0, value)),
        ):
            totals.append(sum(transform(item.evidence) for item in graded))
        denominator = totals[0] + totals[1]
        return totals[0] / denominator if denominator > 0 else 0.0

    @staticmethod
    def advance(previous, observation):
        count = (previous.seen_count if previous else 0) + 1
        score = (previous.score if previous else 0.0) + observation.evidence
        history = list(previous.observations) if previous else []
        return Reinforcement(observation.pattern, count, score, [*history, observation])

    def proposal(self, facet, record, displaced, body):
        evidence = []
        for observation in self.observations[-3:]:
            parts = [
                f"{observation.reaction}",
                "after",
                "success" if observation.succeeded else "failure",
            ]
            if observation.route:
                parts.extend(("via", f"{observation.route}"))
            evidence.append(" ".join(parts))
        return PrincipleProposal(
            facet=facet,
            pattern=record.pattern,
            body=body or record.pattern,
            seen_count=record.seen_count,
            confidence=record.confidence,
            evidence=evidence,
            displaces=displaced,
        )


class _FacetPopulation:
    def __init__(self, entries):
        self.entries = entries

    def promotion(self, facet, record):
        answer = PromotionPlan(facet=facet, pattern=record.pattern, allowed=False)
        if facet not in FACETS:
            answer.reason = (
                f"unknown facet {facet!r}; expected one of {', '.join(FACETS)}"
            )
            return answer
        if not record.promotable_for(facet):
            answer.reason = (
                f"seen {record.seen_count}× at {record.confidence:.0%} confidence; "
                f"needs {min_seen_for(facet)}× and {MIN_CONFIDENCE:.0%}"
            )
            return answer
        residents = self.members(facet)
        capacity = CAPS.get(facet, 0)
        if len(residents) < capacity:
            answer.allowed = True
            return answer
        incumbent = min(residents, key=self.retention_order)
        if record.confidence <= incumbent.confidence:
            answer.reason = (
                f"the {facet} cap of {capacity} is full and the weakest entry ({incumbent.key}) is at "
                f"{incumbent.confidence:.0%} — a newcomer must BEAT it, not tie it"
            )
        else:
            answer.allowed = True
            answer.reason = f"the {facet} cap of {capacity} is full; this would displace {incumbent.key}"
            answer.displaces = incumbent.key
        return answer

    @staticmethod
    def retention_order(entry):
        return entry.confidence, entry.seen_count, entry.created_at

    def members(self, facet):
        return list(filter(lambda item: item.facet == facet, self.entries))

    def overflow(self):
        from collections import Counter

        population = Counter(item.facet for item in self.entries)
        excess = {}
        for facet, count in population.items():
            if facet not in CAPS:
                continue
            remaining = count - CAPS[facet]
            if remaining > 0:
                excess[facet] = remaining
        return excess

    def bounded(self, facet):
        recent = self.members(facet)
        untouched = list(filter(lambda item: item.facet != facet, self.entries))
        capacity = CAPS.get(facet, 0)
        recent.sort(key=lambda item: item.last_seen_at or item.created_at, reverse=True)
        untouched.extend(recent[:capacity])
        return untouched

    def render(self, limit):
        groups: dict = {}
        for entry in self.entries:
            if entry.facet in SNAPSHOT_FACETS:
                groups.setdefault(entry.facet, []).append(entry)
        headings = {
            Facet.PRINCIPLE.value: "How I work with you:",
            Facet.FOCUS.value: "Currently working on:",
            Facet.THEORY.value: "Unproven working theories:",
        }
        pending = []
        for facet in SNAPSHOT_FACETS:
            members = sorted(groups.get(facet, []), key=lambda item: -item.confidence)
            if members:
                pending.append(headings[facet])
                pending.extend(f"- {member.body}" for member in members)
        boundary = 0
        consumed = 0
        for index, line in enumerate(pending):
            consumed += len(line) + 1
            if consumed > limit:
                break
            boundary = index + 1
        while boundary and pending[boundary - 1].endswith(":"):
            boundary -= 1
        return "\n".join(pending[:boundary])
