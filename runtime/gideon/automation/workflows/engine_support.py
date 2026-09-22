"""Shared workflow-dispatch support types and pure resolution helpers."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows import leases, ownership
from gideon.automation.workflows.bindings import (
    BindingContext,
    BindingError,
    resolve,
    resolve_prompt,
)
from gideon.automation.workflows.judge_contract import (
    JudgeVerdict,
    Verdict,
)
from gideon.automation.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    Node,
    NodeKind,
)

logger = logging.getLogger(__name__)

_PROGRESS_HEARTBEAT_SECS = 0.5

MAX_WF_DEPTH = 3
WF_DEPTH_KEY = "__wf_depth"

DEFAULT_MODEL_TIERS = {
    "reasoning": "reasoning",
    "standard": "orchestration",
    "fast": "background",
}


@dataclass
class NodeResult:
    """What a dispatcher produces. `state` is advisory — the controller applies it, so a
    dispatcher cannot flip a node terminal on its own (WF2-R10 terminal-write ownership,
    and the engine-owned-completion rule from WF2-R3)."""

    state: InstanceState = InstanceState.DONE
    output: Any = None
    failure: Failure | None = None
    degraded_reason: str = ""
    tokens: int = 0
    model: str = ""
    provider: str = ""
    cost_usd: float = 0.0
    declined_edges: list[str] = field(default_factory=list)
    wake_at: float = 0.0
    resolved_prompt: str = ""
    ask: dict[str, Any] | None = None
    published: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        from gideon.automation.workflows.models import SUCCESS_STATES

        return self.state in SUCCESS_STATES


def _fail(
    cls: FailureClass, cause: str, remediation: str = "", **kw: Any
) -> NodeResult:
    return NodeResult(
        state=InstanceState.FAILED,
        failure=Failure(
            failure_class=cls, cause_plain=cause, remediation=remediation, **kw
        ),
    )


async def _wait_with_progress(controller: Any, timeout: float, on_progress: Any) -> Any:
    """Wait for a child run, feeding the parent's stall clock while it works.

    The heartbeat is what makes `timeout_stall` mean "silent" rather than "slow": a nested run that
    legitimately takes ten minutes is progressing, and killing it as wedged would make nesting
    unusable for exactly the long-horizon work it exists for. The interval is well under any sane
    stall window, and each tick is one function call — the cost is nothing next to a child run.
    """
    if not callable(on_progress):
        return await controller.wait_for_terminal(timeout=timeout)

    task = asyncio.ensure_future(controller.wait_for_terminal(timeout=timeout))
    while not task.done():
        on_progress()
        await asyncio.wait({task}, timeout=_PROGRESS_HEARTBEAT_SECS)
    return task.result()


def _failed_with(
    cls: FailureClass, cause: str, remediation: str, output: Any
) -> NodeResult:
    """A FAILED result that still carries an output.

    `_fail` forwards its kwargs to `Failure`, which has no output field — but a failed subworkflow
    must still hand back the child run id, or the user is told a nested run failed with no way to
    find it.
    """
    return NodeResult(
        state=InstanceState.FAILED,
        output=output,
        failure=Failure(failure_class=cls, cause_plain=cause, remediation=remediation),
    )


def _condition_keys(node: Node) -> frozenset[str]:
    """Config keys holding a CONDITION — parsed by `conditions`, never interpolated.

    Interpolating one can only do harm. `resolve` reads a value that both starts and ends
    with braces as ONE whole reference (`bindings._WHOLE_RE`), so a two-term
    `{{a}} && {{b}}` resolves as a single path named `a}} && {{b` and fails — a gate would
    report a broken binding for an expression that is perfectly well formed. The dispatcher
    re-reads the raw value anyway, so nothing is lost by leaving these alone.

    `expr` is a condition ONLY on a gate: on a `transform` it is the value-producing
    expression, and skipping resolution there would hand the next node a template instead
    of data. `success_when` is a condition on every kind, and is evaluated after the node
    ran — its `output.*` root does not exist at config-resolution time at all.
    """
    if node.kind is NodeKind.GATE:
        return frozenset({"expr", "success_when"})
    return frozenset({"success_when"})


def resolve_config(
    node: Node, ctx: BindingContext
) -> tuple[dict[str, Any], Failure | None]:
    """Resolve every binding in a node's config, except its conditions.

    A `BindingError` becomes a USER failure rather than an exception: the spec is wrong,
    the run should say so precisely, and a traceback in a run log tells a non-developer
    nothing actionable.
    """
    raw = dict(node.config or {})
    held = {key: raw.pop(key) for key in _condition_keys(node) if key in raw}
    try:
        prompts = {
            key: raw.pop(key)
            for key in ("prompt", "system", "instruction", "instructions", "message")
            if key in raw
        }
        resolved = resolve(raw, ctx)
        resolved.update(
            {key: resolve_prompt(value, ctx) for key, value in prompts.items()}
        )
        resolved.update(held)
        return resolved, None
    except BindingError as exc:
        return {}, Failure(
            failure_class=FailureClass.USER,
            cause_plain=f"binding failed: {exc}",
            remediation=(
                "check the referenced node id and field exist, or add a `| default(...)` "
                "pipe if the value is genuinely optional"
            ),
        )


def resolve_use_case(node: Node, tiers: dict[str, str] | None = None) -> str:
    """Map a node's declared tier to a model use case."""
    table = dict(DEFAULT_MODEL_TIERS)
    table.update({str(k): str(v) for k, v in (tiers or {}).items()})
    tier = str((node.config or {}).get("model_tier", "standard") or "standard")
    return table.get(tier, "background")


