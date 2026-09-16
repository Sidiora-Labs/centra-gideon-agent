"""Workflow data model — definitions, runs, the node algebra, and outcomes.

Deliberately dataclasses with explicit `to_dict`/`from_dict` rather than a validation
library: these shapes are persisted as JSON that must survive engine upgrades, so the
readers are **unknown-field-tolerant** by construction (WF2-R12). A bundled template or
a flywheel-proposed diff written by an older engine has to load on a newer one, and a
strict parser would reject it.

Three rules the rest of the engine depends on:

* **A node's identity is its path**, not a uuid. `root.children[2].body` is addressable
  and stable across mutations that do not touch it, which is what lets a rewind
  invalidate exactly the affected journal region.
* **Outcomes are richer than done|failed.** `degraded`, `no_change`, `scope_violation`,
  `escalated` and `blocked` are first-class, because retrofitting them into journal keys
  and widget semantics later is far more painful than declaring them now (WF2-R5).
* **Nothing here executes.** Models are pure data; the engine owns transitions. A model
  that could mutate run state would put two writers on the journal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.workflows.workflow_codec import (
    ModelPolicy,
    RecordCodecs,
    TreeShape,
)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

SPEC_SEMVER = "1.0"


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


class NodeKind(str, Enum):
    """The construct algebra. A spec is a TREE of containers — the tree renders
    directly as the progress widget, which is why containers are nodes rather than
    edges. DAG shapes inside `parallel` come from per-child `needs`."""

    SEQUENCE = "sequence"
    PARALLEL = "parallel"
    FOREACH = "foreach"
    LOOP = "loop"
    STAGE = "stage"
    INFER = "infer"
    BRANCH = "branch"
    TRANSFORM = "transform"
    ACTION = "action"
    VISUALIZE = "visualize"
    WAIT = "wait"
    GATE = "gate"
    SUBWORKFLOW = "subworkflow"


CONTAINER_KINDS = frozenset(
    {
        NodeKind.SEQUENCE,
        NodeKind.PARALLEL,
        NodeKind.FOREACH,
        NodeKind.LOOP,
        NodeKind.BRANCH,
    }
)

LLM_KINDS = frozenset({NodeKind.STAGE, NodeKind.INFER})

LANE_LLM = "llm"
LANE_IO = "io"
LANE_COMPUTE = "compute"


def lane_for(kind: NodeKind) -> str:
    return ModelPolicy.lane(kind)


class JoinMode(str, Enum):
    ALL = "all"
    ANY = "any"
    QUORUM = "quorum"


class LoopMode(str, Enum):
    COUNTED = "counted"
    UNTIL = "until"
    UNTIL_DRY = "until_dry"
    UNTIL_CANCELLED = "until_cancelled"


class ItemErrorPolicy(str, Enum):
    """What a `foreach` does about an item that FAILED. Three genuinely different answers, and
    the difference is observable at the RUN level — see `tick.foreach_outcome`, which is the
    single place the choice is made and which must branch on every member.

    The two axes are "how much of the fan-out still runs" and "does the failure count"; the
    members are the three useful combinations of them.
    """

    HALT = "halt"
    SKIP = "skip"
    COLLECT = "collect"


class GateKind(str, Enum):
    APPROVAL = "approval"
    VERIFY_COMMAND = "verify_command"
    VERIFY_SCRIPT = "verify_script"
    EVENT = "event"
    EXPRESSION = "expression"
    LADDER = "ladder"
    JUDGE = "judge"


class SessionMode(str, Enum):
    FRESH = "fresh"
    CONTINUOUS = "continuous"


@dataclass
class Node:
    """One spec node. Kind-specific fields live in `config` rather than in a subclass
    per kind: the spec is JSON that older engines must still read, and a tagged union
    keeps the tolerant-reader rule cheap (an unknown config key is ignored, not fatal).

    `id` is author-facing and only needs to be unique among siblings — bindings address
    nodes by id, and the engine addresses instances by path.
    """

    kind: NodeKind
    id: str = ""
    children: list[Node] = field(default_factory=list)
    body: Node | None = None
    cases: dict[str, Node] = field(default_factory=dict)
    default_case: Node | None = None
    config: dict[str, Any] = field(default_factory=dict)
    needs: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def lane(self) -> str:
        return lane_for(self.kind)

    @property
    def is_container(self) -> bool:
        return self.kind in CONTAINER_KINDS

    def child_nodes(self) -> list[Node]:
        """Every structural child, whatever the container shape."""
        return [child for _, child in TreeShape.edges(self)]

    # ── serialization ──

    _KNOWN = frozenset(
        {"kind", "id", "children", "body", "cases", "default", "config", "needs"}
    )

    def to_dict(self) -> dict[str, Any]:
        return TreeShape.write(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Node:
        """Tolerant read. An unrecognized `kind` raises (the engine cannot schedule what
        it cannot dispatch), but unknown *fields* are preserved in `extra`."""
        return TreeShape.read(cls, d)


def walk(node: Node, path: str = "root") -> list[tuple[str, Node]]:
    """Depth-first `(path, node)` pairs. The path IS the instance key the engine uses,
    so its shape is a contract: `root.children[0]`, `root.body`, `root.cases[hit]`."""
    return list(TreeShape.descendants(node, path))


class InstanceState(str, Enum):
    """A node instance's lifecycle. Wider than done|failed on purpose.

    `DEGRADED` is a SUCCESS with a machine-readable reason: an optional capability was
    absent and the node carried on. Templates that would otherwise die when a token is
    missing keep working, and the provenance stays visible downstream.
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"
    NO_CHANGE = "no_change"
    SCOPE_VIOLATION = "scope_violation"
    DISCARDED = "discarded"
    ESCALATED = "escalated"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {
        InstanceState.DONE,
        InstanceState.DEGRADED,
        InstanceState.FAILED,
        InstanceState.SKIPPED,
        InstanceState.NO_CHANGE,
        InstanceState.SCOPE_VIOLATION,
        InstanceState.DISCARDED,
        InstanceState.ESCALATED,
        InstanceState.BLOCKED,
        InstanceState.CANCELLED,
    }
)

