"""Tier proposals — what a workflow's own terminal runs say about the tier it should be.

Two moves, both read off the record rather than asked of a model:

* **Distillation.** An AGENTIC workflow whose terminal runs succeed and take the same
  path every time is paying for a model's judgement it never uses. The evidence for
  that claim is the trajectory: same signature, same step count, same branch routing,
  run after run.
* **Promotion.** A DETERMINISTIC workflow that keeps failing is a fixed path that does
  not fit its inputs. The evidence is the failure count, not one bad run.

**The tier is DERIVED, never read off a label.** A def carries no tier field, and one
bolted on would drift the moment an `infer` node was added. The structural rule is
:mod:`gideon.automation.workflows.judge_pretier`'s, carried across from judging a plan to
classifying one: work a free deterministic rule can settle is deterministic, and work that
needs a model call is not. Here that reads as the node algebra — a node on
:data:`~gideon.automation.workflows.models.LANE_LLM` (`stage`, `infer`, `visualize`) or a
`judge` gate spends a model call, so a spec containing one is AGENTIC and a spec containing
none is DETERMINISTIC. The walk is `models.walk`, so `cases`/`default`/`body` children are
counted rather than missed the way a hand-rolled traversal misses them.

**Nothing here calls a model.** Every number below is a projection over
`journal.ledger()` events and `store` run rows, reusing `introspection`'s trajectory
signature and edge projection rather than minting a second definition of "the path a run
took". An analysis that asked a model whether a workflow "feels deterministic" would cost
tokens to produce an opinion the ledger already states as a fact.

The pure half (`derive_tier`, `trajectory_of`, `analyze`, `dedupe`) takes data and returns
records. The wired half (`collect`, `review_workflow`) reads the store and files through
:mod:`gideon.cognition.learning.proposals`, whose `TIER_MIGRATION` kind, `target`-keyed
`resolve` cascade and decision memory already own deduplication, the human gate and the
inbox surface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.workflows import introspection
from gideon.automation.workflows.models import (
    LANE_LLM,
    GateKind,
    Node,
    NodeKind,
    RunStatus,
    WorkflowRun,
    walk,
)

logger = logging.getLogger(__name__)

#: How many of a workflow's recent runs the wired analysis reads. Bounded for the same
#: reason the introspection card is: a personal instance accumulates runs forever.
ANALYSIS_RUN_LIMIT = 50

#: The sample floor. "Two runs went the same way" is not evidence of low variance, and
#: "two runs failed" is not a repeatedly-failing workflow.
MIN_DECIDED_RUNS = 5

#: At or below this, the successful trajectories are one path with noise.
LOW_VARIANCE_MAX = 0.2

DISTILL_MIN_SUCCESS_RATE = 0.9

PROMOTE_MAX_SUCCESS_RATE = 0.5

#: "Repeatedly failing" is a count, not a rate: one failure in two runs is 50%.
PROMOTE_MIN_FAILURES = 3


class Tier(str, Enum):
    """What a workflow's STRUCTURE says it is."""

    DETERMINISTIC = "deterministic"
    AGENTIC = "agentic"


class Action(str, Enum):
    """The two tier moves the record can justify."""

    DISTILL = "distill"
    PROMOTE = "promote"


