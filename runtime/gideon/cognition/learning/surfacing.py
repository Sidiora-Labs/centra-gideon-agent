from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

THRESHOLD_PROFILES: dict[str, float] = {
    "skill": 0.55,
    "lesson": 0.55,
    "memory": 0.55,
    "template": 0.62,
    "route": 0.62,
    "context": 0.50,
}

ENTITY_PRIORS: dict[str, float] = {
    "lesson": 1.05,
    "skill": 1.0,
    "memory": 1.0,
    "template": 0.98,
    "context": 0.95,
}

W_QUERY_OVERLAP = 0.55

W_SCORE = 0.45

RANK_DECAY = 0.85

RRF_K = 60

MAX_PER_SOURCE = 3

UNCAPPED_KINDS = frozenset({"lesson", "skill"})

FULL_BODY_KINDS = frozenset({"skill"})

L2_SCORE_FRACTION = 0.9

L2_MAX_ITEMS = 3

SLOT_ORDER = (
    ("system", 0, False),
    ("constraints", 1, False),
    ("lessons", 2, False),
    ("skills", 3, False),
    ("memory", 4, False),
    ("retrieved_context", 5, True),
)

_DEBUG_RE = re.compile(
    r"\b(bug|broken|fail(?:ed|ing|ure)?|error|crash|traceback|stack ?trace|"
    r"regress(?:ion|ed)|why (?:is|does|did)|not working|debug)\b",
    re.IGNORECASE,
)

_IDEATION_RE = re.compile(
    r"\b(design|approach|options?|brainstorm|should we|trade-?offs?|architect|"
    r"alternatives?|what if|explore)\b",
    re.IGNORECASE,
)

INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "debug": {"overlap": 0.65, "score": 0.35, "path_bonus": 0.25},
    "ideation": {"overlap": 0.45, "score": 0.55, "path_bonus": 0.05},
    "default": {"overlap": W_QUERY_OVERLAP, "score": W_SCORE, "path_bonus": 0.15},
}

ABLATABLE: tuple[str, ...] = (
    "intent",
    "path_bonus",
    "entity_prior",
    "rank_decay",
    "diversification",
)

AUTHORITY_PREAMBLE = (
    "The context below is AUTHORITATIVE and overrides your general priors.\n"
    "On conflict: the user's lessons win, then their stored preferences, then this "
    "session's context; your training is last.\n"
    "If something here already answers the question, do NOT re-derive or re-search it "
    "— cite it and act."
)

NULL_DELTA = 0.02


class Tier(str, Enum):
    L0 = "l0"
    L1 = "l1"
    L2 = "l2"


@dataclass
class Candidate:
    kind: str
    key: str
    score: float
    l0: str
    l1: str = ""
    l2: str = ""
    path_match: bool = False
    source_rank: int = 0
    salience: float = 0.0
    tier: Tier = Tier.L1
    max_tokens: int = 0
    arm: str = ""

    def text(self, tier: Tier) -> str:
        choices = (self.l0,)
        if tier is Tier.L2:
            choices = (self.l2, self.l1, self.l0)
        elif tier is Tier.L1:
            choices = (self.l1, self.l0)
        return next((value for value in choices[:-1] if value), choices[-1])


@dataclass
class Slot:
    name: str
    priority: int
    sacrificial: bool = False
    items: list[Candidate] = field(default_factory=list)


@dataclass
class Allocation:
    text: str
    used_tokens: int
    budget_tokens: int
    included: list[tuple[str, str, Tier]] = field(default_factory=list)
    near_misses: list[str] = field(default_factory=list)
    skipped_oversized: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    truncated_slot: str = ""

    @property
    def headroom(self) -> int:
        return max(0, self.budget_tokens - self.used_tokens)


def classify_intent(query: str) -> str:
    if query:
        for name, pattern in (("debug", _DEBUG_RE), ("ideation", _IDEATION_RE)):
            if pattern.search(query):
                return name
    return "default"


def query_overlap(query: str, text: str) -> float:
    vocabularies = [
        set(
            filter(
                lambda token: len(token) > 2,
                re.findall(r"[a-z0-9]+", (value or "").lower()),
            )
        )
        for value in (query, text)
    ]
    wanted, offered = vocabularies
    return (
        len(wanted.intersection(offered)) / len(wanted) if wanted and offered else 0.0
    )


def _check_ablate(ablate: str) -> str:
    if not ablate or ablate in ABLATABLE:
        return ablate
    raise ValueError(f"unknown ablation {ablate!r} — expected one of {ABLATABLE}")


def env_ablate() -> str:
    import os

    from gideon.assurance.evals.overlay import ABLATE_SURFACING_ENV

    configured = os.environ.get(ABLATE_SURFACING_ENV)
    return str(configured or "")