SUCCESS_STATES = frozenset(
    {InstanceState.DONE, InstanceState.DEGRADED, InstanceState.NO_CHANGE}
)

FROZEN_STATES = TERMINAL_STATES | {InstanceState.RUNNING}


class FailureClass(str, Enum):
    """Why a node failed, which decides whether the scheduler may retry it.

    Only TRANSIENT and NETWORK are retryable: retrying a USER error (a malformed
    prompt) or a PERMISSION error burns budget to reach the same failure.
    """

    USER = "user"
    TRANSIENT = "transient"
    NETWORK = "network"
    PERMISSION = "permission"
    PROTOCOL = "protocol"
    BUDGET = "budget"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


RETRYABLE_CLASSES = frozenset({FailureClass.TRANSIENT, FailureClass.NETWORK})


@dataclass
class Failure:
    """A typed failure. `cause_plain` and `remediation` are DIFFERENT things — the
    widget renders the remediation as an actionable next step, and collapsing them
    leaves the user with an error and no idea what to do."""

    failure_class: FailureClass = FailureClass.INTERNAL
    cause_plain: str = ""
    remediation: str = ""
    recoverable: bool = False
    terminal_reason: str = ""
    suggestion: str = ""

    @property
    def retryable(self) -> bool:
        return self.failure_class in RETRYABLE_CLASSES

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("Failure", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Failure:
        return RecordCodecs.failure(cls, d)


@dataclass
class FailureSignature:
    """A 4-layer localization record for cheap cross-run diffing (WF2-R5)."""

    failing_node: str = ""
    stage: str = ""
    layer: str = ""
    reason: str = ""
    input_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("FailureSignature", self)


class LifecyclePhase(str, Enum):
    """What a work-unit status MEANS, separate from the word a noun spells it with.

    `PP-16`'s "one status vocabulary" clause is not a rename. `LoopStatus` and `RunStatus` each
    carry members the other cannot express — `loop_run_map.STATUS_VOCABULARY_DELTA` measures the
    ten orphans — and, the part no rename can reconcile, they disagreed about the terminality of
    the SAME word: `failed` stamped an end timestamp and refused any further transition on a run,
    while a failed loop is one of `ACTION_SOURCE_STATES["resume"]`'s five sources.

    That was never drift between two vocabularies. It is **two properties collapsed into one
    set**:

    * **phase** — has the unit stopped producing? A property of the STATE, so it is declared
      once, here, and shared by every noun.
    * **terminality** — may it still transition? A property of the NOUN's lifetime, because a
      run is one attempt while a loop is a campaign OF attempts. Retrying a run means creating
      another run; "retrying" a loop means the same loop moves again.

    So `ENDED` is declared once, each noun names which of its ended states remain resumable, and
    terminality is **derived** from the two. `failed` is then ended for both nouns and terminal
    for only one, with no contradiction and no membership set hand-maintained beside an enum.

    The four phases are exhaustive over both vocabularies by rail
    (`tests/test_lifecycle_phase_vocabulary.py`), which is what makes them a vocabulary rather
    than a convenience: a new status member has no phase until someone decides which one, and
    the rail refuses the merge until they do.
    """

    PRELAUNCH = "prelaunch"
    ACTIVE = "active"
    ATTENTION = "attention"
    ENDED = "ended"


class RunStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    NEEDS_INPUT = "needs_input"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


RUN_PHASES: dict[RunStatus, LifecyclePhase] = {
    RunStatus.DRAFT: LifecyclePhase.PRELAUNCH,
    RunStatus.RUNNING: LifecyclePhase.ACTIVE,
    RunStatus.PAUSED: LifecyclePhase.ATTENTION,
    RunStatus.NEEDS_INPUT: LifecyclePhase.ATTENTION,
    RunStatus.COMPLETE: LifecyclePhase.ENDED,
    RunStatus.FAILED: LifecyclePhase.ENDED,
    RunStatus.CANCELLED: LifecyclePhase.ENDED,
    RunStatus.ESCALATED: LifecyclePhase.ENDED,
}

ENDED_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    status for status, phase in RUN_PHASES.items() if phase is LifecyclePhase.ENDED
)

