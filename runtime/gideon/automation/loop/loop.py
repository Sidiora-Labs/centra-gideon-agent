"""Unified Loop data model — the one autonomous primitive.

Gideon has a single autonomous engine: the **Loop**. Every loop shares the
same spine (understand → break into phases → plan with persona/skills/workflows →
execute each phase until its goal is met) and the same machinery (nudge + a
deterministic watchdog + a file-based worker↔supervisor split). What differs per
loop is **subject-matter expertise**, supplied by a per-:data:`kind` strategy
(see :mod:`gideon.automation.loop.kinds`): how it classifies the task, phases the
problem, gates done-ness, loads capabilities, and frames the worker brief.

Kinds: ``general`` (Claude-Code-``/loop``-style generic iteration), ``goal``
(verifiable/open-ended/monitor research+action), ``code`` (SDLC stage-gated work
in a workspace), ``design`` (design-system creation).

The entity holds the genuinely-shared lifecycle/timing/identity fields at the top
level (what the store/manager/watchdog touch generically) plus a ``kind_config``
dict the kind strategy owns (goal_type+granularity; entry_stage+stage_plan;
design tokens; …). Kind-specific *behavior* lives in the strategy, never here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.loop import files as loop_files
from gideon.automation.workflows.models import LifecyclePhase


class LoopKind(str, Enum):
    """The subject-matter axis of a loop. Picks the strategy that supplies the
    type-specific classify/phase/gate/capability/brief behavior."""

    GENERAL = "general"
    GOAL = "goal"
    CODE = "code"
    DESIGN = "design"
    RESEARCH = "research"


KINDS: frozenset[str] = frozenset(k.value for k in LoopKind)


class LoopStatus(str, Enum):
    """Lifecycle states — the UNION of the former GoalLoop + CodeProject enums, so
    one machine serves every kind. ``stagnant`` (goal's no-new-findings) and
    ``blocked`` (code's repeatedly-failing stage gate) are both supervisor-set
    attention states; a kind uses whichever its watchdog logic raises.

    Transitions are enforced in :mod:`gideon.automation.loop.store`. Which members are terminal is
    not spelled out here: it is DERIVED from :data:`LOOP_PHASES` below, so the enum names the
    states and the phase map names what they mean (`PP-16`, "one status vocabulary").
    """

    INTAKE = "intake"
    PLANNING = "planning"
    REVIEW = "review"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STAGNANT = "stagnant"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"
    COMPLETE = "complete"
    FAILED = "failed"
    STOPPED = "stopped"


LOOP_PHASES: dict[LoopStatus, LifecyclePhase] = {
    LoopStatus.INTAKE: LifecyclePhase.PRELAUNCH,
    LoopStatus.PLANNING: LifecyclePhase.PRELAUNCH,
    LoopStatus.REVIEW: LifecyclePhase.PRELAUNCH,
    LoopStatus.READY: LifecyclePhase.PRELAUNCH,
    LoopStatus.RUNNING: LifecyclePhase.ACTIVE,
    LoopStatus.PAUSED: LifecyclePhase.ATTENTION,
    LoopStatus.STAGNANT: LifecyclePhase.ATTENTION,
    LoopStatus.BLOCKED: LifecyclePhase.ATTENTION,
    LoopStatus.NEEDS_INPUT: LifecyclePhase.ATTENTION,
    LoopStatus.COMPLETE: LifecyclePhase.ENDED,
    LoopStatus.FAILED: LifecyclePhase.ENDED,
    LoopStatus.STOPPED: LifecyclePhase.ENDED,
}

ENDED_STATUSES: frozenset[LoopStatus] = frozenset(
    status for status, phase in LOOP_PHASES.items() if phase is LifecyclePhase.ENDED
)

ATTENTION_STATUSES: frozenset[LoopStatus] = frozenset(
    status for status, phase in LOOP_PHASES.items() if phase is LifecyclePhase.ATTENTION
)

RESUMABLE_ENDED_STATUSES: frozenset[LoopStatus] = frozenset({LoopStatus.FAILED})

TERMINAL_STATUSES: frozenset[LoopStatus] = ENDED_STATUSES - RESUMABLE_ENDED_STATUSES


class LoopStopReason(str, Enum):
    """WHY a loop ended — the closed vocabulary persisted on the run record (`AG-14`).

    :class:`LoopStatus` says WHERE a loop landed (``complete``/``failed``/``stopped``);
    this says WHY, so downstream consumers (cockpit, flywheel, reports) can act on the
    cause without parsing the free-text ``error_message``/journal reasons — those stay
    the human-facing explanation, this is the machine-facing classification. Stamped by
    ``store.update_status`` alongside ``completed_at`` when a loop arrives at an ENDED
    status, and cleared when a resumed FAILED loop leaves one, so the two never disagree.
    """

    DONE = "done"
    USER = "user"
    CYCLE_BUDGET = "cycle_budget"
    COST_BUDGET = "cost_budget"
    DEADLINE = "deadline"
    WORKER_FAILED = "worker_failed"


ACTIVE_STATUSES: frozenset[LoopStatus] = frozenset(
    status
    for status, phase in LOOP_PHASES.items()
    if phase in (LifecyclePhase.ACTIVE, LifecyclePhase.ATTENTION)
)

PRELAUNCH_STATUSES: frozenset[LoopStatus] = frozenset(
    status for status, phase in LOOP_PHASES.items() if phase is LifecyclePhase.PRELAUNCH
)

STOPPABLE_STATUSES: frozenset[LoopStatus] = ACTIVE_STATUSES | frozenset(
    {LoopStatus.INTAKE, LoopStatus.PLANNING}
)

ACTION_SOURCE_STATES: dict[str, frozenset[LoopStatus]] = {
    "start": frozenset({LoopStatus.READY, LoopStatus.REVIEW}),
    "pause": frozenset({LoopStatus.RUNNING}),
    "resume": frozenset(
        {
            LoopStatus.PAUSED,
            LoopStatus.STAGNANT,
            LoopStatus.BLOCKED,
            LoopStatus.NEEDS_INPUT,
            LoopStatus.FAILED,
        }
    ),
    "stop": STOPPABLE_STATUSES,
}


@dataclass
class Loop:
    """One autonomous loop of any kind.

    Top-level fields are the SHARED spine (identity, the worker binding, lifecycle,
    timing, project scope, capabilities). ``kind_config`` carries everything
    type-specific that only the kind strategy interprets — so the store/manager/
    watchdog stay kind-agnostic and a new kind adds a strategy, not entity columns.
    """

    id: str
    name: str
    kind: str
    task: str

    project_id: str = ""

    summary: str = ""
    intake_rigor: str = "auto"

    plan: list[dict] = field(default_factory=list)
    phase_status: dict = field(default_factory=dict)

    execution: str = "solo"
    agent: str = ""
    model: str = ""
    provider: str = ""
    provider_agent: str = ""
    reasoning_effort: str = ""
    roster: list[dict] = field(default_factory=list)
    strategy_id: str = "orchestrator"
    strategy_config: dict = field(default_factory=dict)
    skill_ids: list[str] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)

    workspace_dir: str = ""
    auto_teardown_on_complete: bool = False
    attended: bool = False
    autopilot: bool = True
    max_cycles: int = 30
    max_cost_usd: float = 0.0
    deadline_secs: float = 0.0
    idle_secs: int = 120
    stop_reason: str = ""
    success_criteria: str | None = None

    kind_config: dict = field(default_factory=dict)

    status: str = LoopStatus.READY.value
    created_at: float = 0.0
    started_at: float | None = (
        None  # start of the CURRENT running stretch (reset each resume)
    )
    completed_at: float | None = None
    elapsed_seconds: float = (
        0.0  # banked running time from PRIOR stretches (excludes pauses)
    )
    error_message: str | None = None

    tasks_project_id: str = ""
    task_list_ids: dict = field(default_factory=dict)
    linked_task_ids: list[str] = field(default_factory=list)
    session_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Loop":
        """Build a Loop from a dict, ignoring unknown keys (forward-compatible reads)."""
        allowed = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in data.items() if k in allowed})


def finding_content(finding: dict, *, limit: int = 6000) -> str:
    """The cycle's reported content for scoring — the SINGLE canonical extraction the
    done-ness judge + ratchet both use, so they always evaluate the same text.
    Precedence: evidence → summary → content → note (richest first). A divergent
    precedence here caused false-regression pauses (ratchet scored a terse summary while
    the judge scored rich evidence from the same finding)."""
    text = (
        finding.get("evidence")
        or finding.get("summary")
        or finding.get("content")
        or finding.get("note")
        or ""
    )
    return str(text)[:limit]


def effective_dir(loop: "Loop") -> str:
    """The directory a loop's work actually lives in — the SINGLE resolver every
    ground-truth check uses so the supervisor reads the same place the worker writes.

    Mirrors the worker session's own cwd resolution, in the same precedence:
      1. ``workspace_dir`` — an explicitly bound codebase (brownfield code, or any loop
         pointed at a real directory);
      2. a GREENFIELD code loop's own ``loop_dir`` — a code loop with NO bound workspace
         operates FROM its files dir: the brief tells the worker "(none — operate from the
         project files dir)" and the cycle_nudge qualifies every path with the loop dir, so
         a greenfield deliverable (e.g. slugify.py) lands there, NOT the shared workspace
         root. Only the CODE kind is directed this way — goal/general keep only engine files
         (brief/findings/FINDINGS.md) in the loop dir and write their deliverable to the
         project/workspace, so this tier is code-only to avoid mis-pointing them. Missing
         this tier hard-failed the code deliverable gate forever — the supervisor looked in
         workspace_root() while the worker wrote to the loop dir, so `_resolve_deliverable`
         returned (True, None) → "stage held" on a genuinely-complete stage (observed live:
         greenfield loop 07d5a0d0, 15/15 tests green, stage held across cycles);
      3. the containing project's shared context dir (``workspace_dir == ""`` means "use
         the project context dir" per the Loop model);
      4. ``workspace_root()`` — the default session workspace, where a loop with NO bound
         workspace AND no project actually runs (the common open-ended-goal case; observed
         live: goal 0fef190e wrote its deliverable to the workspace root).

    Always resolves to a real dir in practice (tier 4 never empty), so a ground-truth read
    has somewhere to look rather than silently no-opping on an empty path."""
    ws = (loop.workspace_dir or "").strip()
    if ws:
        return ws
    if loop.kind == "code":
        try:
            d = loop_files.loop_dir(loop.id)
            if d is not None and d.is_dir():
                return str(d)
        except Exception:
            pass
    if loop.project_id:
        try:
            from gideon.cognition import projects as projects_svc

            ctx = (projects_svc.context_dir(loop.project_id) or "").strip()
            if ctx:
                return ctx
        except Exception:
            pass
    try:
        from gideon.core.config.loader import workspace_root

        return str(workspace_root())
    except Exception:
        return ""
