"""The ONE loop supervisor — one evaluator over a declared policy (`PP-16`, seam 3).

A loop's done-ness used to be pluggable Python: a ``LoopKindStrategy.is_done_signal`` per kind
plus two satellite hooks (``has_done_check``, ``budget_stop_genuine``) the watchdog reached for
with ``getattr``, and a third rule (``_stagnation_disabled``) hard-coded in the watchdog against
one kind's name. Five implementations, three lookup styles, and no single place that answered
"what completes this loop?".

This module is that place. It reads a :class:`~gideon.automation.workflows.supervisor_policy.\
SupervisorPolicy` — the object `PP-14` declared and `PP-15` wired — and dispatches its
``convergence.signal`` to the ONE implementation of that mechanism. The domain knowledge that
used to be spread over five modules is now DATA in
:data:`~gideon.automation.workflows.supervisor_policy.KIND_CONVERGENCE`; the code below is
kind-agnostic and never branches on ``loop.kind``.

**No new vocabulary.** A done-signal names which MECHANISM produces the answer; what a judge
actually decided still travels in ``judge_contract``'s verdict types, adjudicated by
``judge_contract.adjudicate``. `WF2LOO-16` reconciled those dialects and this module adds none.

**The tenet is unchanged: no agent certifies its own work.** Every mechanism here is the
SUPERVISOR's own read — a command it runs, a judge subagent it commissions — never the worker's
self-report. The watchdog still owns the lifecycle decision; this only supplies the signal.
"""

from __future__ import annotations

import logging

from gideon.automation.loop import files as loop_files
from gideon.automation.loop.loop import Loop
from gideon.automation.workflows.supervisor_policy import (
    DONE_NEVER,
    DONE_ORCHESTRATED,
    DONE_SIGNALS,
    DONE_VERIFY_COMMAND,
    ConvergenceSpec,
    SupervisorPolicy,
)

logger = logging.getLogger(__name__)


def _cfg(loop: Loop) -> dict:
    return loop.kind_config or {}


def _command(loop: Loop, spec: ConvergenceSpec) -> str:
    """The command the policy points at, read off the loop's own config."""
    if not spec.command_key:
        return ""
    return str(_cfg(loop).get(spec.command_key, "") or "")


def _criteria(loop: Loop, spec: ConvergenceSpec) -> list[str]:
    """The criteria list the policy points at (a verifiable goal's sub-goals)."""
    if not spec.criteria_key:
        return []
    raw = _cfg(loop).get(spec.criteria_key, []) or []
    if not isinstance(raw, list):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


async def done_signal(
    loop: Loop, findings: list[dict], policy: SupervisorPolicy
) -> bool | None:
    """The loop's done-ness read for the CURRENT state, produced by something other than the
    worker. ``True`` = complete, ``False`` = keep going, ``None`` = can't tell (defer).

    Dispatches the policy's declared mechanism. An unknown mechanism is a programming error in
    the declaration table, not a runtime condition, so it raises rather than deferring — a
    silently-deferring loop is exactly the failure the closed
    :data:`~gideon.automation.workflows.supervisor_policy.DONE_SIGNALS` set exists to make impossible.
    """
    spec = policy.convergence
    if loop.kind == "research":
        cfg = _cfg(loop)
        try:
            min_pages = max(0, int(cfg.get("evidence_min_pages") or 0))
            min_domains = max(0, int(cfg.get("evidence_min_domains") or 0))
        except (TypeError, ValueError):
            min_pages = min_domains = 0
        if min_pages or min_domains:
            from gideon.automation.loop.research_sources import coverage

            have = coverage(loop.id)
            if have["pages"] < min_pages or have["domains"] < min_domains:
                loop_files.write_guidance(
                    loop.id,
                    f"Research needs readable-source coverage: {have['pages']}/{min_pages} "
                    f"distinct fetched pages and {have['domains']}/{min_domains} sites. "
                    "Fetch and read further primary sources, or state the unmet coverage "
                    "in the report if the run must stop.",
                )
                return False
    if spec.signal not in DONE_SIGNALS:
        raise ValueError(
            f"unknown done signal {spec.signal!r}; known: {sorted(DONE_SIGNALS)}"
        )
    if spec.signal == DONE_ORCHESTRATED:
        return None
    if spec.signal == DONE_NEVER:
        return False
    if spec.signal == DONE_VERIFY_COMMAND:
        return await _verify_command_signal(loop, findings, spec)
    return await _judge_assessment_signal(loop, findings, spec)


async def _verify_command_signal(
    loop: Loop, findings: list[dict], spec: ConvergenceSpec
) -> bool | None:
    """The deterministic mechanism: RUN the declared command and read its exit code.

    An unset command yields ``None`` (``run_verify_command`` returns the can't-tell tristate for
    an empty command), which is how a General loop with no check defers to budget by design.
    """
    from gideon.automation.loop.gates import run_verify_command

    ok = await run_verify_command(
        _command(loop, spec), loop.workspace_dir or None, label="verify"
    )
    if ok is not True:
        return ok
    criteria = _criteria(loop, spec)
    if len(criteria) <= 1:
        return True
    return await _all_criteria_met(loop, criteria, findings)