class _Salience:
    def __init__(self, intent: str, ablate: str) -> None:
        self.ablate = ablate
        profile = "default" if ablate == "intent" else intent
        self.weights = INTENT_WEIGHTS.get(profile, INTENT_WEIGHTS["default"])

    def measure(self, candidate: Candidate, query: str) -> float:
        lexical = query_overlap(query, f"{candidate.l0} {candidate.l1}")
        bounded = max(0.0, min(1.0, candidate.score))
        weighted = self.weights["overlap"] * lexical + self.weights["score"] * bounded
        decay = 1.0 if self.ablate == "rank_decay" else RANK_DECAY
        weighted *= decay ** max(0, candidate.source_rank)
        weighted *= (
            1.0
            if self.ablate == "entity_prior"
            else ENTITY_PRIORS.get(candidate.kind, 1.0)
        )
        if candidate.path_match and self.ablate != "path_bonus":
            weighted += self.weights["path_bonus"]
        return round(weighted, 6)


def score_candidate(
    cand: Candidate, query: str, intent: str = "default", *, ablate: str = ""
) -> float:
    return _Salience(intent, _check_ablate(ablate)).measure(cand, query)


def _stronger_arm(left: str, right: str) -> str:
    if left and right:
        from gideon.cognition.learning.measure import arm_confidence

        return left if arm_confidence(left) >= arm_confidence(right) else right
    return right if not left else left


class _RankPool:
    def __init__(self) -> None:
        self.observations: list[tuple[float, Candidate]] = []
        self.entities: dict[str, tuple[float, Candidate]] = {}

    def collect(self, sources: dict[str, list[Candidate]]) -> None:
        for candidates in sources.values():
            for position, candidate in enumerate(candidates):
                candidate.source_rank = position
                self.observations.append((1.0 / (RRF_K + position + 1), candidate))

    def reconcile(self) -> None:
        for weight, candidate in self.observations:
            identity = f"{candidate.kind}\x1f{candidate.key}"
            incumbent = self.entities.get(identity)
            if incumbent is None:
                self.entities[identity] = (weight, candidate)
                continue
            old_weight, old_candidate = incumbent
            if weight > old_weight:
                candidate.arm = _stronger_arm(candidate.arm, old_candidate.arm)
                self.entities[identity] = (weight, candidate)
            elif candidate.arm:
                old_candidate.arm = _stronger_arm(old_candidate.arm, candidate.arm)

    def ranked(self, ablate: str) -> list[Candidate]:
        candidates = [entry[1] for entry in self.entities.values()]
        candidates.sort(key=lambda candidate: candidate.salience, reverse=True)
        admitted: list[Candidate] = []
        occupancy: dict[str, int] = {}
        for candidate in candidates:
            limited = (
                candidate.kind not in UNCAPPED_KINDS and ablate != "diversification"
            )
            if limited:
                occupied = occupancy.get(candidate.kind, 0)
                if occupied >= MAX_PER_SOURCE:
                    continue
                occupancy[candidate.kind] = occupied + 1
            admitted.append(candidate)
        return admitted


def fuse(sources: dict[str, list[Candidate]], *, ablate: str = "") -> list[Candidate]:
    _check_ablate(ablate)
    pool = _RankPool()
    pool.collect(sources)
    pool.reconcile()
    return pool.ranked(ablate)