class Outcome(str, Enum):
    """What one terminal run counts as.

    `CANCELLED` is UNDECIDED rather than a failure: a user aborting a run says nothing
    about whether the workflow's tier fits. `ESCALATED` is a failure — the run could not
    finish on its own, which is exactly the claim a promotion is made on.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    UNDECIDED = "undecided"


_OUTCOMES: dict[RunStatus, Outcome] = {
    RunStatus.COMPLETE: Outcome.SUCCESS,
    RunStatus.FAILED: Outcome.FAILURE,
    RunStatus.ESCALATED: Outcome.FAILURE,
    RunStatus.CANCELLED: Outcome.UNDECIDED,
}


def spends_a_model_call(node: Node) -> bool:
    """Whether this one node needs a model to produce its output.

    The `judge` gate is included because a rubric gate is a model call wearing a gate's
    kind — a spec whose only model use is its judge is still agentic, and counting only
    `LANE_LLM` nodes would file it for a distillation it cannot survive.
    """
    if node.lane == LANE_LLM:
        return True
    return (
        node.kind is NodeKind.GATE
        and str((node.config or {}).get("kind", "") or "") == GateKind.JUDGE.value
    )


def derive_tier(root: Node) -> Tier:
    """The tier of a spec tree, from its structure alone."""
    return (
        Tier.AGENTIC
        if any(spends_a_model_call(node) for _path, node in walk(root))
        else Tier.DETERMINISTIC
    )


def tier_of_spec(spec: dict[str, Any] | None) -> Tier | None:
    """The tier of a run's pinned spec document, or None when it cannot be read.

    None rather than a default: a spec that failed to parse is an unknown structure, and
    guessing DETERMINISTIC would propose promoting a workflow nobody measured.
    """
    root = (spec or {}).get("root")
    if not isinstance(root, dict):
        return None
    try:
        return derive_tier(Node.from_dict(root))
    except Exception:
        logger.debug("tier proposals: unreadable spec root", exc_info=True)
        return None


def _dispersion(values: list[Any]) -> float:
    """The share of observations OFF the dominant value — 0.0 when they all agree.

    Not a distinct-value count: two classes split evenly across six runs and two classes
    split five-to-one are the same count and nothing like the same variance, and only the
    second is a fixed path with one outlier.
    """
    if not values:
        return 0.0
    counts: dict[Any, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return 1.0 - max(counts.values()) / len(values)


def _spread(values: list[int]) -> float:
    """Relative spread of a set of counts, 0.0 when they are all equal."""
    usable = [value for value in values if value >= 0]
    if len(usable) < 2 or not max(usable):
        return 0.0
    return (max(usable) - min(usable)) / max(usable)


@dataclass(frozen=True)
class Variance:
    """How much a workflow's successful runs differ from one another.

    Three measures because they fail independently: a workflow can take the same node
    sequence with wildly different step counts (a loop), or the same step count down
    different branch legs. `score` is the worst of them — a workflow is low-variance only
    when every axis says so.
    """

    path: float = 0.0
    length: float = 0.0
    branch: float = 0.0

    @property
    def score(self) -> float:
        return max(self.path, self.length, self.branch)

    def to_dict(self) -> dict[str, float]:
        return {
            "path": round(self.path, 4),
            "length": round(self.length, 4),
            "branch": round(self.branch, 4),
            "score": round(self.score, 4),
        }


@dataclass(frozen=True)
class RunTrajectory:
    """One terminal run, projected to what a tier decision needs.

    Carries the run's tier rather than the workflow's: a def edited from deterministic to
    agentic makes its older runs evidence about a structure that no longer exists, and
    :func:`analyze` drops them for exactly that reason.
    """

    run_id: str
    tier: Tier
    outcome: Outcome
    signature: str
    steps: int
    branch_route: tuple[tuple[str, str], ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.outcome is Outcome.SUCCESS

    @property
    def decided(self) -> bool:
        return self.outcome is not Outcome.UNDECIDED


def run_outcome(run: WorkflowRun) -> Outcome:
    """What a run counts as. A non-terminal run is UNDECIDED — it has not finished."""
    if not run.is_terminal:
        return Outcome.UNDECIDED
    return _OUTCOMES.get(run.status, Outcome.UNDECIDED)


def _branch_route(events: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    """The `(branch path, case)` pairs one run actually routed through.

    Read through `introspection.edge_stats` over this single run rather than a private
    walk of `.cases[...]` segments, so "which case did this run take" has one definition
    across the engine.
    """
    stats = introspection.edge_stats([events])
    return tuple(
        sorted(
            (path, label)
            for path, branch in stats.branches.items()
            for label, count in branch.cases.items()
            if count > 0
        )
    )


def trajectory_of(
    run: WorkflowRun, events: list[dict[str, Any]], *, tier: Tier
) -> RunTrajectory:
    """Project one terminal run's ledger into a trajectory. Pure over `events`."""
    signature = introspection.trajectory_signature(run.id, events)
    return RunTrajectory(
        run_id=run.id,
        tier=tier,
        outcome=run_outcome(run),
        signature=signature.signature,
        steps=signature.length,
        branch_route=_branch_route(events),
    )