def resolve_axis_model(use_case: str) -> str:
    """The concrete ``"Provider:model_id"`` ref the engine WOULD resolve for one axis.

    Reads the head of the active-selection CHAIN — the exact model
    `one_shot_completion` resolves for this use case — so a `cross_model` judge is
    validated against the model it will ACTUALLY run on, not a guess. Returns ``""``
    when nothing is bound (which the caller treats as an undeterminable family, so a
    cross-model gate fails closed rather than certifying against an unknown).

    Injected into `dispatch_gate` as `judge_model_resolver` so a test can pin a
    candidate family with no live provider.
    """
    try:
        from gideon.extensions.providers.use_cases import active_model_refs

        refs = active_model_refs(use_case)
    except Exception:  # noqa: BLE001 — an unresolvable axis is an undeterminable family
        return ""
    return str(refs[0]) if refs else ""


def _judge_pretier_screen(cfg: dict[str, Any]) -> NodeResult | None:
    """Run the free rule tier on a judge gate's declared `evidence`. Returns a NodeResult to
    SHORT-CIRCUIT the model call, or None to proceed to the judge (LOOPS-EVOLUTION criterion 2).

    🔴 Gated on the gate DECLARING `evidence`, and that guard is the whole reason this is additive.
    A judge that binds no evidence (every judge shipped before this) has nothing for the mechanical
    length check to measure — screening it unconditionally would reject it as "under 20 chars —
    nothing to judge" and turn a working gate into a permanent REJECT. So: no `evidence` key → no
    screen → identical behaviour to before. A gate opts INTO the saver by binding the deliverable it
    judges into `evidence` (e.g. `evidence: "{{nodes.deliverable.output.report}}"`).

    A rejection here is a USER failure carrying the pre-tier's own `failure_class` in the output, so
    the escalation ladder can route on WHY it was rejected (empty vs stubbed vs a worker give-up)
    rather than re-deriving it — the routing distinction the ladder was built for.
    """
    if "evidence" not in cfg:
        return None
    from gideon.automation.workflows.judge_pretier import run_pretier

    evidence = cfg.get("evidence")
    text = (
        evidence
        if isinstance(evidence, str)
        else ("" if evidence is None else str(evidence))
    )

    def _int(key: str, default: int = 0) -> int:
        try:
            return int(cfg.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    result = run_pretier(
        worker_output=text,
        artifacts=_int("evidence_artifacts"),
        commits=_int("evidence_commits"),
        changed_files=_int("evidence_changed_files"),
        min_chars=_int("min_chars", 20) or 20,
        check_existence_gate=any(
            k in cfg
            for k in (
                "evidence_artifacts",
                "evidence_commits",
                "evidence_changed_files",
            )
        ),
    )
    if not result.rejected:
        return None
    return NodeResult(
        state=InstanceState.FAILED,
        output={
            "verdict": Verdict.REJECT.value,
            "pretier": True,
            "failure_class": result.failure_class,
            "reason": result.reason,
            "checks_run": result.checks_run,
        },
        failure=Failure(
            failure_class=FailureClass.USER,
            cause_plain=f"pre-tier rejected before the judge: {result.reason}",
            remediation=(
                "the free rule tier proved the work unfinished (empty, stubbed, a tool error, or a "
                "worker give-up); fix the producing node — no model call was spent"
            ),
            recoverable=False,
        ),
    )


MAX_JUDGE_SAMPLES = 5


def _judge_sample_count(cfg: dict[str, Any]) -> int:
    """How many independent samples this judge gate takes. Always ≥ 1.

    Absent/invalid → 1, which is the pre-S145 behaviour: a gate that never asked for sampling must
    not start paying for it. Clamped at `MAX_JUDGE_SAMPLES` — see that constant for why a typo
    here is expensive rather than merely wrong.
    """
    raw = cfg.get("judge_samples", 1)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 1
    if count < 1:
        return 1
    return min(count, MAX_JUDGE_SAMPLES)


def _judge_gate_outcome(
    decision: JudgeVerdict, node: Node
) -> tuple[InstanceState, Failure | None]:
    """One contract-validated verdict → the node's state and failure (WF2LOO-13).

    The map is exhaustive over the merged `Verdict`, so a new member must add its branch
    rather than inheriting a pass — the same rule `_dual_guard` applies to `GuardOutcome`.

    An INVALID verdict is decided before the verdict itself, because that is the teeth: a PASS
    the contract refused (no cited proof, a rubric shortfall, an admitted forbidden mode) is a
    REJECT, not a pass with a note. `protocol_error` splits the two remediations — "the judge
    could not answer in the required shape" sends a reader to the prompt, "the work fell short"
    sends them to the deliverable, and reporting both as one loses the distinction that decides
    which they read.
    """
    if decision.escalated:
        return InstanceState.ESCALATED, Failure(
            failure_class=FailureClass.USER,
            cause_plain=decision.escalation_reason or "judge escalated",
            remediation="a human must decide: the judge named a contradiction it cannot resolve",
        )
    if not decision.valid:
        return InstanceState.FAILED, Failure(
            failure_class=(
                FailureClass.PROTOCOL if decision.protocol_error else FailureClass.USER
            ),
            cause_plain=f"judge PASS refused by the contract: {decision.invalid_reason}",
            remediation=(
                "the judge answered in the wrong shape — check the rubric it was given"
                if decision.protocol_error
                else "address the cited shortfall in the work and re-run the producing node"
            ),
        )
    if decision.verdict is Verdict.PASS:
        return InstanceState.DONE, None
    if decision.verdict is Verdict.ESCALATE:
        return InstanceState.ESCALATED, Failure(
            failure_class=FailureClass.USER,
            cause_plain="judge returned ESCALATE",
            remediation="a human must decide; the judge would not rule either way",
        )
    if decision.verdict is Verdict.NEEDS_INPUT:
        return InstanceState.WAITING, None
    if decision.verdict is Verdict.RETRY:
        return InstanceState.FAILED, Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain="judge returned RETRY",
            remediation="the engine will retry",
            recoverable=True,
        )
    if decision.verdict is Verdict.REPLAN:
        return InstanceState.FAILED, Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain="judge returned REPLAN: the remaining steps must be re-derived",
            remediation="re-plan the rest of the work from the judge's critique",
            recoverable=True,
        )
    if decision.verdict is Verdict.REJECT:
        return InstanceState.FAILED, Failure(
            failure_class=FailureClass.USER,
            cause_plain=f"judge returned REJECT: {decision.reasoning[:200]}",
            remediation="address the judge's rubric and re-run the producing node",
        )
    return InstanceState.FAILED, Failure(
        failure_class=FailureClass.INTERNAL,
        cause_plain=f"unhandled judge verdict {decision.verdict.value!r}",
        remediation=f"map the new Verdict member in `_judge_gate_outcome` ({node.id})",
    )


