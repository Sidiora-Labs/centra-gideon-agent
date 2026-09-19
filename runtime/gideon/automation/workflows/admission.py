"""Pure capacity composition, step admission snapshots and ranked ready-work selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from gideon.automation.loop.tick import (
    Action,
    Decision,
    StepConfig,
    TickConfig,
    TickState,
)
from gideon.automation.loop.tick import evaluate as evaluate_step
from gideon.automation.loop.tick import step_config_from_phase
from gideon.automation.workflows import pool
from gideon.automation.workflows.models import LANE_COMPUTE, LANE_IO, LANE_LLM, Node

DEFAULT_LANE_CAPS = {LANE_LLM: 4, LANE_IO: 2, LANE_COMPUTE: 64}


class Scope(str, Enum):
    """Which bucket a request is about. A policy answers one scope and abstains on the rest."""

    LANE = "lane"
    CONTAINER = "container"
    RESOURCE = "resource"
    STEP = "step"


class Hold(str, Enum):
    """How a refusal by this policy is reported — the `wip_held` vs `deferred` distinction."""

    DEFERRED = "deferred"
    WIP_HELD = "wip_held"
    UNRECORDED = ""
    LEASED = "leased"
    BAKING = "baking"
    REGRESSED = "regressed"


RANK_CAPACITY = 0
RANK_INVARIANT = 10
RANK_EXCLUSION = 20
RANK_REGRESSION = 30


@dataclass(frozen=True)
class AdmissionRequest:
    """One admission question. Carries only what the policies read, so a policy cannot reach
    sideways into engine state and quietly become impure."""

    scope: Scope
    key: str
    node: Node | None = None


class AdmissionPolicy:
    """One rule of the form *(declaration) → capacity for a bucket*."""

    name: str = ""
    hold: Hold = Hold.UNRECORDED
    rank: int = RANK_CAPACITY

    def capacity(self, request: AdmissionRequest) -> int | None:
        """The most this policy will allow in `request`'s bucket, or `None` to abstain."""
        raise NotImplementedError


