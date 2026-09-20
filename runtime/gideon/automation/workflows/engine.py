"""Node dispatchers — the only place in the engine that performs real work.

One function per node kind, each with the same contract: take a resolved node and a
binding context, do the work, return a `NodeResult`. Dispatchers do NOT write state and
do NOT decide what runs next — the controller owns both. Keeping that split means a
dispatcher can be tested by calling it, with no run, no lock, and no journal.

Two asymmetries here are deliberate and easy to get backwards:

**A null output is a value; an unresolvable reference is an error (WF2-R9).** A node that
legitimately produced nothing hands `None` downstream, and `filter`/`default` pipes exist
to absorb it. But a binding naming something that does not exist raises `BindingError` —
because the silent alternative is a prompt with an empty hole in it, which produces
confident nonsense that looks like a real answer. That failure must be loud.

**`stage` and `infer` are different kinds for a real reason.** A `stage` is a subagent
with tools and a session — expensive, capable, and able to spawn. An `infer` is exactly
one bounded model call with no tools and no session. Templates that just need a
classification should not pay for a subagent, and the lane accounting depends on
distinguishing them.

Model resolution goes through the `model_tiers` slot map: a node declares an intent
(`reasoning`/`standard`/`fast`), config maps intents to use cases, and the existing
use-case bridge resolves the actual model. A template never names a model — that is what
keeps a template portable across a user's provider setup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.workflows import leases, longrun, ownership
from gideon.automation.workflows.bindings import (
    BindingContext,
    BindingError,
    resolve,
    resolve_prompt,
)
from gideon.automation.workflows.compaction import complete_with_compaction
from gideon.automation.workflows.judge_contract import (
    JudgeHints,
    JudgeVerdict,
    Verdict,
    aggregate_samples,
    judge_instruction,
    parse_judge_json,
    validate_verdict,
)
from gideon.automation.workflows.models import (
    Failure,
    FailureClass,
    GateKind,
    InstanceState,
    Node,
    NodeKind,
)
from gideon.automation.workflows.verify import (
    check_required_artifacts,
    requires_fresh_judge,
    run_ladder,
)
from gideon.security.safety_flags import strict_bool

logger = logging.getLogger(__name__)

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


async def dispatch_transform(node: Node, ctx: BindingContext) -> NodeResult:
    """Pure data reshaping — zero tokens. The `expr` binding IS the transform: there is
    no expression language beyond the closed pipe set, which is what keeps a spec from
    becoming an eval surface.

    `skeleton: "<artifact-slug>"` is the same transform over a body stored OUTSIDE the spec
    (AMBIENT-SURFACES §2.1). A live dashboard's template is an artifact a model authored once;
    interpolating it here — rather than pasting the whole HTML into `expr` — is what makes the
    steady-state refresh a pure substitution the spec stays readable through. Zero tokens
    either way: the render transform is the reason a refresh costs nothing.
    """
    cfg = node.config or {}
    skeleton_slug = str(cfg.get("skeleton", "") or "").strip()
    raw = cfg.get("expr")
    if skeleton_slug:
        from gideon.workspace.artifacts.registry import get_provider

        provider = get_provider()
        art = provider.get(skeleton_slug) if provider is not None else None
        if art is None:
            return _fail(
                FailureClass.USER,
                f"transform skeleton artifact {skeleton_slug!r} does not exist",
                "create the skeleton artifact, or correct the slug on this node",
            )
        raw = art.content or ""
    try:
        value = resolve(raw, ctx)
    except BindingError as exc:
        return _fail(
            FailureClass.USER,
            f"transform binding failed: {exc}",
            "check the referenced node id and field exist",
        )
    contract = (node.config or {}).get("output_contract")
    if isinstance(contract, dict):
        problem = check_output_contract(value, contract)
        if problem:
            return _fail(
                FailureClass.USER,
                f"output contract violated: {problem}",
                "adjust the transform expression or relax the contract",
            )
    return NodeResult(state=InstanceState.DONE, output=value)


async def dispatch_infer(
    node: Node,
    ctx: BindingContext,
    *,
    tiers: dict[str, str] | None = None,
    completion: Any = None,
    compaction_saves: list[float] | None = None,
) -> NodeResult:
    """ONE bounded model call — no tools, no session, no spawn.

    `completion` is injected so tests can drive this without a provider; production
    passes `llm_helpers.one_shot_completion`.

    The call goes through the compaction ladder (WV-12): a long-horizon prompt is
    compacted proactively at ~80% of the bound model's window, and a length rejection
    triggers one aggressive re-compaction + retry before the node fails. `compaction_saves`
    is this node's compaction history, which the anti-thrashing rule reads.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    prompt = str(cfg.get("prompt", "") or "")
    if not prompt.strip():
        return _fail(
            FailureClass.USER,
            "infer node has an empty prompt after binding",
            "check the prompt template and its bindings",
        )

    use_case = resolve_use_case(node, tiers)
    fn = completion
    if fn is None:
        from gideon.integrations.llm_helpers import one_shot_completion

        fn = one_shot_completion

    want_json = bool(cfg.get("schema")) or str(cfg.get("output", "")) == "json"
    sent_prompt = prompt

    def remember_sent_prompt(value: str) -> None:
        nonlocal sent_prompt
        sent_prompt = value

    try:
        text = await complete_with_compaction(
            fn,
            prompt,
            use_case=use_case,
            output_type=dict if want_json else None,
            saves=compaction_saves,
            on_prompt=remember_sent_prompt,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return NodeResult(
            state=InstanceState.FAILED,
            failure=_classify_exception(exc),
            resolved_prompt=sent_prompt,
        )

    output: Any = text
    if want_json:
        parsed = _parse_json_loose(text)
        if parsed is None:
            return NodeResult(
                state=InstanceState.FAILED,
                failure=Failure(
                    failure_class=FailureClass.PROTOCOL,
                    cause_plain="model output was not valid JSON",
                    remediation="add an explicit schema to the prompt, or split into a "
                    "produce-then-extract pair",
                ),
                resolved_prompt=sent_prompt,
            )
        output = parsed
    return NodeResult(
        state=InstanceState.DONE,
        output=output,
        resolved_prompt=sent_prompt,
        tokens=_estimate_tokens(prompt, text),
    )


async def dispatch_visualize(
    node: Node,
    ctx: BindingContext,
    *,
    completion: Any = None,
) -> NodeResult:
    """ONE bounded model call → a genui widget spec, agency-free (AMBIENT-SURFACES §5.3).

    The workflow-node face of the shared `visualize(data, hint)` primitive: it renders a
    node's `data` binding into a generative-UI widget with no tools, no session, no
    spawn — the reasoning-axis two-step (a prior node produces the data; this renders it).
    `completion` is injected so a test can drive it without a provider; production lets
    the primitive resolve `one_shot_completion` on the reasoning axis itself.

    Output is the genui DSL + the ready-to-embed `<widget>` block, so a downstream node
    (a tile render, a digest) can bind `{{nodes.<id>.output.widget}}` directly.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    if "data" not in cfg:
        return _fail(
            FailureClass.USER,
            "visualize node has no `data` binding",
            "bind `data` to the value to render, e.g. data: {{nodes.compute.output}}",
        )
    hint = str(cfg.get("hint", "") or "")
    title = str(cfg.get("title", "") or "Visualization")
    from gideon.workspace.visualize import visualize as _visualize_primitive

    try:
        result = await _visualize_primitive(
            cfg["data"], hint, title=title, completion=completion
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return NodeResult(state=InstanceState.FAILED, failure=_classify_exception(exc))
    if not result.dsl.strip():
        return _fail(
            FailureClass.PROTOCOL,
            "visualize produced no renderable components",
            "the model returned nothing the registry could render; simplify the data or hint",
        )
    return NodeResult(
        state=InstanceState.DONE,
        output={"dsl": result.dsl, "widget": result.widget},
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


def leaf_spawn_env(
    node: Node, cfg: dict[str, Any], *, run_id: str, depth: int
) -> dict[str, str]:
    """The env one stage leaf runs with: lineage + capability posture, secret-filtered.

    Built here because this is where a leaf is actually spawned — the compiled `postures` block is
    an external contract until something applies it, and `batch_compile.CompileResult.unenforced`
    names this seam as the missing half. `leaf_env` does the credential filtering (reusing
    `workspace.looks_secret`, not a second copy of that policy).

    The child's depth is the parent's PLUS ONE: a leaf that inherited the parent's depth unchanged
    would let each level re-spend the same budget, and `depth_lint`'s static refusal of a nested
    batch would never trip.
    """
    from gideon.automation.workflows.batch_compile import lineage_env
    from gideon.integrations.mcp_shared import LEAF_READ_ONLY_KEY, leaf_env

    lineage = lineage_env(
        run_id=run_id,
        project_id=str(cfg.get("project_id", "") or ""),
        node_id=node.id or "",
        depth=int(depth) + 1,
    )
    if str(cfg.get("capability", "") or "research").strip().lower() != "mutating":
        lineage[LEAF_READ_ONLY_KEY] = "1"
    return leaf_env(dict(os.environ), lineage)


async def dispatch_stage(
    node: Node,
    ctx: BindingContext,
    *,
    subagents: Any = None,
    depth: int = 0,
    run_id: str = "",
    cwd: str = "",
) -> NodeResult:
    """One subagent execution, with tools and a session.

    Spawns are `silent=True` and run-scoped: completions belong in the run journal, not
    injected into whatever chat session happened to start the run. Without `silent`, a
    background workflow would interrupt an unrelated conversation with stage results.

    Depth is enforced in CODE here (`MAX_WF_DEPTH`), which is new: the existing contract
    is a sentence in a system prompt, and a prompt is not an enforcement mechanism.
    """
    if depth >= MAX_WF_DEPTH:
        return _fail(
            FailureClass.PERMISSION,
            f"workflow nesting depth cap ({MAX_WF_DEPTH}) reached — not spawning",
            "flatten the workflow, or move the nested work into a separate run",
        )
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    prompt = str(cfg.get("prompt", "") or "")
    if not prompt.strip():
        return _fail(
            FailureClass.USER,
            "stage node has an empty prompt after binding",
            "check the prompt template and its bindings",
        )

    skip, skip_why = _restriction_skip(cfg, run_id)
    if skip:
        return NodeResult(
            state=InstanceState.DEGRADED, output=None, degraded_reason=skip_why
        )

    if subagents is None:
        return _fail(
            FailureClass.INTERNAL,
            "subagent manager unavailable",
            "the gateway did not initialize the subagent service",
        )

    claim_target = claim_key(run_id, node.id or "")
    holder = claim_holder(run_id, node.id or "")
    if claim_target:
        granted, reason = leases.acquire_claim(claim_target, holder)
        if granted is None:
            return NodeResult(
                state=InstanceState.DEGRADED,
                output=None,
                degraded_reason=(
                    f"another worker holds the claim on this node ({reason}) — not executing twice"
                ),
                resolved_prompt=prompt,
            )

    info = subagents.spawn(
        task=prompt,
        parent_session_key=ownership.owned_key(run_id, node.id or "node"),
        parent_run=(f"{ownership.OWNED_PREFIX}{run_id}" if run_id else ""),
        agent=str(cfg.get("agent", "") or ""),
        model=str(cfg.get("model", "") or "") or None,
        max_turns=int(cfg.get("max_turns", 0) or 0),
        cwd=cwd,
        silent=True,
        approval_mode=str(cfg.get("approval_mode", "") or "") or None,
        capability_class=(
            "mutating"
            if str(cfg.get("capability", "") or "research").strip().lower()
            == "mutating"
            else "research"
        ),
        extra_env=leaf_spawn_env(node, cfg, run_id=run_id, depth=depth),
    )
    if info is None:
        _release_claim(claim_target, holder)
        return NodeResult(
            state=InstanceState.READY,
            degraded_reason="subagent capacity reached; will retry",
            resolved_prompt=prompt,
        )
    if getattr(info, "error", ""):
        _release_claim(claim_target, holder)
        return NodeResult(
            state=InstanceState.FAILED,
            failure=Failure(
                failure_class=FailureClass.PERMISSION,
                cause_plain=f"spawn rejected: {info.error}",
                remediation="check subagent approval settings and cwd allow-roots",
            ),
            resolved_prompt=prompt,
        )
    return NodeResult(
        state=InstanceState.RUNNING,
        output={"subagent_id": info.id},
        resolved_prompt=prompt,
    )


async def dispatch_branch(node: Node, ctx: BindingContext) -> NodeResult:
    """Evaluate the selector, then record every edge the branch did NOT take.

    Declines are recorded, not inferred (WF2-R18). The frontier turns a declined edge's
    target into SKIPPED — terminal — which is what lets a downstream join proceed. Trying
    to infer "not taken" from "routed elsewhere" would also starve any sibling whose
    `needs` merely names this branch, since routing among cases says nothing about it.

    A resolved value with no matching case and no default is a routing failure, reported
    as one: falling through silently would make a spec that never ran its real work look
    like a clean pass.
    """
    from gideon.automation.workflows.tick import _select_case, edge_key

    expr = str((node.config or {}).get("on", "") or "")
    if not expr:
        return _fail(
            FailureClass.USER,
            "branch node has no `on` binding",
            "add `config.on` naming the value to route on",
        )
    selected = _select_case(node, ctx)
    if selected is None:
        return _fail(
            FailureClass.USER,
            "branch selector matched no case and the node has no default",
            "add a `default` case, or declare `enum` so validation catches this earlier",
        )
    label, case_node = selected
    declined: list[str] = []
    if node.id:
        taken = id(case_node)
        for other in list(node.cases.values()) + (
            [node.default_case] if node.default_case is not None else []
        ):
            if other is not None and id(other) != taken and other.id:
                declined.append(edge_key(node.id, other.id))
    return NodeResult(
        state=InstanceState.DONE,
        output={"case": label},
        declined_edges=sorted(set(declined)),
    )


MAX_SUBWORKFLOW_DEPTH = 3

_PROGRESS_HEARTBEAT_SECS = 0.5


async def dispatch_subworkflow(
    node: Node,
    ctx: BindingContext,
    *,
    depth: int = 0,
    run_id: str = "",
    supervisor: Any = None,
    timeout: int = 60,
    on_progress: Any = None,
) -> NodeResult:
    """Run a named workflow as a CHILD run, and wait for it (WF2-R13).

    **A real child run, not an inlined subtree.** The child gets its own run id, its own journal,
    its own state map and its own terminal-status writer. That costs a row and a directory, and it
    buys the things that matter: the child can be rewound, resumed, forked and inspected on its
    own; a crash mid-child leaves a child run to adopt rather than a half-written parent; and the
    parent's journal stays readable instead of interleaving two graphs' events.

    **Genealogy is threaded, so a nested tree is queryable.** `parent_run_id` names the immediate
    parent and `root_run_id` names the top of the tree — both are needed: the parent answers "who
    spawned this?", and the root answers "show me everything this user request did", which is the
    query a widget and the ledger actually run.

    **Depth is capped at 3 and checked BEFORE anything is created.** A workflow that references
    itself would otherwise spawn runs until the process died, each one with a row and a directory
    to clean up. Refused as a USER failure with the ref named, because the fix is in the spec.

    **Waited on, not fired and forgotten.** A subworkflow node's whole purpose is to produce an
    output the parent binds to; returning `launched` (the `run-workflow` provider's contract) would
    make `{{nodes.child.output}}` resolve to nothing. The wait is bounded by the node's timeout,
    and a timeout leaves the child RUNNING — it is a real run and killing it would discard work
    the
    parent merely stopped waiting for.
    """
    cfg = node.config or {}
    ref = str(cfg.get("ref", "") or "").strip()
    if not ref:
        return _fail(
            FailureClass.USER,
            "subworkflow node has no `ref`",
            "set `config.ref` to a workflow definition name (optionally `name@version`)",
        )
    name = ref.split("@", 1)[0]

    if depth + 1 > MAX_SUBWORKFLOW_DEPTH:
        return _fail(
            FailureClass.USER,
            f"subworkflow nesting would exceed depth {MAX_SUBWORKFLOW_DEPTH} at {ref!r}",
            "flatten one level, or check whether the workflow references itself",
        )
    if supervisor is None:
        return _fail(
            FailureClass.INTERNAL,
            "no workflow supervisor is available to run a subworkflow",
            "this is an engine wiring problem — the controller must pass its supervisor",
        )

    from gideon.automation.workflows import defs as defs_mod
    from gideon.automation.workflows import store
    from gideon.automation.workflows.models import (
        TERMINAL_RUN_STATUSES,
        OriginKind,
        RunOrigin,
        RunStatus,
        WorkflowRun,
    )

    definition = None
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            continue
        if found is not None:
            definition = found
            break
    if definition is None:
        return _fail(
            FailureClass.USER,
            f"no workflow definition named {name!r}",
            "check the name, or list the available definitions",
        )

    spec = definition if isinstance(definition, dict) else definition.to_dict()

    raw_inputs = cfg.get("inputs") if isinstance(cfg.get("inputs"), dict) else {}
    child_inputs: dict[str, Any] = {}
    for key, value in (raw_inputs or {}).items():
        try:
            child_inputs[str(key)] = resolve(value, ctx)
        except BindingError as exc:
            return _fail(
                FailureClass.USER,
                f"subworkflow input {key!r} did not resolve: {exc}",
                "check the referenced node id and field exist",
            )

    parent = store.get(run_id) if run_id else None
    child = store.create(
        WorkflowRun(
            id="",
            workflow_name=name,
            status=RunStatus.DRAFT,
            inputs=child_inputs,
            mode="background",
            parent_run_id=run_id or None,
            root_run_id=(parent.root_run_id if parent else "") or run_id or "",
            project_id=parent.project_id if parent else "",
            origin=RunOrigin(kind=OriginKind.SUBAGENT_TOOL, trigger_id=node.id),
        )
    )
    store.write_spec(child.id, spec)

    try:
        controller = await supervisor.launch(child, spec, depth=depth + 1)
    except Exception as exc:
        return _failed_with(
            FailureClass.INTERNAL,
            f"could not start subworkflow {name!r}: {exc}",
            "check the gateway log for the child run's launch failure",
            {"child_run_id": child.id},
        )

    try:
        status = await _wait_with_progress(controller, float(timeout or 0), on_progress)
    except Exception as exc:
        return _failed_with(
            FailureClass.INTERNAL,
            f"subworkflow {name!r} failed while running: {exc}",
            "inspect the child run directly",
            {"child_run_id": child.id},
        )

    child_outputs: dict[str, Any] = {}
    try:
        for path, inst in store.read_state(child.id).items():
            if inst.output_ref:
                value = store.read_output(child.id, path)
                if value is not None:
                    child_outputs[path.split(".")[-1].split("#")[0].split("@")[0]] = (
                        value
                    )
    except Exception:
        logger.debug(
            "subworkflow %s: could not collect child outputs", child.id, exc_info=True
        )

    payload: dict[str, Any] = {
        "child_run_id": child.id,
        "workflow": name,
        "status": status.value if hasattr(status, "value") else str(status),
        "outputs": child_outputs,
    }

    if status not in TERMINAL_RUN_STATUSES:
        return NodeResult(
            state=InstanceState.DEGRADED,
            output=payload,
            degraded_reason=f"subworkflow is still {payload['status']}",
        )
    if status == RunStatus.COMPLETE:
        return NodeResult(state=InstanceState.DONE, output=payload)
    return _failed_with(
        FailureClass.USER,
        f"subworkflow {name!r} ended {payload['status']}",
        f"inspect child run {child.id} for the failing node",
        payload,
    )


async def dispatch_action(
    node: Node,
    ctx: BindingContext,
    *,
    get_provider: Any = None,
    timeout: int = 60,
    run_id: str = "",
    project_id: str = "",
    instance_path: str = "",
    cwd: str = "",
) -> NodeResult:
    """Dispatch to an action provider — zero tokens.

    The provider's `outcome` honesty contract is preserved: `"launched"` means background
    work STARTED, not that it succeeded, so it maps to DEGRADED with a reason rather than
    a clean DONE. Reporting it as success would make a fire-and-forget action look
    verified.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    name = str(cfg.get("provider", "") or "")
    if not name:
        return _fail(
            FailureClass.USER,
            "action node has no `provider`",
            "set `config.provider` to a registered action provider",
        )

    getter = get_provider
    if getter is None:
        from gideon.integrations.action_providers.registry import get_action_provider

        getter = get_action_provider
    provider = getter(name)
    if provider is None:
        return _fail(
            FailureClass.USER,
            f"unknown action provider {name!r}",
            "install the app that provides it, or pick a registered provider",
        )

    from gideon.integrations.action_providers.base import ActionContext

    action_config = dict(cfg.get("with") or cfg.get("config") or {})
    payload = dict(cfg.get("payload") or {})
    payload.setdefault("node_id", getattr(node, "id", "") or "")
    if run_id:
        payload.setdefault("run_id", run_id)
    if instance_path:
        payload.setdefault("instance_path", instance_path)
    if project_id:
        payload.setdefault("project_id", project_id)
    if cwd:
        payload.setdefault("workspace", cwd)
    context = ActionContext(
        event="workflow_node",
        context=str(cfg.get("context", "") or ""),
        payload=payload,
    )
    try:
        result = await provider.execute(action_config, context, timeout=timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return NodeResult(state=InstanceState.FAILED, failure=_classify_exception(exc))

    output: Any = _action_output(result)
    contract = (node.config or {}).get("output_contract")
    if isinstance(contract, dict) and getattr(result, "success", False):
        problem = check_output_contract(output, contract)
        if problem:
            return _fail(
                FailureClass.PROTOCOL,
                f"output contract violated: {problem}",
                "fix the provider output or relax the node's output_contract",
            )

    if not getattr(result, "success", False):
        err = (
            getattr(result, "error", "")
            or getattr(result, "stderr", "")
            or "action failed"
        )
        return NodeResult(
            state=InstanceState.FAILED,
            output=output,
            failure=Failure(
                failure_class=FailureClass.TRANSIENT,
                cause_plain=str(err)[:500],
                remediation=_provider_fix(result),
                recoverable=True,
            ),
        )
    if getattr(result, "outcome", "") == "launched":
        return NodeResult(
            state=InstanceState.DEGRADED,
            output=output,
            degraded_reason="action started background work; completion not verified",
        )
    if getattr(result, "outcome", "") == "queued":
        return NodeResult(
            state=InstanceState.DEGRADED,
            output=output,
            degraded_reason="action queued a run behind one already in flight; it has not started",
        )
    if getattr(result, "outcome", "") == "needs_input":
        return NodeResult(
            state=InstanceState.WAITING,
            output=output,
            degraded_reason=str(
                getattr(result, "stderr", "") or "the action needs your input"
            ),
        )
    if getattr(result, "outcome", "") == "skip":
        return NodeResult(state=InstanceState.NO_CHANGE, output=output)
    return NodeResult(state=InstanceState.DONE, output=output)


async def dispatch_wait(node: Node, ctx: BindingContext, *, now: float) -> NodeResult:
    """Park until a deadline. Returns WAITING with a `wake_at`.

    Outgoing edges activate at WAIT-ENTRY, not on completion (WF2-R18). This is the
    subtle half of active-edge gating: a 3-way fan-out with one fast leg and two waiting
    legs would otherwise fire its join after the fast leg alone.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    until = cfg.get("until_ts")
    duration = cfg.get("duration_secs")
    seal = cfg.get("seal")

    if isinstance(seal, dict):
        return _dispatch_seal(seal, ctx, now=now)

    if isinstance(until, (int, float)) and until > 0:
        deadline = float(until)
    elif isinstance(duration, (int, float)) and duration > 0:
        deadline = now + float(_adaptive_duration(cfg, float(duration)))
    else:
        return _fail(
            FailureClass.USER,
            "wait node has no duration_secs, until_ts or seal",
            "set one of them",
        )
    if deadline <= now:
        return NodeResult(state=InstanceState.DONE, output={"waited": True})
    return NodeResult(state=InstanceState.WAITING, wake_at=deadline)


def _adaptive_duration(cfg: dict[str, Any], configured: float) -> int:
    """The wait's duration after an optional model-proposed override, clamped.

    `adaptive` names the binding that carries the proposal (usually
    `{{last.output.next_cycle_delay_seconds}}`); `min_secs`/`max_secs` bound it. The
    template's own `duration_secs` is the fallback, so a garbage proposal keeps the declared
    cadence rather than falling to the floor — "the model returned nonsense" must not make the
    loop faster.
    """
    proposed = cfg.get("adaptive")
    if proposed is None:
        return int(configured)
    secs, reason = longrun.clamp_delay(
        proposed,
        default=int(configured),
        minimum=_int_or(cfg.get("min_secs"), longrun.MIN_ADAPTIVE_DELAY_SECS),
        maximum=_int_or(cfg.get("max_secs"), longrun.MAX_ADAPTIVE_DELAY_SECS),
    )
    if reason:
        logger.info("adaptive wait delay adjusted: %s -> %ss", reason, secs)
    return secs


def _int_or(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    return int(raw)


def _dispatch_seal(
    seal: dict[str, Any], ctx: BindingContext, *, now: float
) -> NodeResult:
    """Buffer-seal wait: ready when the buffer fills, else a bounded re-check.

    The buffer contents come from a binding (`items`), so the seal is a pure function of what
    the watcher's sibling has produced — no engine-side buffer to persist, and therefore
    nothing to lose on a restart.
    """
    raw_items = seal.get("items")
    items: list[Any] = []
    if raw_items is not None:
        try:
            resolved = (
                resolve(raw_items, ctx) if isinstance(raw_items, str) else raw_items
            )
        except Exception:
            resolved = []
        items = resolved if isinstance(resolved, list) else []

    buffer = longrun.BufferState(
        items=items,
        seal_threshold=_int_or(seal.get("threshold"), 20),
        seal_tokens=_int_or(seal.get("tokens"), 0),
        flush_stale_after_secs=_int_or(seal.get("flush_stale_after_secs"), 3600),
        last_flush_at=float(_int_or(seal.get("last_flush_at"), 0)),
    )
    sealed, reason = buffer.should_seal(now=now)
    if sealed:
        return NodeResult(
            state=InstanceState.DONE,
            output={
                "waited": False,
                "sealed": True,
                "reason": reason,
                "count": len(items),
            },
        )
    check_every = _int_or(seal.get("check_every_secs"), 60)
    return NodeResult(state=InstanceState.WAITING, wake_at=now + max(1, check_every))


class GuardOutcome(str, Enum):
    """What the regression half of a dual gate concluded. CLOSED and mapped exhaustively
    by `_dual_guard` — an unmapped value falling through to "passed" is the shape that
    makes a regression gate look enforced while it certifies breakage.
    """

    SKIPPED = "skipped"
    CLEAN = "clean"
    PRE_EXISTING = "pre_existing"
    REGRESSION = "regression"
    UNDETERMINED = "undetermined"


async def _dual_guard(block: dict[str, Any], verify: Any) -> NodeResult:
    """The REGRESSION half of a dual verify+guard gate (LOOPS-EVOLUTION R5e).

    The metric command has already passed when this runs: `command` says the deliverable is
    done, and `guard` says nothing else got worse. Two commands rather than one because
    they answer different questions, and a single command that mixes them cannot tell "my
    feature works" from "I broke the suite".

    **The classification is the point, and it is deterministic — no model involved.** A
    guard failure is only a regression if the guard was PASSING before the change, which is
    what `guard_baseline` carries (captured by a baseline node before the first mutating
    step). Without that comparison the honest answers "you broke this" and "this was
    already broken" are indistinguishable, and criterion 6 exists because they must not be.
    """
    from gideon.automation.workflows.conditions import truthy

    guard_cmd = str(block.get("guard", "") or "").strip()
    if not guard_cmd:
        return NodeResult(
            state=InstanceState.DONE,
            output={"verified": True, "guard": GuardOutcome.SKIPPED.value},
        )

    guard_block = {
        "command": guard_cmd,
        "cwd": block.get("cwd", ""),
        "label": "guard",
    }
    try:
        outcome = await verify(guard_block)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return NodeResult(state=InstanceState.FAILED, failure=_classify_exception(exc))

    baseline_passed = truthy(block.get("guard_baseline"))
    if outcome is True:
        verdict = GuardOutcome.CLEAN
    elif outcome is None:
        verdict = GuardOutcome.UNDETERMINED
    elif baseline_passed:
        verdict = GuardOutcome.REGRESSION
    else:
        verdict = GuardOutcome.PRE_EXISTING

    output = {
        "verified": True,
        "guard": verdict.value,
        "guard_command": guard_cmd,
        "guard_passed_at_baseline": baseline_passed,
    }

    if verdict == GuardOutcome.CLEAN:
        return NodeResult(state=InstanceState.DONE, output=output)
    if verdict == GuardOutcome.PRE_EXISTING:
        return NodeResult(
            state=InstanceState.DEGRADED,
            output=output,
            degraded_reason=(
                "the guard command was already failing before this change (pre-existing, "
                "not a regression)"
            ),
        )
    if verdict == GuardOutcome.REGRESSION:
        return _failed_with(
            FailureClass.USER,
            "regression: the guard command passed at baseline and fails now",
            "fix what this change broke, or narrow the change until the guard passes again",
            output,
        )
    if verdict == GuardOutcome.UNDETERMINED:
        return _failed_with(
            FailureClass.INTERNAL,
            "the guard command could not be run, so no regression verdict is possible",
            "check the guard command exists and is executable in the run workspace",
            output,
        )
    return _failed_with(
        FailureClass.INTERNAL,
        f"unhandled guard outcome {verdict.value!r}",
        "map the new GuardOutcome member in `_dual_guard`",
        output,
    )


async def dispatch_gate(
    node: Node,
    ctx: BindingContext,
    *,
    now: float,
    verify: Any = None,
    completion: Any = None,
    tiers: dict[str, str] | None = None,
    mode: str = "background",
    worker_model: str = "",
    judge_model_resolver: Any = None,
    compaction_saves: list[float] | None = None,
    judge_hints: JudgeHints | None = None,
) -> NodeResult:
    """A checkpoint the engine — never the worker — resolves (WF2-R3).

    An `expression` gate is decided here. `approval` parks in WAITING with a typed ask
    payload. `verify_command`/`verify_script` run a deterministic validator through the
    injected `verify` callable. A stage may REQUEST completion; only this flips it.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    raw = str(cfg.get("kind", "") or "")
    try:
        kind = GateKind(raw)
    except ValueError:
        return _fail(
            FailureClass.USER,
            f"unknown gate kind {raw!r}",
            "use approval|verify_command|verify_script|event|expression",
        )

    if kind == GateKind.EXPRESSION:
        from gideon.automation.workflows.conditions import (
            evaluate as evaluate_condition,
        )

        expr = str((node.config or {}).get("expr", "") or "")
        if not expr.strip():
            return _fail(
                FailureClass.USER,
                "expression gate has no `expr`",
                "add `config.expr` with the condition the gate tests",
            )
        try:
            passed = evaluate_condition(expr, ctx)
        except BindingError as exc:
            return _fail(
                FailureClass.USER,
                f"gate expression failed to resolve: {exc}",
                "check the referenced nodes exist",
            )
        return NodeResult(
            state=InstanceState.DONE if passed else InstanceState.FAILED,
            output={"passed": passed, "expr": expr},
            failure=(
                None
                if passed
                else Failure(
                    failure_class=FailureClass.USER,
                    cause_plain=f"gate condition is false: {expr}",
                    remediation="inspect the upstream node output the gate tests",
                )
            ),
        )

    if kind in (GateKind.VERIFY_COMMAND, GateKind.VERIFY_SCRIPT):
        block = cfg.get("verify")
        if not isinstance(block, dict):
            return _fail(
                FailureClass.USER,
                f"{raw} gate has no `verify` block",
                "add `config.verify` with a command/script",
            )
        if verify is None:
            return _fail(
                FailureClass.INTERNAL,
                "no verifier wired for this gate",
                "the engine did not provide a verify callable",
            )
        try:
            outcome = await verify(block)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return NodeResult(
                state=InstanceState.FAILED, failure=_classify_exception(exc)
            )
        if outcome is None:
            return _fail(
                FailureClass.INTERNAL,
                "verification could not be determined (verifier did not run)",
                "check the command exists and is executable in the run workspace",
            )
        if outcome is not True:
            return _fail(
                FailureClass.USER,
                "verification failed",
                "fix the reported problems and re-run this node",
            )
        return await _dual_guard(block, verify)

    if kind == GateKind.LADDER:
        criteria = cfg.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            return _fail(
                FailureClass.USER,
                "ladder gate has no `criteria`",
                "declare an ordered list of criteria with rung + threshold",
            )
        if verify is None:
            return _fail(
                FailureClass.INTERNAL,
                "no verifier wired for this gate",
                "the engine did not provide a verify callable",
            )
        evaluated: dict[str, Any] = {}
        for crit in criteria:
            name = str((crit or {}).get("name", "") or "criterion")
            try:
                evaluated[name] = await verify(crit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return NodeResult(
                    state=InstanceState.FAILED, failure=_classify_exception(exc)
                )
        ladder = run_ladder(criteria, evaluated)
        if ladder.passed:
            return NodeResult(state=InstanceState.DONE, output=ladder.to_dict())
        return NodeResult(
            state=InstanceState.FAILED,
            output=ladder.to_dict(),
            failure=Failure(
                failure_class=FailureClass.USER,
                cause_plain=f"verification ladder rejected at the {ladder.stopped_at} rung",
                remediation="fix the failing criterion; a hard failure is never averaged away",
            ),
        )

    if kind == GateKind.JUDGE:
        prompt = str(cfg.get("prompt", "") or "")
        if not prompt.strip():
            return _fail(
                FailureClass.USER,
                "judge gate has no `prompt`",
                "add the rubric the judge should apply",
            )

        pre = _judge_pretier_screen(cfg)
        if pre is not None:
            return pre

        fn = completion
        if fn is None:
            from gideon.integrations.llm_helpers import one_shot_completion

            fn = one_shot_completion
        use_case = (
            resolve_use_case(node, tiers)
            if node.config.get("model_tier")
            else "reasoning"
        )

        judge_model = ""
        if str(cfg.get("isolation", "") or "").strip().lower() == "cross_model":
            from gideon.automation.workflows.judge_actors import (
                plan_judge_session,
                validate_judge_model,
            )

            spec = plan_judge_session(
                isolation="cross_model", worker_model=worker_model
            )
            if not spec.avoid_family:
                return _fail(
                    FailureClass.USER,
                    "cross_model judge isolation unsatisfiable: the worker's model family could "
                    "not be determined, so a different-family judge cannot be proven",
                    "bind a model for the worker tier so its family is known, or use "
                    "isolation: fresh",
                )
            resolve_candidate = judge_model_resolver or resolve_axis_model
            candidate = str(resolve_candidate(use_case) or "")
            ok, reason = validate_judge_model(spec, candidate)
            if not ok:
                return _fail(
                    FailureClass.USER,
                    f"cross_model judge isolation unsatisfiable: {reason}",
                    "configure a different-family model for the judge tier, or use "
                    "isolation: fresh",
                )
            judge_model = candidate

        hints = judge_hints or JudgeHints()
        from gideon.automation.workflows.judge_actors import (
            Actor,
            assemble_judge_evidence,
        )
        from gideon.automation.workflows.judge_actors import (
            resolve_transition as rule_transition,
        )

        blinded = assemble_judge_evidence(
            [
                {"role": "spec", "content": prompt},
                {"role": "tool_output", "content": str(cfg.get("evidence", "") or "")},
            ],
            blind=True,
        )
        rubric_prose = str(blinded[0].get("content", "")) if blinded else prompt
        measured = str(blinded[1].get("content", "")) if len(blinded) > 1 else ""
        if measured and measured not in rubric_prose:
            rubric_prose = f"{rubric_prose}\n\nThe measured evidence:\n{measured}"
        instruction = judge_instruction(rubric_prose, hints)
        samples = _judge_sample_count(cfg)
        fallback_result: bool | None = None
        if isinstance(cfg.get("verify"), dict) and verify is not None:
            try:
                fallback_result = await verify(cfg["verify"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("judge fallback check failed to run", exc_info=True)
        judged: list[JudgeVerdict] = []
        texts: list[str] = []
        pin = {"model": judge_model} if judge_model else {}
        for _ in range(samples):
            try:
                text = await complete_with_compaction(
                    fn,
                    instruction,
                    use_case=use_case,
                    output_type=None,
                    saves=compaction_saves,
                    model=str(pin.get("model", "")),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return NodeResult(
                    state=InstanceState.FAILED,
                    failure=_classify_exception(exc),
                    resolved_prompt=instruction,
                )
            texts.append(str(text))
            answer = parse_judge_json(text)
            if answer is None:
                unparseable = True
                break
            judged.append(
                validate_verdict(
                    answer,
                    hints,
                    fallback_result=fallback_result,
                    evidence_text=rubric_prose,
                )
            )
        else:
            unparseable = False
        text = texts[-1] if texts else ""
        sampled_tokens = sum(_estimate_tokens(instruction, body) for body in texts)
        sample_values = [v.verdict.value for v in judged]
        if unparseable:
            return NodeResult(
                state=InstanceState.FAILED,
                output={
                    "verdict": "",
                    "judge_evidence": {
                        "samples": sample_values,
                        "texts": [t[:2000] for t in texts],
                        "sample_count": samples,
                        "aggregated": "",
                        "protocol_error": True,
                    },
                    "judge_status": "kept",
                },
                failure=Failure(
                    failure_class=FailureClass.PROTOCOL,
                    cause_plain=(
                        "judge did not return the contract JSON object: "
                        f"{str(text)[:120]}"
                    ),
                    remediation="tighten the rubric, or use a ladder gate for a "
                    "deterministic check",
                ),
                resolved_prompt=instruction,
                tokens=sampled_tokens,
            )
        decision = aggregate_samples(judged, hints)
        state, failure = _judge_gate_outcome(decision, node)
        judge_status = (
            "kept"
            if not sample_values or decision.verdict.value in sample_values
            else "discard"
        )
        actor = Actor.JUDGE if requires_fresh_judge(node.config) else Actor.WORKER
        effective, note = rule_transition(actor, state.value)
        output = {
            "verdict": decision.verdict.value,
            "judge_verdict": decision.to_dict(),
            "judge_evidence": {
                "samples": sample_values,
                "texts": [t[:2000] for t in texts],
                "sample_count": samples,
                "aggregated": decision.verdict.value,
                "scores": dict(decision.scores),
                "overall": decision.overall,
                "shortfalls": list(decision.shortfalls),
                "invalid_reason": decision.invalid_reason,
            },
            "judge_status": judge_status,
        }
        if effective == "review" or state is InstanceState.WAITING:
            from gideon.automation.workflows.human_input import gate_timeout_secs

            timeout = gate_timeout_secs(cfg, mode=mode)
            parked = dict(output)
            if effective == "review":
                parked["actor_note"] = note
            return NodeResult(
                state=InstanceState.WAITING,
                output=parked,
                wake_at=now + float(timeout) if timeout > 0 else 0.0,
                ask=_ask_payload(node, cfg),
                resolved_prompt=instruction,
                tokens=sampled_tokens,
            )
        return NodeResult(
            state=state,
            output=output,
            failure=failure,
            resolved_prompt=instruction,
            tokens=sampled_tokens,
        )

    from gideon.automation.workflows.human_input import gate_timeout_secs

    timeout_secs = gate_timeout_secs(cfg, mode=mode)
    wake = now + float(timeout_secs) if timeout_secs > 0 else 0.0
    return NodeResult(
        state=InstanceState.WAITING,
        wake_at=wake,
        ask=_ask_payload(node, cfg),
    )


def _ask_payload(node: Node, cfg: dict[str, Any]) -> dict[str, Any]:
    """The typed human-input ask (WF2-R7). One renderer covers every gate, which is why
    the shape is fixed by `human_input.Ask` rather than left to each template."""
    from gideon.automation.workflows.human_input import Ask

    return Ask.from_dict(
        {
            "kind": cfg.get("ask_kind", "approval"),
            "prompt": str(cfg.get("prompt", "") or cfg.get("message", "") or ""),
            "fields": cfg.get("fields") or [],
            "choices": cfg.get("choices") or [],
            "node_id": node.id,
            "unattended_suppress": strict_bool(
                cfg.get("unattended_suppress"), field="unattended_suppress"
            ),
        }
    ).to_dict()


def apply_artifact_gate(node: Node, result: NodeResult, workspace: Any) -> NodeResult:
    """Refuse a node's completion until its declared `required_artifacts` exist (WF2-R3).

    Applied at the dispatch seam rather than inside each producing dispatcher, so a new
    node kind inherits the gate instead of silently skipping it. A node that CLAIMS to have
    written files but did not is the single most common way agent-declared completion lies.
    """
    patterns = (node.config or {}).get("required_artifacts")
    if not isinstance(patterns, list) or not patterns:
        return result
    if result.state not in (InstanceState.DONE, InstanceState.DEGRADED):
        return result
    if workspace is None:
        return NodeResult(
            state=InstanceState.FAILED,
            output=result.output,
            failure=Failure(
                failure_class=FailureClass.INTERNAL,
                cause_plain="required_artifacts declared but the run has no workspace",
                remediation="the engine did not provide a workspace path for this run",
            ),
        )
    from pathlib import Path

    check = check_required_artifacts([str(p) for p in patterns], Path(workspace))
    if check.satisfied:
        payload = result.output
        if isinstance(payload, dict):
            payload = {**payload, "artifacts": check.digests}
        result.output = payload
        return result
    return NodeResult(
        state=InstanceState.FAILED,
        output=result.output,
        failure=Failure(
            failure_class=FailureClass.USER,
            cause_plain=("required artifacts missing: " + ", ".join(check.missing)),
            remediation="the node reported success without producing its declared files; "
            "check where it actually wrote them",
        ),
    )


def apply_judge_contract(
    node: Node, result: NodeResult, judge_hints: JudgeHints | None
) -> NodeResult:
    """Validate a judge STAGE's output against the contract (WF2LOO-13).

    Six bundled templates carry a `judge` stage whose prompt asks for this exact object —
    `{reasoning, verdict, scores, evidence_refs, proof, cannot_judge}` — and before this seam
    NOTHING validated it and NOTHING read it: `dispatch_stage`/`dispatch_infer` return DONE for
    any parseable JSON whatever the verdict says. A contract-shaped verdict was produced every
    loop iteration and discarded.

    Applied at the ONE dispatch seam, like `apply_artifact_gate`, so a node kind cannot skip it.
    Opt-in via `config.judge_contract: true`, because the seam sees every node and only a judge's
    output is a verdict.

    **It validates and BINDS; it does not fail the node.** Deliberate, and the difference between
    a gate and an outage: those stage prompts tell the model in as many words that "reporting
    real issues is the normal outcome", so a REJECT is expected traffic on 6 live templates and
    failing on it would convert normal operation into a failed run. What changes is that the
    bound output is now the ENGINE's record rather than the model's claim — `overall` recomputed
    from the scores, a PASS that cited no proof carried as `valid: false`, and `contract_valid`
    for the template's own `success_when` to read. The hard teeth belong to the judge GATE, where
    the engine owns completion.

    The model's own keys survive underneath the validated ones: a template may read something
    the contract does not model (`marginal_value`), and a loop's `progress_field` may point at
    any key in the body's output, so dropping them would break a reader this seam never saw.
    """
    if not bool((node.config or {}).get("judge_contract", False)):
        return result
    if result.state not in (InstanceState.DONE, InstanceState.DEGRADED):
        return result
    hints = judge_hints or JudgeHints()
    raw = result.output if isinstance(result.output, dict) else None
    decision = validate_verdict(raw, hints)
    payload: dict[str, Any] = dict(raw or {})
    payload.update(decision.to_dict())
    payload["contract_valid"] = decision.valid and not decision.protocol_error
    result.output = payload
    return result


def _restriction_skip(cfg: dict[str, Any], run_id: str) -> tuple[bool, str]:
    """Whether a restricted run must skip this node, resolved from the RUN's inherited mode.

    The mode lives on the run record, not on the node — a node cannot know whether the run it
    belongs
    to was launched from an incognito chat. Best-effort: if the run cannot be read, nothing is
    skipped, because a lookup failure must not silently stop doing the work the user asked for. The
    fail-closed direction in this feature is about the memory MODE (an unknown mode reads as
    restricted), not about whether the run executes.
    """
    if not run_id:
        return False, ""
    try:
        from gideon.automation.workflows import store

        run = store.get(run_id)
        raw = (
            (getattr(run, "extra", None) or {}).get(ownership.RUN_MODE_KEY, "")
            if run
            else ""
        )
        mode = ownership.parse_mode(raw)
    except Exception:
        logger.debug("restriction lookup failed for run %s", run_id, exc_info=True)
        return False, ""
    return ownership.skips_node(cfg, mode)


def _publish_media_resolver(cwd: str | None) -> Any:
    """A `rewrite_media_refs` resolver reading files under the run's own cwd — and only there.

    Containment is the whole security posture of the copy: `..` traversal and symlinks out of the
    tree are refused, so a published body cannot pull `~/.ssh/id_rsa` into an artifact the dashboard
    serves by writing `![](../../../.ssh/id_rsa)`. Returns None (never raises) for anything it will
    not read, which `rewrite_media_refs` reports as unresolved rather than silently dropping.
    """
    import hashlib
    from pathlib import Path

    from gideon.security.security import is_sensitive_path

    max_bytes = 8 * 1024 * 1024

    def _resolve(reference: str) -> tuple[bytes, str] | None:
        if not cwd:
            return None
        try:
            root = Path(cwd).resolve()
            target = (root / reference).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                return None
            if is_sensitive_path(str(target)):
                return None
            if target.stat().st_size > max_bytes:
                return None
            data = target.read_bytes()
        except (OSError, ValueError):
            return None
        return data, hashlib.sha256(data).hexdigest()

    return _resolve


def apply_publish(
    node: Node, result: NodeResult, *, run_id: str = "", cwd: str | None = None
) -> NodeResult:
    """Publish a node's output as an Artifact when it declares `publish:` (WORK-CONTAINERS §2,
    S47).

    At the dispatch seam beside the artifact gate, so a new node kind inherits publishing
    rather than
    silently dropping a declared output.

    A MALFORMED declaration FAILS the node. The alternative — treating it as "no publish" —
    would let
    a node whose author declared a deliverable report success while producing nothing, which is the
    completion-lie class the artifact gate exists to catch. A declaration is a promise about output.

    A REGISTRY failure does not fail the node. The work happened; losing the copy is worth reporting
    on the result, not worth discarding a completed stage over. The distinction is deliberate: a bad
    declaration is the author's bug (fail loudly), a registry outage is the environment's (degrade
    honestly).
    """
    from gideon.automation.workflows.publish import (
        PublishAction,
        flatten_lineage,
        parse_publish,
        rewrite_media_refs,
        upsert_plan,
    )

    cfg = node.config or {}
    if "publish" not in cfg:
        return result
    spec, error = parse_publish(cfg)
    if error:
        return NodeResult(
            state=InstanceState.FAILED,
            output=result.output,
            failure=Failure(
                failure_class=FailureClass.USER,
                cause_plain=f"invalid publish declaration: {error}",
                remediation=(
                    "fix the node's `publish:` block; it declares an output nothing produced"
                ),
            ),
        )
    if spec is None or result.state not in (InstanceState.DONE, InstanceState.DEGRADED):
        return result

    content = result.output if isinstance(result.output, str) else ""
    if not content and isinstance(result.output, dict):
        content = str(result.output.get("text") or result.output.get("output") or "")
    if not content.strip():
        return _with_publish(
            result, {"action": "noop", "reason": "node output was not text"}
        )

    try:
        from gideon.workspace.artifacts.registry import (
            get_provider as _artifact_provider,
        )

        provider = _artifact_provider()
        if provider is None or provider.readonly:
            return _with_publish(
                result, {"action": "noop", "reason": "no writable artifact provider"}
            )
        content, media_copies, media_unresolved = rewrite_media_refs(
            content, _publish_media_resolver(cwd)
        )
        existing = provider.find_similar(spec.artifact)
        previous = None
        if existing is not None:
            detail = provider.get(existing.slug)
            previous = getattr(detail, "content", None) if detail else None
        plan = upsert_plan(
            spec,
            content,
            existing_content=previous,
            run_id=run_id,
            node_id=node.id or "",
        )
        event_meta = {
            "run_id": run_id,
            "node_id": node.id or "",
            "change_note": plan.change_note,
            **flatten_lineage(plan.lineage),
        }
        if plan.action is PublishAction.CREATE:
            created = provider.create(
                name=spec.artifact,
                content=content,
                kind=spec.kind,
                source="subagent",
                description=spec.description,
                actor="workflow",
                event_metadata=event_meta,
            )
            payload = {**plan.to_dict(), "slug": getattr(created, "slug", "")}
        elif plan.action is PublishAction.VERSION and existing is not None:
            updated = provider.update(
                existing.slug,
                content=content,
                snapshot=True,
                event_type="iterated",
                actor="workflow",
                event_metadata=event_meta,
            )
            payload = {
                **plan.to_dict(),
                "slug": getattr(updated, "slug", existing.slug if existing else ""),
            }
        else:
            payload = {**plan.to_dict(), "slug": existing.slug if existing else ""}
        payload["media"] = _land_media_copies(
            provider, str(payload.get("slug") or ""), media_copies, media_unresolved
        )
        _journal_publish(run_id, node.id or "", payload)
        _open_publish_outcome(run_id, node.id or "", payload)
        return _with_publish(result, payload)
    except Exception as exc:
        logger.debug("publish failed for node %s", node.id, exc_info=True)
        return _with_publish(
            result, {"action": "error", "reason": f"{type(exc).__name__}: {exc}"}
        )


PUBLISH_CONSUMPTION_HORIZON_SECS = 7 * 24 * 3600.0


def _open_publish_outcome(run_id: str, node_id: str, payload: dict[str, Any]) -> None:
    """Open the artifact's outcome question: we published a deliverable — did anyone consume it?

    The `publish:` producer of the general outcome facility (PP-9). Publishing records what the run
    DID; this records the bet about what it was FOR, so an artifact stream nobody reads becomes a
    measurable fact instead of a busy outbox. `PP-10` supplies the ground truth this asks for: a
    :data:`~gideon.assurance.ledger.outcomes.SOURCE_CONSUMPTION` question is graded off the artifact's
    own lifecycle timeline and the dashboard pin list — writers that already exist — so the answer
    is a real `measured` 1.0/0.0 on any box, with no vector store and no new counter. `PP-10`'s
    dormancy sweep (`learning/consumer_liveness.py`) then reads the RESOLUTIONS and proposes pausing
    or retiring a work unit whose last N cycles all went untouched.

    Best-effort, like the publish journal beside it: the artifact already landed, and no ledger
    write is worth failing a completed stage over.
    """
    slug = str(payload.get("slug") or "")
    if not run_id or not slug:
        return
    from gideon.assurance.ledger import outcomes
    from gideon.automation.workflows.journal import Journal

    try:
        Journal(run_id).open_outcome(
            producer=outcomes.PRODUCER_PUBLISH,
            subject=f"published artifact `{slug}`",
            metric=outcomes.consumption_metric(slug),
            metric_source=outcomes.SOURCE_CONSUMPTION,
            horizon_secs=PUBLISH_CONSUMPTION_HORIZON_SECS,
            baseline=1.0,
            node_id=node_id,
            slug=slug,
            artifact=str(payload.get("artifact") or ""),
            action=str(payload.get("action") or ""),
        )
    except Exception:
        logger.debug("publish outcome open failed for run %s", run_id, exc_info=True)


def _journal_publish(run_id: str, node_id: str, payload: dict[str, Any]) -> None:
    """Record one publish outcome in the run's own log — what the §2.5 outbox lists.

    A run-scoped journal rather than a query over the artifact registry: the registry knows an
    artifact exists, not which run published it, and reconstructing that from event metadata means
    scanning every artifact's events to answer "what did THIS run produce". A NOOP is journalled
    too,
    because an outbox that hides a converged republish makes the artifact look abandoned by its
    producer — the same reason `upsert_plan` attaches provenance to a no-op.
    """
    if not run_id or not payload.get("slug"):
        return
    from datetime import datetime, timezone

    from gideon.automation.workflows import store as _store

    try:
        _store.append_jsonl(
            run_id,
            "publishes.jsonl",
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "node_id": node_id,
                "slug": payload.get("slug", ""),
                "artifact": payload.get("artifact", ""),
                "kind": payload.get("kind", ""),
                "action": payload.get("action", ""),
                "change_note": payload.get("change_note", ""),
                "media": payload.get("media", {}),
            },
        )
    except Exception:
        logger.debug("publish journal write failed for run %s", run_id, exc_info=True)


def _land_media_copies(
    provider: Any, slug: str, copies: list[Any], unresolved: list[tuple[str, str]]
) -> dict[str, Any]:
    """Copy each referenced local file into the artifact's version dir. Reports what landed.

    `unresolved` rides in the SAME record as the successes so one read answers "is this artifact
    self-contained?". Split across two fields on two surfaces, the failures are the ones nobody
    looks at.
    """
    stored: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = [
        {"reference": r, "reason": why} for r, why in unresolved
    ]
    for copy in copies:
        ok = False
        if slug:
            try:
                ok = provider.store_version_file(slug, copy.filename, copy.data)
            except Exception:
                logger.debug(
                    "media copy failed for %s/%s", slug, copy.filename, exc_info=True
                )
                ok = False
        if ok:
            stored.append(
                {
                    "reference": copy.reference,
                    "filename": copy.filename,
                    "sha256": copy.sha256,
                    "size": copy.size,
                }
            )
        else:
            failed.append(
                {
                    "reference": copy.reference,
                    "reason": "the artifact store did not accept the copy",
                }
            )
    return {"stored": stored, "unresolved": failed, "self_contained": not failed}


def _with_publish(result: NodeResult, payload: dict[str, Any]) -> NodeResult:
    """Attach the publish outcome to the node's output without disturbing it.

    A string output stays reachable at its original binding path — wrapping it in a dict would
    break
    every `{{nodes.x.output}}` downstream, so publishing a node's output would change what its
    consumers read.
    """
    result.published = payload
    if isinstance(result.output, dict):
        result.output = {**result.output, "published": payload}
    return result


def check_output_contract(value: Any, contract: dict[str, Any]) -> str:
    """A ~0.3ms mechanical check before a node is marked complete.

    Runs BEFORE any `{{nodes.x.output}}` binding resolves, so malformed output never
    silently propagates through the graph. Returns "" when the value conforms, or a
    human-readable problem.
    """
    if contract.get("must_be_json"):
        if isinstance(value, str):
            if _parse_json_loose(value) is None:
                return "expected JSON, got unparseable text"
        elif not isinstance(value, (dict, list)):
            return f"expected JSON object/array, got {type(value).__name__}"

    required = contract.get("required_keys")
    if isinstance(required, list) and required:
        target = value
        if isinstance(target, str):
            target = _parse_json_loose(target)
        if not isinstance(target, dict):
            return "required_keys declared but output is not an object"
        missing = [k for k in required if str(k) not in target]
        if missing:
            return f"missing required keys: {', '.join(str(m) for m in missing)}"

    text = value if isinstance(value, str) else None
    if text is not None:
        min_len = contract.get("min_length")
        if isinstance(min_len, int) and len(text) < min_len:
            return f"output shorter than min_length {min_len}"
        max_len = contract.get("max_length")
        if isinstance(max_len, int) and len(text) > max_len:
            return f"output longer than max_length {max_len}"
        forbidden = contract.get("forbidden_phrases")
        if isinstance(forbidden, list):
            low = text.lower()
            hit = [p for p in forbidden if str(p).lower() in low]
            if hit:
                return f"output contains forbidden phrase: {hit[0]}"
    return ""


def _parse_json_loose(text: Any) -> Any:
    """Parse JSON, stripping markdown fencing first.

    Fenced output is the dominant real-world format failure and stripping it fixes most
    cases with ZERO retries — measurably cheaper than a retry round-trip.
    """
    import json

    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(raw[start : end + 1])
            except (TypeError, ValueError):
                continue
    return None


def _classify_exception(exc: BaseException) -> Failure:
    """Map an exception to the typed taxonomy. Only TRANSIENT/NETWORK are retryable, so
    this classification decides whether budget gets spent on a retry."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if isinstance(exc, TimeoutError) or "timeout" in low or "timed out" in low:
        return Failure(
            failure_class=FailureClass.TIMEOUT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="raise timeout_total, or split the node into smaller steps",
            recoverable=True,
        )
    if any(k in low for k in ("connection", "network", "dns", "unreachable", "socket")):
        return Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check connectivity; the engine will retry",
            recoverable=True,
        )
    if any(
        k in low
        for k in (
            "permission",
            "forbidden",
            "unauthorized",
            "credential",
            "access denied",
        )
    ):
        return Failure(
            failure_class=FailureClass.PERMISSION,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check the credential in Settings → Providers, or the tool's "
            "approval policy",
        )
    if any(
        k in low for k in ("rate limit", "429", "throttl", "overloaded", "capacity")
    ):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="the engine will back off and retry",
            recoverable=True,
        )
    if "outputcontract" in name.lower() or "schema" in low or "json" in low:
        return Failure(
            failure_class=FailureClass.PROTOCOL,
            cause_plain=f"{name}: {text}"[:500],
            remediation="tighten the schema in the prompt, or use produce-then-extract",
        )
    return Failure(
        failure_class=FailureClass.INTERNAL,
        cause_plain=f"{name}: {text}"[:500],
        remediation="check the gateway log for the full traceback",
    )


def _action_output(result: Any) -> Any:
    """A provider's canonical output. Prefers parsed JSON stdout (the BYOI contract is
    "stdout = one JSON object"), falling back to raw text."""
    stdout = getattr(result, "stdout", "") or ""
    parsed = _parse_json_loose(stdout)
    if parsed is not None:
        return parsed
    return {
        "stdout": stdout[:8000],
        "exit_code": int(getattr(result, "exit_code", 0) or 0),
        "outcome": getattr(result, "outcome", "") or "",
    }


def _provider_fix(result: Any) -> str:
    err = getattr(result, "agent_error", None)
    fix = getattr(err, "fix", "") if err else ""
    return str(fix) if fix else "check the action's configuration and the gateway log"


def _estimate_tokens(prompt: str, response: str) -> int:
    """A ~4-chars-per-token floor when the provider reported no usage.

    Deliberately an ESTIMATE and named as one: budgets treat it as a floor so an
    unreported call still costs something against the cap, rather than being free and
    letting an unmetered provider run away.
    """
    return max(1, (len(prompt) + len(response)) // 4)


async def dispatch(
    node: Node,
    ctx: BindingContext,
    *,
    now: float = 0.0,
    subagents: Any = None,
    depth: int = 0,
    run_id: str = "",
    project_id: str = "",
    instance_path: str = "",
    cwd: str = "",
    tiers: dict[str, str] | None = None,
    completion: Any = None,
    get_provider: Any = None,
    verify: Any = None,
    timeout: int = 60,
    mode: str = "background",
    supervisor: Any = None,
    on_progress: Any = None,
    worker_model: str = "",
    compaction_saves: list[float] | None = None,
    judge_hints: JudgeHints | None = None,
) -> NodeResult:
    """Route one node to its dispatcher.

    A container reaching here is an engine bug, not a spec problem — the frontier is
    supposed to recurse into containers and only ever hand back leaves. It is reported as
    INTERNAL so the distinction stays visible in the ledger.
    """
    clock = now or time.time()
    result = await _dispatch_inner(
        node,
        ctx,
        now=clock,
        subagents=subagents,
        depth=depth,
        run_id=run_id,
        project_id=project_id,
        instance_path=instance_path,
        cwd=cwd,
        tiers=tiers,
        completion=completion,
        get_provider=get_provider,
        verify=verify,
        timeout=timeout,
        mode=mode,
        supervisor=supervisor,
        on_progress=on_progress,
        worker_model=worker_model,
        compaction_saves=compaction_saves,
        judge_hints=judge_hints,
    )
    result = apply_artifact_gate(node, result, cwd or None)
    result = apply_judge_contract(node, result, judge_hints)
    return apply_publish(node, result, run_id=run_id, cwd=cwd or None)


async def _dispatch_inner(
    node: Node,
    ctx: BindingContext,
    *,
    now: float = 0.0,
    subagents: Any = None,
    depth: int = 0,
    run_id: str = "",
    project_id: str = "",
    instance_path: str = "",
    cwd: str = "",
    tiers: dict[str, str] | None = None,
    completion: Any = None,
    get_provider: Any = None,
    verify: Any = None,
    timeout: int = 60,
    mode: str = "background",
    supervisor: Any = None,
    on_progress: Any = None,
    worker_model: str = "",
    compaction_saves: list[float] | None = None,
    judge_hints: JudgeHints | None = None,
) -> NodeResult:
    kind = node.kind
    clock = now or time.time()
    if kind == NodeKind.TRANSFORM:
        return await dispatch_transform(node, ctx)
    if kind == NodeKind.INFER:
        return await dispatch_infer(
            node,
            ctx,
            tiers=tiers,
            completion=completion,
            compaction_saves=compaction_saves,
        )
    if kind == NodeKind.VISUALIZE:
        return await dispatch_visualize(node, ctx, completion=completion)
    if kind == NodeKind.STAGE:
        return await dispatch_stage(
            node, ctx, subagents=subagents, depth=depth, run_id=run_id, cwd=cwd
        )
    if kind == NodeKind.BRANCH:
        return await dispatch_branch(node, ctx)
    if kind == NodeKind.ACTION:
        return await dispatch_action(
            node,
            ctx,
            get_provider=get_provider,
            timeout=timeout,
            run_id=run_id,
            project_id=project_id,
            instance_path=instance_path,
            cwd=cwd,
        )
    if kind == NodeKind.WAIT:
        return await dispatch_wait(node, ctx, now=clock)
    if kind == NodeKind.GATE:
        return await dispatch_gate(
            node,
            ctx,
            now=clock,
            verify=verify,
            completion=completion,
            tiers=tiers,
            mode=mode,
            worker_model=worker_model,
            compaction_saves=compaction_saves,
            judge_hints=judge_hints,
        )
    if kind == NodeKind.SUBWORKFLOW:
        return await dispatch_subworkflow(
            node,
            ctx,
            depth=depth,
            run_id=run_id,
            supervisor=supervisor,
            timeout=timeout,
            on_progress=on_progress,
        )
    return _fail(
        FailureClass.INTERNAL,
        f"{kind.value} is a container and has no dispatcher",
        "this is an engine bug — the frontier should never return a container",
    )
