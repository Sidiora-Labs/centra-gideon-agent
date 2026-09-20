"""Tasks as a projection of run state: materialization, dedup, caps (TASKS-SOPS §1 — S55). A
materialized Task is a VIEW of a node, not a second copy of the truth. That single sentence
decides everything here: * **The engine owns the status of a managed task**, so a direct user
write is rejected at the write façade rather than merged. Two writers on one status field
produce a board that disagrees with the run it is showing, and the user believes the board. *
**Dedup is by content, not by transaction.** Per-file JSON storage gives no atomic check-and-
create, so a resume or rewind that re-materializes has to recognize its own earlier work. The
fingerprint is that recognition. * **A fan-out gets a cap, not a task per item.** Twenty
parallel leaves is a readable board; two hundred is a board nobody opens, and the collapse-to-
a-counter is what keeps the surface useful rather than complete. The projection table is the
other half. `TaskStatus` gains exactly ONE member (`SKIPPED`) and the WHY of a block lives in
`blocked_kind`, because a status per reason is a state fork every surface then has to re-
implement — and the surface that forgets is the one that shows a stale column. Pure functions
over node/instance state. The registry call is the caller's: `plan_materialization` decides
what should exist, so the rules are testable without a task store on disk.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.models import InstanceState
from gideon.engine.tasks import rules as task_rules
from gideon.engine.tasks.models import (
    TERMINAL_STATUSES,
    TaskStatus,
    WorkflowTaskBinding,
)

ENGINE_OWNED_FIELDS = task_rules.ENGINE_OWNED_FIELDS
managed = task_rules.managed
reject_write = task_rules.reject_write

FANOUT_TASK_CAP = 20

NON_MATERIALIZING_KINDS = frozenset(
    {"sequence", "parallel", "foreach", "loop", "branch", "transform", "wait"}
)

OPT_OUT_KEY = "materialize_task"


STATE_TO_STATUS: dict[InstanceState, TaskStatus] = {
    InstanceState.PENDING: TaskStatus.OPEN,
    InstanceState.READY: TaskStatus.OPEN,
    InstanceState.RUNNING: TaskStatus.IN_PROGRESS,
    InstanceState.WAITING: TaskStatus.BLOCKED,
    InstanceState.DONE: TaskStatus.DONE,
    InstanceState.DEGRADED: TaskStatus.DONE,
    InstanceState.FAILED: TaskStatus.BLOCKED,
    InstanceState.SKIPPED: TaskStatus.SKIPPED,
    InstanceState.CANCELLED: TaskStatus.CANCELLED,
    InstanceState.NO_CHANGE: TaskStatus.DONE,
    InstanceState.ESCALATED: TaskStatus.BLOCKED,
    InstanceState.SCOPE_VIOLATION: TaskStatus.BLOCKED,
    InstanceState.BLOCKED: TaskStatus.BLOCKED,
    InstanceState.DISCARDED: TaskStatus.SKIPPED,
}

FAILURE_TO_BLOCKED_KIND = {
    "permission": "capability",
    "budget": "capability",
    "transient": "transient",
    "network": "transient",
    "timeout": "transient",
}


def project_status(state: InstanceState) -> TaskStatus:
    """One node state as a task status. A state absent from the table returns OPEN rather than
    raising: a projection that crashed on a thirteenth engine state would take down the board
    for every task, and OPEN is the reading that keeps the work visible. The exhaustiveness
    test is what keeps that fallback from being load-bearing.
    """
    return STATE_TO_STATUS.get(state, TaskStatus.OPEN)


def project_blocked_kind(
    state: InstanceState, *, failure_class: str = "", waiting_on_human: bool = False
) -> str:
    """The WHY of a block, or "" when the task is not blocked. `DEGRADED` deliberately projects
    to DONE with no blocked kind: a degraded node SUCCEEDED with a machine-readable reason,
    and filing it as blocked would put completed work in the column the user scans for
    problems.
    """
    if project_status(state) is not TaskStatus.BLOCKED:
        return ""
    if waiting_on_human:
        return "needs_input"
    if state is InstanceState.WAITING:
        return "dependency"
    if state is InstanceState.ESCALATED:
        return "capability"
    if state in (InstanceState.SCOPE_VIOLATION, InstanceState.BLOCKED):
        return "needs_input"
    return FAILURE_TO_BLOCKED_KIND.get((failure_class or "").strip().lower(), "")


_FOREIGN_STATUS_ALIASES: dict[str, TaskStatus] = {"completed": TaskStatus.DONE}


def normalize_status(status: Any) -> TaskStatus | None:
    """One task status as the native enum, or `None` when nothing recognizes it. `None` rather
    than a default member because the two predicates below both mean "is this finished", and an
    unknown status defaulting to a terminal one would report unfinished work as complete — the
    one direction of error a gate must never make.
    """
    if isinstance(status, TaskStatus):
        return status
    raw = str(getattr(status, "value", status) or "").strip().lower()
    if not raw:
        return None
    try:
        return TaskStatus(raw)
    except ValueError:
        return _FOREIGN_STATUS_ALIASES.get(raw)


def is_done(status: Any) -> bool:
    """Whether a task is DONE specifically — the narrow reading, for callers that must not treat
    a cancellation as an accomplishment (a loop's per-task completion check is one).
    """
    return normalize_status(status) is TaskStatus.DONE


def is_resolved(status: Any) -> bool:
    """Whether a task is terminal, i.e. whether it still owes anyone work. Derived from
    `tasks.models.TERMINAL_STATUSES` rather than re-listing the members, so this stays ONE
    vocabulary with the task graph's own dependency-satisfaction rule instead of becoming a
    second dialect that drifts from it.

    A cancelled blocker resolves its dependents — the canonical task graph and the cockpit
    already agree on that, and the loop side's phase gate was written to the same rule (C432).

    NOT resolved here: `SKIPPED`. That is the canonical tuple's reading, carried faithfully
    rather than quietly widened — see this module's own `STATE_TO_STATUS`, which MINTS
    `SKIPPED` from two engine states while the board maps `skipped` onto DONE. Reconciling
    those three is an owner decision that moves `reconcile.py`'s dependency behavior, not
    something this projection may decide on its own.
    """
    return normalize_status(status) in TERMINAL_STATUSES


def fingerprint(*, source_ref: str = "", title: str = "", body: str = "") -> str:
    """The dedup key: `sha1(source_ref or title+body)[:16]`. A `source_ref` wins when present
    because it is stable across a re-worded title — a rewind that re-materialized a node whose
    label had been edited would otherwise create a second task for one piece of work, and the
    board would show it twice. Truncated to 16 hex chars: this is a dedup key inside one run's
    task list, not a security digest, and a full hash makes the stored record harder to
    eyeball for no gain.
    """
    basis = (
        source_ref or ""
    ).strip() or f"{(title or '').strip()}\n{(body or '').strip()}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[
        :16
    ]  # noqa: S324 — dedup, not crypto


def should_materialize(node: dict[str, Any]) -> tuple[bool, str]:
    """Whether a node earns a task, and why not when it does not. Three refusals, in order of
    authority: an explicit opt-out (the author decided), a container or zero-token kind (a
    board row nobody can act on), and a node with no id (a task that cannot be addressed by
    the engine that owns it).
    """
    cfg = node.get("config") or {}
    if cfg.get(OPT_OUT_KEY) is False:
        return False, f"node declares {OPT_OUT_KEY}: false"
    kind = str(node.get("kind", "") or "")
    if kind in NON_MATERIALIZING_KINDS:
        return False, f"{kind} nodes produce no actionable work of their own"
    if not str(node.get("id", "") or "").strip():
        return False, "node has no id, so the engine could not address its task"
    return True, ""


@dataclass
class TaskSpec:
    """One task the projection says should exist. `body` is behavior-first by contract: what to
    build, acceptance checkboxes, blocked-by. File paths and code snippets are prohibited
    because they go stale — the exception being decision-rich artifacts (schemas, state
    machines, type shapes), which are the thing a reader cannot reconstruct.
    """

    title: str
    binding: WorkflowTaskBinding
    body: str = ""
    done_criterion: str = ""
    status: TaskStatus = TaskStatus.OPEN
    blocked_kind: str = ""
    preview: str = ""

    def to_fields(self) -> dict[str, Any]:
        """The kwargs for `registry.create_task`. Goes through the FAÇADE, so a non-native task
        provider keeps working rather than being bypassed by a direct native write.
        """
        return {
            "title": self.title,
            "description": self.body,
            "status": self.status.value,
            "workflow_binding": self.binding,
            "done_criterion": self.done_criterion,
            "blocked_kind": self.blocked_kind,
            "preview": self.preview,
        }


BODY_SECTIONS = ("what to build", "acceptance", "blocked by")

_STALE_BODY_MARKERS = ("```", ".py:", ".ts:", "/src/", "line ")


def body_issues(body: str) -> list[str]:
    """Lint a task body against the §1 contract. Advisory, and it says why. Advisory rather than
    refusing: a body with a code snippet is still a body, and dropping the task to enforce a
    formatting rule would lose the work. But it is reported, because the staleness is real and
    the author is the only one who can fix it.
    """
    text = body or ""
    issues: list[str] = []
    lowered = text.lower()
    for marker in _STALE_BODY_MARKERS:
        if marker in lowered:
            issues.append(
                f"body contains {marker!r} — file paths and code snippets go stale, "
                "and a body that confidently names a moved file sends the reader to "
                "the wrong place"
            )
            break
    if text.strip() and "acceptance" not in lowered:
        issues.append(
            "body has no acceptance section — without one, 'done' is whatever the reader decides"
        )
    return issues


def build_body(
    what: str, acceptance: list[str], blocked_by: list[str] | None = None
) -> str:
    """Assemble a §1-shaped body. Acceptance criteria render as checkboxes because a checkbox is
    a thing a person can tick and a sentence is not — the `done_criterion` the engine runs is
    a separate machine check, and the two are deliberately not the same field.
    """
    parts = [f"**What to build**\n\n{what.strip()}"]
    if acceptance:
        checks = "\n".join(f"- [ ] {c.strip()}" for c in acceptance if c.strip())
        parts.append(f"**Acceptance**\n\n{checks}")
    if blocked_by:
        names = "\n".join(f"- {b}" for b in blocked_by if b)
        parts.append(f"**Blocked by**\n\n{names}")
    return "\n\n".join(parts)


@dataclass
class MaterializationPlan:
    """What to create, what already exists, and what was capped. `existing` is returned rather
    than silently skipped: a resume that reported "0 tasks created" with no further detail is
    indistinguishable from a resume that failed to materialize anything.
    """

    create: list[TaskSpec] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    capped: int = 0
    cap_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "create": [s.to_fields() for s in self.create],
            "existing": list(self.existing),
            "skipped": list(self.skipped),
            "capped": self.capped,
            "cap_note": self.cap_note,
        }


def plan_materialization(
    run_id: str,
    nodes: list[dict[str, Any]],
    *,
    existing_tasks: list[Any] | None = None,
    cap: int | None = None,
) -> MaterializationPlan:
    """Decide which nodes need a task, deduping against what already exists. Dedup runs on TWO
    keys, and both are needed. `(run_id, node_id)` catches the same node being re-
    materialized; the FINGERPRINT catches the same work arriving under a different node id —
    which is what a rewind-then-replan produces. Checking only the first would duplicate the
    work; checking only the second would collide two genuinely different nodes whose titles
    happen to match.
    """
    if cap is None:
        from gideon.automation.workflows.settings import fanout_task_cap

        cap = fanout_task_cap()
    plan = MaterializationPlan()
    seen_pairs: set[tuple[str, str]] = set()
    seen_prints: set[str] = set()
    for task in existing_tasks or []:
        binding = getattr(task, "workflow_binding", None)
        if binding is None:
            continue
        seen_pairs.add((binding.run_id, binding.node_id))
        if binding.fingerprint:
            seen_prints.add(binding.fingerprint)

    for node in nodes:
        ok, why = should_materialize(node)
        node_id = str(node.get("id", "") or "")
        if not ok:
            plan.skipped.append(f"{node_id or '<no id>'}: {why}")
            continue
        cfg = node.get("config") or {}
        title = str(node.get("label") or cfg.get("label") or node_id)
        print_key = fingerprint(
            source_ref=str(node.get("source_ref", "") or ""),
            title=title,
            body=str(cfg.get("prompt", "") or ""),
        )
        if (run_id, node_id) in seen_pairs:
            plan.existing.append(f"{node_id}: already materialized for this run")
            continue
        if print_key in seen_prints:
            plan.existing.append(
                f"{node_id}: fingerprint {print_key} already materialized"
            )
            continue

        if len(plan.create) >= max(1, cap):
            plan.capped += 1
            continue

        seen_pairs.add((run_id, node_id))
        seen_prints.add(print_key)
        plan.create.append(
            TaskSpec(
                title=title,
                binding=WorkflowTaskBinding(
                    run_id=run_id,
                    node_id=node_id,
                    node_path=str(node.get("path", "") or ""),
                    managed=True,
                    fingerprint=print_key,
                ),
                body=str(cfg.get("task_body", "") or ""),
                done_criterion=str(cfg.get("done_means", "") or ""),
            )
        )
    if plan.capped:
        plan.cap_note = (
            f"{len(plan.create)} of {len(plan.create) + plan.capped} items materialized "
            f"(cap {cap}); the rest are represented by the parent node's counter — a board that is "
            "complete but unreadable is worse than one that says how much it is not showing"
        )
    return plan


def task_list_ids_for_run(run_id: str, tasks: Iterable[Any] | None) -> dict[str, str]:
    """The run-side PLURAL tasks projection (PP-16 seam 4e, OWNER RULING 1): which TaskList
    holds each of this run's tasks, keyed by node id — ``{node_id: task_list_id}``.

    This is the destination for the loop row's ``task_list_ids`` column, and it is DERIVED,
    not stored. The ruling's operative sentence is "a run that projects to tasks carries the
    PLURAL shape, matching the live field": the loop keeps ``{phase_key: task_list_id}``, and
    a node id is the run-side phase key (the graph IS the plan — `loop_run_map`'s own
    ``plan``/``phase_status`` rows). Both halves of each entry already persist on the Task
    rows themselves — the binding carries ``(run_id, node_id)`` and ``Task.task_list_id`` is
    the structural parent — so a stored run-side column would be a second copy of the truth,
    which is exactly the shape seam 4c retired.

    Pure over the task iterable, like everything in this module: the registry call is the
    caller's. Semantics, each measured rather than guessed:

    * A task with no list contributes nothing. Same reading as a phase absent from
      ``Loop.task_list_ids`` — no list holds that work yet. MEASURED: the engine's own write
      (`controller._write_projected_task`) passes no ``task_list_id``, so a fresh run
      projects ``{}`` until its tasks are FILED into lists — a user move the write façade
      permits, because ``task_list_id`` is deliberately not in `ENGINE_OWNED_FIELDS`.
    * A managed binding wins over a produced one on the same node. A node can both project
      its own managed task and attribute produced output to itself; "where does this node's
      tracking live" is the managed task's list, the same question the loop's dict answers.
      Within a class, first wins — `plan_materialization`'s set-add idiom, not dict last-wins.
    * An empty ``run_id`` projects ``{}`` outright, so a malformed binding whose own run id
      is empty cannot be harvested by an equally empty query.

    No production caller yet, and this docstring says so (plan invariant 9): the consumers-
    to-be are `Loop.task_list_ids`' readers (`loop.tasks_link.phase_list_id` and friends,
    `dashboard.handlers.loop_routes._loop_task_ids`, the sdlc/goal briefs, the code cockpit),
    which move here when the loop row retires — that wiring belongs to the store-retirement /
    cockpit-contract seams, and wiring a forced consumer now would pre-empt them.
    """
    if not run_id:
        return {}
    out: dict[str, str] = {}
    managed_keys: set[str] = set()
    for task in tasks or ():
        binding = getattr(task, "workflow_binding", None)
        if binding is None or str(getattr(binding, "run_id", "") or "") != run_id:
            continue
        node_id = str(getattr(binding, "node_id", "") or "").strip()
        list_id = str(getattr(task, "task_list_id", "") or "").strip()
        if not node_id or not list_id:
            continue
        if getattr(binding, "managed", False):
            if node_id not in managed_keys:
                out[node_id] = list_id
                managed_keys.add(node_id)
        elif node_id not in out:
            out[node_id] = list_id
    return out


def progress_line(done: int, total: int, blocked: int = 0) -> str:
    """The parent-with-counter line a capped fan-out collapses into. Names the blocked count
    separately from the incomplete count, because "18 of 200" and "18 of 200, 3 blocked" call
    for different actions and the first hides the second.
    """
    if total <= 0:
        return ""
    line = f"{done} of {total} complete"
    if blocked:
        line += f", {blocked} blocked"
    return line