async def _all_criteria_met(
    loop: Loop, criteria: list[str], findings: list[dict]
) -> bool | None:
    """A strict judge over a verifiable loop's criteria: PASS only if the evidence from completed
    cycles shows EVERY criterion is met. Guards against a green command on a partial build.

    Returns True (all met), False (>=1 unmet → keep going), or None (judge unavailable → defer;
    the watchdog still bounds by budget). Conservative: any ambiguity is NOT a pass.
    """
    if not findings:
        return None
    from gideon.automation.loop.gates import (
        judge_verdict,
        verdict_is_pass,
        verdict_rendered,
    )
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    recent = findings[-6:]
    evidence = "\n".join(
        f"- cycle {f.get('cycle')}: {str(f.get('summary', '') or f.get('key_insight', ''))[:300]}"
        for f in recent
    )
    criteria_block = "\n".join(f"- {s}" for s in criteria)
    prompt = render_use_case_prompt(
        "subgoal_judge",
        {"task": loop.task, "criteria": criteria_block, "evidence": evidence},
    )
    if not prompt:
        return None
    raw = await judge_verdict(prompt)
    if verdict_is_pass(raw):
        return True
    return False if verdict_rendered(raw) else None


async def _judge_assessment_signal(
    loop: Loop, findings: list[dict], spec: ConvergenceSpec
) -> bool | None:
    """A SEPARATE judge subagent (never the worker) scores the latest cycle's done-ness + marginal
    value; the deterministic granularity dial decides returns-exhaustion. The judge advises; the
    supervisor (watchdog) decides.

    Returns True (done / returns-exhausted), False (keep going), or None (defer — a judge failure
    is observable-but-not-a-clean-False so the watchdog can surface degradation).
    """
    if not findings:
        return None
    from gideon.automation.loop import instrument
    from gideon.automation.loop import judge as judge_mod
    from gideon.automation.loop import store
    from gideon.automation.loop.granularity import returns_exhausted_calibrated

    finding = findings[-1]
    cfg0 = _cfg(loop)
    if cfg0.get("judge_calibrated") is False:
        return None
    if "judge_calibrated" not in cfg0:

        async def _probe_assess(goal, dod, fnd, prior):
            return await judge_mod.assess_cycle(goal, dod, fnd, prior)

        trustworthy = await instrument.probe_judge(_probe_assess)
        if trustworthy is not None:
            store.set_kind_config_key(loop.id, "judge_calibrated", bool(trustworthy))
            if trustworthy is False:
                return (
                    None  # blind → defer; watchdog surfaces judge_blind + NEEDS_INPUT
                )
    cycle = int(finding.get("cycle", len(findings)))
    cfg = cfg0
    deliverables = [
        str(d).strip() for d in (cfg.get("deliverables", []) or []) if str(d).strip()
    ]
    from gideon.automation.loop.loop import effective_dir

    _loop_dir = loop_files.safe_loop_dir(loop.id)
    fallback_dirs = [str(_loop_dir)] if _loop_dir is not None else []
    primary = str(cfg.get("primary_deliverable", "") or "").strip()
    canonical = primary or spec.ground_truth_deliverable
    gt_deliverables = deliverables or ([canonical] if canonical else [])
    verify_command = _command(loop, spec)
    try:
        verdict = await judge_mod.assess_cycle(
            loop.task,
            loop.success_criteria or "",
            finding,
            findings[:-1],
            verify_command=verify_command,
            workspace=effective_dir(loop) or None,
            deliverables=gt_deliverables,
            fallback_dirs=fallback_dirs,
        )
    except Exception:
        verdict = None
    if verdict is None:
        return None
    if verdict.done or verdict.regressed:
        try:
            skeptic = await judge_mod.assess_cycle_skeptic(
                loop.task,
                loop.success_criteria or "",
                finding,
                findings[:-1],
                verify_command=verify_command,
                workspace=effective_dir(loop) or None,
                deliverables=gt_deliverables,
                fallback_dirs=fallback_dirs,
            )
        except Exception:
            skeptic = None
        from gideon.automation.workflows.judge_contract import adjudicate

        verdict = adjudicate(verdict, skeptic)
    trail = store.record_marginal_score(loop.id, verdict.marginal_value)
    store.record_quality_score(loop.id, verdict.quality_score)
    granularity = str(cfg.get("granularity", "balanced"))
    from gideon.automation.loop.granularity import calibrated_band, dial_for

    _setting = dial_for(granularity)
    if _setting is not None:
        verdict.band_used = calibrated_band(trail, _setting.threshold)
    loop_files.write_verdict(loop.id, cycle, {"cycle": cycle, **verdict.to_dict()})
    if verdict.done:
        return True
    if returns_exhausted_calibrated(trail, granularity):
        return True
    return False


def has_done_check(loop: Loop, policy: SupervisorPolicy) -> bool:
    """Whether this loop HAS a point-in-time done-check at all.

    ``None`` from :func:`done_signal` has two meanings, and only one is a degradation: (a) a loop
    that HAS a check genuinely couldn't assess (judge errored / command un-runnable) → surface it;
    (b) a loop that has NO such check for its config (a General loop with no command) → deferring
    to budget BY DESIGN, not a failure. Only (a) may raise "done-ness check unavailable".
    """
    spec = policy.convergence
    if spec.signal == DONE_ORCHESTRATED:
        return False
    if spec.signal == DONE_VERIFY_COMMAND and spec.done_check_optional:
        return bool(_command(loop, spec).strip())
    return True


def budget_stop_is_genuine(policy: SupervisorPolicy) -> bool:
    """Whether reaching the cycle budget is a CLEAN completion rather than the error-flavoured
    "stopped before the goal was met". True where the budget IS the intended stopping condition
    (a monitor's watch window), so the cockpit shows a clean completion for an inherently-ongoing
    loop that ran its course."""
    return bool(policy.convergence.budget_stop_is_genuine)


def stagnation_enabled(policy: SupervisorPolicy) -> bool:
    """Whether the stall signal applies. A monitor goal's quiet cycle is a valid no-op."""
    return bool(policy.convergence.stagnation_enabled)