@dataclass(frozen=True)
class Limits:
    """Per-lane concurrency caps, as a config carries them. A single total is accepted and split, so"""

    lanes: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LANE_CAPS))

    @classmethod
    def from_config(cls, value: Any) -> Limits:
        if isinstance(value, dict):
            caps = dict(DEFAULT_LANE_CAPS)
            for key, raw in value.items():
                lane = str(key)
                if lane in caps:
                    parsed = _positive_cap(raw)
                    if parsed is not None:
                        caps[lane] = parsed
            return cls(lanes=caps)
        total = _positive_cap(value)
        if total is None:
            return cls()
        io = max(1, total // 3)
        return cls(
            lanes=dict(
                zip((LANE_LLM, LANE_IO, LANE_COMPUTE), (max(1, total - io), io, 64))
            )
        )

    def cap(self, lane: str) -> int:
        return int(self.lanes.get(lane, DEFAULT_LANE_CAPS.get(lane, 1)))


@dataclass(frozen=True)
class Lane(AdmissionPolicy):
    """Typed-lane caps as an admission policy (WF2-R21). A `foreach` over minute-long local-model"""

    limits: Limits = field(default_factory=Limits)

    name = "lane"
    hold = Hold.DEFERRED
    rank = RANK_CAPACITY

    def capacity(self, request: AdmissionRequest) -> int | None:
        if request.scope != Scope.LANE:
            return None
        return self.limits.cap(request.key)


@dataclass(frozen=True)
class ContainerConcurrency(AdmissionPolicy):
    """A fan-out's declared `max_concurrency` — how many ITEMS may be in flight at once."""

    name = "max_concurrency"
    hold = Hold.UNRECORDED
    rank = RANK_CAPACITY

    def capacity(self, request: AdmissionRequest) -> int | None:
        config = _node_config(request, Scope.CONTAINER)
        if config is not None:
            value = config.get("max_concurrency")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        return None


@dataclass(frozen=True)
class Wip(AdmissionPolicy):
    """The run-level WIP=1 invariant (`single_active_feature`, LOOPS-EVOLUTION R5b: +37% feature"""

    active: bool = False

    name = "single_active_feature"
    hold = Hold.WIP_HELD
    rank = RANK_INVARIANT

    def capacity(self, request: AdmissionRequest) -> int | None:
        if request.scope != Scope.CONTAINER or not self.active:
            return None
        return 1


@dataclass(frozen=True)
class AdmissionState:
    """The clock-and-disk inputs the `PP-12` policies read, gathered ONCE by the caller."""

    now: float = 0.0
    holder: str = ""
    leases: Mapping[str, pool.Lease] = field(default_factory=dict)
    lease_ttl_secs: int = pool.DEFAULT_LEASE_SECS
    since: Mapping[str, float] = field(default_factory=dict)
    metrics: Mapping[str, float] = field(default_factory=dict)
    floors: Mapping[str, float] = field(default_factory=dict)
    rollbacks: Mapping[str, int] = field(default_factory=dict)
    rollback_cap: int = 3


@dataclass(frozen=True)
class Lease(AdmissionPolicy):
    """Exclusive occupancy of a named external resource — a `lease:` declaration (PP-12)."""

    state: AdmissionState = field(default_factory=AdmissionState)

    name = "lease"
    hold = Hold.LEASED
    rank = RANK_EXCLUSION

    def capacity(self, request: AdmissionRequest) -> int | None:
        eligible = request.scope == Scope.RESOURCE and bool(request.key)
        if eligible and self.state.holder.strip():
            snapshot = self.state
            admitted, _ = pool.acquire(
                snapshot.leases.get(request.key),
                task_id=request.key,
                holder=snapshot.holder,
                now=snapshot.now,
                ttl_seconds=snapshot.lease_ttl_secs,
            )
            return int(admitted is not None)
        return None


@dataclass(frozen=True)
class Dwell(AdmissionPolicy):
    """A bake floor before a step may start — `min_dwell_secs` (PP-12)."""

    state: AdmissionState = field(default_factory=AdmissionState)

    name = "min_dwell_secs"
    hold = Hold.BAKING
    rank = RANK_INVARIANT

    def capacity(self, request: AdmissionRequest) -> int | None:
        step = _StepAdmission.from_request(request, self.state)
        return None if step is None else step.dwell()


@dataclass(frozen=True)
class MetricGate(AdmissionPolicy):
    """A metric gate on a step — `metric_pass` / `metric_hold`, with a regression rolling back."""

    state: AdmissionState = field(default_factory=AdmissionState)

    name = "metric_gate"
    hold = Hold.REGRESSED
    rank = RANK_REGRESSION

    def decision(self, request: AdmissionRequest) -> Decision | None:
        step = _StepAdmission.from_request(request, self.state)
        return None if step is None else step.metric()

    def capacity(self, request: AdmissionRequest) -> int | None:
        result = self.decision(request)
        refused = result is not None and result.action in (
            Action.HOLD,
            Action.ROLLBACK,
            Action.COMPLETE,
        )
        return 0 if refused else None


@dataclass(frozen=True)
class Admission:
    """The composed verdict for one bucket."""

    capacity: int | None = None
    binding: AdmissionPolicy | None = None

    @property
    def bounded(self) -> bool:
        """Whether any policy had an opinion. Unbounded verdicts let the caller skip counting."""
        return self.capacity is not None

    @property
    def hold(self) -> Hold:
        """How a refusal by this verdict is reported. Unbounded verdicts never refuse."""
        return self.binding.hold if self.binding is not None else Hold.UNRECORDED

    def admits(self, in_flight: int) -> bool:
        """Whether one more may start given `in_flight` already occupying the bucket."""
        return self.capacity is None or in_flight < self.capacity


def compose(
    policies: tuple[AdmissionPolicy, ...], request: AdmissionRequest
) -> Admission:
    candidates = (
        _CapacityBound(cap, policy)
        for policy in policies
        if (cap := policy.capacity(request)) is not None
    )
    selected = min(candidates, default=None)
    return (
        Admission()
        if selected is None
        else Admission(selected.capacity, selected.policy)
    )


def default_policies(
    limits: Limits,
    *,
    single_active_feature: bool,
    state: AdmissionState | None = None,
) -> tuple[AdmissionPolicy, ...]:
    configured: list[AdmissionPolicy] = [
        Lane(limits=limits),
        ContainerConcurrency(),
        Wip(active=single_active_feature),
    ]
    if state is not None:
        configured.extend(policy(state=state) for policy in (Lease, Dwell, MetricGate))
    return tuple(configured)


OBSERVER = "work-board"


class Urgency(str, Enum):
    """Why an item is at the top. Shown, not just used for sorting."""

    OVERDUE = "overdue"
    BLOCKING_OTHERS = "blocking_others"
    HIGH_PRIORITY = "high_priority"
    NORMAL = "normal"


PRIORITY_WEIGHT = {
    "critical": 5.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
    "trivial": 0.5,
}


@dataclass(frozen=True)
class ReadyItem:
    """One unit of work as the ready projection sees it. A view, not a `Task`."""

    item_id: str
    title: str = ""
    priority: str = "medium"
    unblocked: bool = True
    blocks_count: int = 0
    overdue: bool = False
    updated_at: float = 0.0

    def urgency(self) -> Urgency:
        rules = (
            (lambda: self.overdue, Urgency.OVERDUE),
            (lambda: self.blocks_count > 0, Urgency.BLOCKING_OTHERS),
            (
                lambda: PRIORITY_WEIGHT.get(self.priority, 2.0)
                >= PRIORITY_WEIGHT["high"],
                Urgency.HIGH_PRIORITY,
            ),
        )
        return next((label for applies, label in rules if applies()), Urgency.NORMAL)

    def score(self) -> float:
        priority = PRIORITY_WEIGHT.get(self.priority, 2.0)
        blocking = min(3.0, self.blocks_count * 0.5)
        subtotal = priority + blocking
        return subtotal + (2.0 if self.overdue else 0.0)


def rank_key(item: ReadyItem) -> tuple[float, float, str]:
    """The pool's ordering, as a comparator on the unified core."""
    return (-item.score(), -item.updated_at, item.item_id)


def explain(item: ReadyItem) -> str:
    pieces = [f"priority={item.priority}"]
    suffixes = (
        (item.overdue, lambda: "overdue"),
        (item.blocks_count, lambda: f"blocks {item.blocks_count} other(s)"),
    )
    pieces.extend(render() for include, render in suffixes if include)
    return f"{item.item_id}: " + ", ".join(pieces)


def ready(
    items: Sequence[ReadyItem], policies: tuple[AdmissionPolicy, ...]
) -> list[ReadyItem]:
    def available(item: ReadyItem) -> bool:
        if not item.unblocked:
            return False
        request = AdmissionRequest(Scope.RESOURCE, item.item_id)
        return compose(policies, request).admits(0)

    return sorted(filter(available, items), key=rank_key)


def next_ready(
    items: Sequence[ReadyItem], policies: tuple[AdmissionPolicy, ...]
) -> ReadyItem | None:
    return next(iter(ready(items, policies)), None)


def _positive_cap(value: Any) -> int | None:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


def _node_config(request: AdmissionRequest, scope: Scope) -> dict[str, Any] | None:
    if request.scope == scope and request.node is not None:
        return request.node.config or {}
    return None


@dataclass(frozen=True)
class _CapacityBound:
    capacity: int
    policy: AdmissionPolicy

    def __lt__(self, other: _CapacityBound) -> bool:
        if self.capacity == other.capacity:
            return self.policy.rank > other.policy.rank
        return self.capacity < other.capacity


@dataclass(frozen=True)
class _StepAdmission:
    key: str
    config: StepConfig
    snapshot: AdmissionState

    @classmethod
    def from_request(
        cls, request: AdmissionRequest, state: AdmissionState
    ) -> _StepAdmission | None:
        config = _node_config(request, Scope.STEP)
        return (
            None
            if config is None
            else cls(request.key, step_config_from_phase(dict(config)), state)
        )

    def dwell(self) -> int | None:
        floor = self.config.min_dwell_secs
        if floor <= 0:
            return None
        started = self.snapshot.since.get(self.key)
        if started is not None and self.snapshot.now - started < floor:
            return 0
        return None

    def metric(self) -> Decision | None:
        if self.config.metric_pass is None:
            return None
        observed = self.snapshot.metrics.get(self.key)
        if observed is None:
            return None
        config, state = self.config, self.snapshot
        tick = TickState(
            step_index=0,
            step_started_at=state.now - config.min_dwell_secs,
            gate_passed=True,
            findings_in_step=config.min_findings,
            metric=observed,
            prior_step_floor=state.floors.get(self.key),
            rollbacks_on_step=int(state.rollbacks.get(self.key, 0)),
        )
        plan = TickConfig(steps=(config, StepConfig()), rollback_cap=state.rollback_cap)
        return evaluate_step(plan, tick, state.now)