@dataclass(frozen=True)
class TierProposal:
    """A proposed tier move, with the measurements that justify it."""

    workflow_name: str
    action: Action
    from_tier: Tier
    to_tier: Tier
    runs: int
    successes: int
    failures: int
    success_rate: float
    variance: Variance = field(default_factory=Variance)
    evidence_run_ids: tuple[str, ...] = ()

    @property
    def dedupe_key(self) -> str:
        """Stable identity of the FINDING, not of the measurement behind it.

        Deliberately free of run counts and rates: those move with every new run, and a
        key that moved with them would file a fresh proposal for the same recommendation
        every time the workflow ran. Used as the queue `target`, which is what makes the
        existing `resolve` cascade recognise a re-file.
        """
        return (
            f"workflow-tier:{self.workflow_name}:"
            f"{self.from_tier.value}->{self.to_tier.value}"
        )

    @property
    def confidence(self) -> float:
        rate = (
            self.success_rate
            if self.action is Action.DISTILL
            else 1.0 - self.success_rate
        )
        return round(rate, 4)

    def title(self) -> str:
        verb = (
            "Distil into a deterministic workflow"
            if self.action is Action.DISTILL
            else "Promote to an agentic workflow"
        )
        return f"{verb}: {self.workflow_name}"[:120]

    def body(self) -> str:
        if self.action is Action.DISTILL:
            claim = (
                f"`{self.workflow_name}` is agentic by structure, but its last "
                f"{self.runs} terminal runs succeeded {self.success_rate:.0%} of the "
                f"time along the same trajectory (path variance "
                f"{self.variance.path:.2f}, step-count variance "
                f"{self.variance.length:.2f}, branch variance "
                f"{self.variance.branch:.2f}). A model is deciding a path that does not "
                "vary, so the same work runs as a fixed sequence for no tokens."
            )
        else:
            claim = (
                f"`{self.workflow_name}` is deterministic by structure and failed "
                f"{self.failures} of its last {self.runs} terminal runs "
                f"({1 - self.success_rate:.0%}). A fixed path that keeps failing does "
                "not fit its inputs; an agentic step can adapt where the fixed one "
                "cannot."
            )
        evidence = ", ".join(self.evidence_run_ids[:8]) or "(none recorded)"
        return f"{claim}\n\nEvidence runs: {evidence}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "action": self.action.value,
            "from_tier": self.from_tier.value,
            "to_tier": self.to_tier.value,
            "runs": self.runs,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 4),
            "variance": self.variance.to_dict(),
            "evidence_run_ids": list(self.evidence_run_ids),
            "dedupe_key": self.dedupe_key,
        }


def variance_of(trajectories: list[RunTrajectory]) -> Variance:
    """Trajectory variance across a set of runs. Pure, and 0.0 for a single run."""
    return Variance(
        path=_dispersion([t.signature for t in trajectories]),
        length=_spread([t.steps for t in trajectories]),
        branch=_dispersion([t.branch_route for t in trajectories]),
    )


def analyze(
    workflow_name: str,
    trajectories: list[RunTrajectory],
    *,
    min_runs: int = MIN_DECIDED_RUNS,
    low_variance_max: float = LOW_VARIANCE_MAX,
    distill_min_success: float = DISTILL_MIN_SUCCESS_RATE,
    promote_max_success: float = PROMOTE_MAX_SUCCESS_RATE,
    promote_min_failures: int = PROMOTE_MIN_FAILURES,
) -> TierProposal | None:
    """The decision, as a pure function of terminal trajectories. OLDEST first.

    The current tier is the newest trajectory's, and only runs sharing it count as
    evidence — runs from before a structural edit measured a different workflow. Variance
    is measured over the SUCCESSFUL runs alone, because a failed run's path is truncated
    at the failure and would read as variance the workflow does not have.

    Returns None whenever the record does not support a move: too few decided runs, the
    wrong tier for the finding, a success rate in between, or a path that varies.
    """
    decided = [t for t in trajectories if t.decided]
    if not decided:
        return None
    tier = decided[-1].tier
    scoped = [t for t in decided if t.tier is tier]
    if len(scoped) < min_runs:
        return None
    successes = [t for t in scoped if t.succeeded]
    failures = len(scoped) - len(successes)
    rate = len(successes) / len(scoped)
    variance = variance_of(successes)

    if (
        tier is Tier.AGENTIC
        and rate >= distill_min_success
        and variance.score <= low_variance_max
    ):
        return TierProposal(
            workflow_name=workflow_name,
            action=Action.DISTILL,
            from_tier=Tier.AGENTIC,
            to_tier=Tier.DETERMINISTIC,
            runs=len(scoped),
            successes=len(successes),
            failures=failures,
            success_rate=rate,
            variance=variance,
            evidence_run_ids=tuple(t.run_id for t in successes),
        )
    if (
        tier is Tier.DETERMINISTIC
        and failures >= promote_min_failures
        and rate <= promote_max_success
    ):
        return TierProposal(
            workflow_name=workflow_name,
            action=Action.PROMOTE,
            from_tier=Tier.DETERMINISTIC,
            to_tier=Tier.AGENTIC,
            runs=len(scoped),
            successes=len(successes),
            failures=failures,
            success_rate=rate,
            variance=variance,
            evidence_run_ids=tuple(t.run_id for t in scoped if not t.succeeded),
        )
    return None