def claim_key(run_id: str, node_id: str) -> str:
    """The lease target for one branch.

    Per-NODE, not per-run: a run's leaves are meant to execute concurrently, so a run-scoped claim
    would serialize the fan-out the lease exists to protect.
    """
    return f"{run_id}:{node_id}" if run_id and node_id else ""


def claim_holder(run_id: str, node_id: str) -> str:
    """A fresh holder identity for ONE execution attempt of a branch.

    Unique per attempt, and that uniqueness is the whole control — measured, not assumed.
    `containers.claim` RENEWS a claim held by the same holder (so a worker that lost its in-memory
    state is not locked out of its own work). A holder derived from stable data — `run_id:node_id`,
    or even that plus the PID — therefore made every second attempt a renewal rather than a refusal,
    and the guard passed both executions through while a lease file sat there looking like
    protection. The PID version failed for the case that matters most: two concurrent co-tenant
    sessions in ONE gateway share a PID, which is exactly the threat §1.5 names.

    The cost of per-attempt identity is that a genuinely dead holder's branch waits out the TTL
    instead of being re-claimed instantly. That is the correct direction to be wrong in: a stalled
    branch is visible and self-healing, while a double execution is silent and can write twice.
    """
    return f"{ownership.owned_key(run_id, node_id or 'node')}#{uuid.uuid4().hex[:12]}"


def _release_claim(claim_target: str, holder: str) -> None:
    """Drop this worker's claim on a branch that did NOT start.

    Never raises: a failed release costs one TTL of a stalled branch, while an exception here would
    turn a recoverable no-spawn return into a crashed dispatch.
    """
    if not claim_target:
        return
    try:
        leases.release_claim(claim_target, holder)
    except Exception:
        logger.debug("claim release failed for %s", claim_target, exc_info=True)