def count_tokens(text: str) -> int:
    if text:
        try:
            import tiktoken

            encoded = tiktoken.get_encoding("cl100k_base").encode(text)
            return len(encoded)
        except Exception:
            return max(1, (len(text) + 3) // 4)
    return 0


def _tier_fits(cost: int, used: int, budget: int, cap: int) -> bool:
    return not (used + cost > budget or (cap and cost > cap))


def _assign_detail(pool: list[Candidate]) -> None:
    peak = max((candidate.salience for candidate in pool), default=0.0)
    granted = 0
    for candidate in pool:
        candidate.tier = Tier.L1
        if not candidate.l2:
            continue
        if candidate.kind in FULL_BODY_KINDS:
            candidate.tier = Tier.L2
            continue
        if (
            granted < L2_MAX_ITEMS
            and peak > 0
            and candidate.salience >= L2_SCORE_FRACTION * peak
        ):
            candidate.tier = Tier.L2
            granted += 1


def _arrange_slots(
    pool: list[Candidate], order: tuple[tuple[str, int, bool], ...]
) -> list[Slot]:
    slots = {
        name: Slot(name, priority, sacrificial) for name, priority, sacrificial in order
    }
    placement = {
        "lesson": "lessons",
        "skill": "skills",
        "memory": "memory",
        "template": "skills",
        "context": "retrieved_context",
    }
    for candidate in pool:
        name = placement.get(candidate.kind, "retrieved_context")
        if name not in slots:
            slots[name] = Slot(name, 9, True)
        slots[name].items.append(candidate)
    return sorted(slots.values(), key=lambda slot: slot.priority)


class _PromptBudget:
    def __init__(self, tokens: int) -> None:
        self.result = Allocation(text="", used_tokens=0, budget_tokens=tokens)
        self.fragments: list[str] = []

    def fits(self, cost: int, candidate: Candidate) -> bool:
        return _tier_fits(
            cost,
            self.result.used_tokens,
            self.result.budget_tokens,
            candidate.max_tokens,
        )

    def preamble(self) -> None:
        cost = count_tokens(AUTHORITY_PREAMBLE)
        if cost <= self.result.budget_tokens:
            self.result.used_tokens += cost
            self.fragments.append(AUTHORITY_PREAMBLE)

    def choose(self, candidate: Candidate) -> tuple[str, int] | None:
        text = candidate.text(candidate.tier)
        cost = count_tokens(text)
        if self.fits(cost, candidate):
            return text, cost
        for tier in (Tier.L1, Tier.L0):
            if tier is candidate.tier:
                continue
            text = candidate.text(tier)
            cost = count_tokens(text)
            if text and self.fits(cost, candidate):
                candidate.tier = tier
                self.result.degraded.append(candidate.key)
                return text, cost
        return None

    def decline(self, candidate: Candidate, slot: Slot) -> None:
        if slot.sacrificial:
            self.result.truncated_slot = slot.name
        else:
            self.result.skipped_oversized.append(candidate.key)
        self.result.near_misses.append(candidate.l0 or candidate.key)

    def fill(self, slot: Slot) -> None:
        block: list[str] = []
        ranked = sorted(
            slot.items, key=lambda candidate: candidate.salience, reverse=True
        )
        for candidate in ranked:
            rendering = self.choose(candidate)
            if rendering is None:
                self.decline(candidate, slot)
                continue
            text, cost = rendering
            self.result.used_tokens += cost
            block.append(text)
            self.result.included.append((candidate.kind, candidate.key, candidate.tier))
        if block:
            self.fragments.append("\n".join(block))

    def finish(self) -> Allocation:
        if self.result.near_misses:
            catalogue = (
                "Also available on request (ask and it will be loaded):\n"
                + "\n".join(f"- {entry}" for entry in self.result.near_misses[:10])
            )
            if (
                self.result.used_tokens + count_tokens(catalogue)
                <= self.result.budget_tokens
            ):
                self.result.used_tokens += count_tokens(catalogue)
                self.fragments.append(catalogue)
        self.result.text = "\n\n".join(filter(None, self.fragments))
        return self.result


def allocate(
    sources: dict[str, list[Candidate]],
    *,
    query: str = "",
    budget_tokens: int = 4000,
    slot_order: tuple[tuple[str, int, bool], ...] = SLOT_ORDER,
    include_preamble: bool = True,
    ablate: str = "",
) -> Allocation:
    ablate = _check_ablate(ablate or env_ablate())
    intent = classify_intent(query)
    for candidates in sources.values():
        for candidate in candidates:
            candidate.salience = score_candidate(
                candidate, query, intent, ablate=ablate
            )
    pool = fuse(sources, ablate=ablate)
    if not pool:
        return Allocation(text="", used_tokens=0, budget_tokens=budget_tokens)
    _assign_detail(pool)
    slots = _arrange_slots(pool, slot_order)
    budget = _PromptBudget(budget_tokens)
    if include_preamble:
        budget.preamble()
    for slot in slots:
        budget.fill(slot)
    return budget.finish()


def _signature(alloc: Allocation) -> list[tuple[str, str, str]]:
    result = []
    for kind, key, tier in alloc.included:
        result.append((kind, key, tier.value))
    return result


def _delta(
    baseline: list[tuple[str, str, str]], ablated: list[tuple[str, str, str]]
) -> float:
    matches = sum(left == right for left, right in zip(baseline, ablated))
    return round(1.0 - matches / max(len(baseline), len(ablated), 1), 6)


def ablation_deltas(
    sources: dict[str, list[Candidate]], *, query: str = "", budget_tokens: int = 4000
) -> list[dict[str, Any]]:
    reference = _signature(allocate(sources, query=query, budget_tokens=budget_tokens))
    measurements = []
    for heuristic in ABLATABLE:
        alternate = allocate(
            sources, query=query, budget_tokens=budget_tokens, ablate=heuristic
        )
        difference = _delta(reference, _signature(alternate))
        measurements.append(
            dict(
                heuristic=heuristic,
                delta=difference,
                verdict="no_effect" if difference <= NULL_DELTA else "earns_its_place",
                items=len(reference),
            )
        )
    return sorted(measurements, key=lambda row: row["delta"])