def dedupe(proposals: list[TierProposal]) -> list[TierProposal]:
    """Collapse equivalent proposals, keeping the first of each `dedupe_key`."""
    seen: dict[str, TierProposal] = {}
    for proposal in proposals:
        seen.setdefault(proposal.dedupe_key, proposal)
    return list(seen.values())


def collect(
    workflow_name: str, *, limit: int = ANALYSIS_RUN_LIMIT
) -> list[RunTrajectory]:
    """Read a workflow's recent TERMINAL runs as trajectories, oldest first.

    A run still going contributes nothing — its path is incomplete, and half a trajectory
    is not a shorter one. A run whose pinned spec cannot be read is skipped rather than
    assigned a tier, so an unreadable spec costs one sample instead of inventing one.
    """
    from gideon.automation.workflows import journal as journal_mod
    from gideon.automation.workflows import store

    runs, _total = store.list_runs(workflow_name=workflow_name, limit=limit)
    out: list[RunTrajectory] = []
    for run in sorted(runs, key=lambda r: getattr(r, "created_at", "") or ""):
        if not run.id or not run.is_terminal:
            continue
        tier = tier_of_spec(store.read_spec(run.id))
        if tier is None:
            continue
        out.append(trajectory_of(run, journal_mod.ledger(run.id), tier=tier))
    return out


def file_proposals(proposals: list[TierProposal]) -> int:
    """File deduplicated proposals into the learning queue. Returns how many landed.

    The queue's `TIER_MIGRATION` kind is the existing home for a tier move, and keying
    `target` off :attr:`TierProposal.dedupe_key` is what hands deduplication to the
    cascade that already owns it: a pending proposal with the same target is reinforced
    or superseded rather than duplicated, and a rejected one stays rejected through its
    cooldown.
    """
    from gideon.cognition.learning import proposals as learning_proposals

    filed = 0
    for proposal in dedupe(proposals):
        _verdict, record = learning_proposals.enqueue(
            kind=learning_proposals.Kind.TIER_MIGRATION.value,
            title=proposal.title(),
            body=proposal.body(),
            target=proposal.dedupe_key,
            provenance="inferred",
            source_cadence="workflow_tier",
            evidence_refs=[f"run:{run_id}" for run_id in proposal.evidence_run_ids],
            evidence_strength="correlated",
            confidence=proposal.confidence,
            tags=["workflow_tier", proposal.action.value],
            occurrences=proposal.runs,
            min_evidence=MIN_DECIDED_RUNS,
        )
        filed += int(record is not None)
    return filed


def review_workflow(
    workflow_name: str, *, limit: int = ANALYSIS_RUN_LIMIT
) -> TierProposal | None:
    """The wired entry point: read one workflow's terminal runs, decide, file.

    Called from the engine's single terminal writer, because a terminal run is exactly
    when the evidence changed. Returns the proposal it derived (filed or already
    pending), or None when the record does not support a move.
    """
    if not workflow_name:
        return None
    proposal = analyze(workflow_name, collect(workflow_name, limit=limit))
    if proposal is None:
        return None
    file_proposals([proposal])
    return proposal