RESUMABLE_ENDED_RUN_STATUSES: frozenset[RunStatus] = frozenset()

TERMINAL_RUN_STATUSES: frozenset[RunStatus] = (
    ENDED_RUN_STATUSES - RESUMABLE_ENDED_RUN_STATUSES
)


class OriginKind(str, Enum):
    CHAT = "chat"
    SCHEDULE = "schedule"
    EVENT = "event"
    HOOK = "hook"
    IDLE = "idle"
    SUBAGENT_TOOL = "subagent-tool"
    MANUAL = "manual"
    API = "api"


class OverlapPolicy(str, Enum):
    """What a trigger-origin start does when the previous run is still going.

    🔴 The branch is `workflows.overlap.decide`, exhaustive with a raising tail, and it is
    the ONLY place that decides. `QUEUE` shipped as a member nothing branched on: the
    run-workflow provider compared against `SKIP` and `CANCEL_PREVIOUS` and let `queue` fall
    through to create+launch, so the one policy whose name promises ordering started a
    CONCURRENT run — the exact behaviour `SKIP`'s comment below says the default exists to
    prevent (WV-14). A new member must add its own branch there rather than inherit one.
    """

    SKIP = "skip"
    QUEUE = "queue"
    CANCEL_PREVIOUS = "cancel_previous"


@dataclass
class InputParam:
    type: str = "string"
    required: bool = False
    default: Any = None
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("InputParam", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InputParam:
        return RecordCodecs.input(cls, d)


@dataclass
class RunBudget:
    """Soft caps. A breach PAUSES the run resumably rather than killing it — the user
    can extend and continue, which is the difference between a budget and a bomb."""

    max_tokens: int = 0
    max_cost: float = 0.0
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("RunBudget", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunBudget:
        return RecordCodecs.budget(cls, d)


@dataclass
class RunDefaults:
    model_tier: str = "standard"
    effort: str = ""
    max_concurrency: int = 0
    node_timeout_total_secs: int = 0
    node_timeout_stall_secs: int = 0
    budget: RunBudget = field(default_factory=RunBudget)

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("RunDefaults", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunDefaults:
        return RecordCodecs.defaults(cls, d)


_SURFACE_MODES = frozenset({"off", "passive", "suggest"})

_ESCALATION_MODES = frozenset({"manual", "auto"})


def _surface_mode(value: Any) -> str:
    return ModelPolicy.choice(value, _SURFACE_MODES, "off")


def _escalation(value: Any) -> str:
    return ModelPolicy.choice(value, _ESCALATION_MODES, "manual")


def _non_negative_int(value: Any) -> int:
    """Coerce to a non-negative int; anything unparseable is 0 (= "no cadence").

    Negative is clamped rather than kept: a negative cadence would make every comparison read as
    overdue, so a fat-fingered `-7` would nag forever.
    """
    return ModelPolicy.non_negative(value)


@dataclass
class DefMetadata:
    """Declared, not inferred. `requirements` is what a run-start preflight checks so a
    missing binary or credential fails BEFORE tokens are spent (Slice 6)."""

    risk: str = "low"
    capabilities: list[str] = field(default_factory=list)
    requirements: dict[str, list[str]] = field(default_factory=dict)
    steering_examples: list[dict[str, str]] = field(default_factory=list)

    keywords: list[str] = field(default_factory=list)
    example_outputs: list[str] = field(default_factory=list)
    shapes: list[str] = field(default_factory=list)
    when_not_to_use: str = ""
    lighter_path: str = ""
    presets: list[str] = field(default_factory=list)
    match_text: str = ""

    surface_mode: str = "off"
    agent_digest: str = ""
    summary: str = ""
    when_to_use: str = ""
    cadence_days: int = 0
    escalation: str = "manual"
    packs: list[str] = field(default_factory=list)
    hands_off_to: list[dict[str, Any]] = field(default_factory=list)
    guided: bool = False
    a2a_published: bool = False

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("DefMetadata", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DefMetadata:
        return RecordCodecs.metadata(cls, d)


@dataclass
class WorkflowDef:
    """A reusable graph spec. Versioned on every save so a run can pin the spec it
    started from and a mutation can be diffed against its predecessor."""

    name: str
    root: Node
    version: int = 1
    spec_semver: str = SPEC_SEMVER
    description: str = ""
    source: str = "user"
    provenance: str = "user"
    inputs: dict[str, InputParam] = field(default_factory=dict)
    defaults: RunDefaults = field(default_factory=RunDefaults)
    metadata: DefMetadata = field(default_factory=DefMetadata)
    on_overlap: OverlapPolicy = OverlapPolicy.SKIP
    tags: list[str] = field(default_factory=list)
    runtime_hints: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = frozenset(
        {
            "name",
            "root",
            "version",
            "spec_semver",
            "description",
            "source",
            "provenance",
            "inputs",
            "defaults",
            "metadata",
            "on_overlap",
            "tags",
            "runtime_hints",
            "created_at",
            "updated_at",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("WorkflowDef", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowDef:
        return RecordCodecs.definition(cls, d)


@dataclass
class RunOrigin:
    kind: OriginKind = OriginKind.MANUAL
    session_key: str = ""
    tool_call_id: str = ""
    trigger_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("RunOrigin", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunOrigin:
        return RecordCodecs.origin(cls, d)


@dataclass
class WorkflowRun:
    """One execution. `root_run_id` is propagated through subworkflow spawns and forks
    and indexed with status, so the whole tree of a run is one query rather than a
    recursive walk (WF2-R13)."""

    id: str
    workflow_name: str
    status: RunStatus = RunStatus.DRAFT
    spec_version: int = 1
    inputs: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    origin: RunOrigin = field(default_factory=RunOrigin)
    parent_run_id: str | None = None
    root_run_id: str = ""
    spawned_by_node_id: str | None = None
    branch_key: str | None = None
    forked_from: dict[str, Any] | None = None
    project_id: str = ""
    mode: str = "background"
    budget: RunBudget = field(default_factory=RunBudget)
    pinned: bool = False
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float = 0.0
    total_tokens: int = 0
    agent_count: int = 0
    error_message: str = ""
    attention: dict[str, Any] | None = None
    policy_overrides: dict[str, Any] = field(default_factory=dict)
    owner_username: str = ""
    origin_harness: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ModelPolicy.initialize_run(self)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    _KNOWN = frozenset(
        {
            "id",
            "workflow_name",
            "status",
            "spec_version",
            "inputs",
            "intent",
            "origin",
            "parent_run_id",
            "root_run_id",
            "spawned_by_node_id",
            "branch_key",
            "forked_from",
            "project_id",
            "mode",
            "budget",
            "pinned",
            "created_at",
            "started_at",
            "completed_at",
            "elapsed_seconds",
            "total_tokens",
            "agent_count",
            "error_message",
            "attention",
            "policy_overrides",
            "owner_username",
            "origin_harness",
        }
    )

    def belongs_to(self, username: str) -> bool:
        """Whether this run is ``username``'s work (TSE2-1, mirroring `Task.belongs_to`).

        A run has no assignee, so `owner_username` alone decides. With no username configured
        every run belongs to the owner — a single-user install must behave exactly as it does
        today, and it is the honest answer: with no identity there is nobody else a run could
        belong to. An UNATTRIBUTED run (empty `owner_username` — written before this field, or
        from an unattributed origin) is likewise the owner's, so a foreign row must SAY whose it
        is. That is the same bargain the tasks and triggers seams already struck; it is what keeps
        a foreign-authored run out of the owner's "my runs" count without excluding pre-plan rows.
        """
        return ModelPolicy.belongs(self, username)

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("WorkflowRun", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowRun:
        return RecordCodecs.run(cls, d)


@dataclass
class NodeInstance:
    """Per-node run state. `epoch` is what makes rewind safe: journal keys are stamped
    with it, so a replayed region from a superseded epoch can never be mistaken for a
    cache hit on the current one."""

    path: str
    state: InstanceState = InstanceState.PENDING
    epoch: int = 0
    attempt: int = 0
    declined_edges: list[str] = field(default_factory=list)
    degraded_reason: str = ""
    failure: Failure | None = None
    started_at: str | None = None
    completed_at: str | None = None
    output_ref: str = ""
    tokens: int = 0
    wake_at: float = 0.0
    item_label: str = ""
    #: liveness stays owned by `DelegationSupervisor.get` -- and it is per-INSTANCE because a
    subagent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return RecordCodecs.write("NodeInstance", self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NodeInstance:
        return RecordCodecs.instance(cls, d)
