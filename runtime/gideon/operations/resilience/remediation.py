"""Health-scored self-remediation engine (PLATFORM-RESILIENCE §4).

ONE background engine that replaces N independent maintenance crons, shaped on
GBrain's ``doctor --remediate --target-score --max-usd``: compute a health score
from **measured** deficits, build a dependency-ordered plan, execute step-by-step
re-checking the score after each step, and stop at whichever comes first —
``target_score`` reached, ``max_cost_usd`` spent, or plan exhausted.

Design tenets (from the plan's risk table): this is a **plan-executor over declared
jobs, not a policy brain**. Deficit inputs are measured counts, ordering is declared
``after:`` edges, stopping is three plain caps, and per-job cooldowns are the "dumb
cooldown".

**SOLE ownership of periodic maintenance (§4.4, PR2-11 then PR2-8).** This engine owns the
maintenance it absorbed — memory FTS reconciliation, the daily history and SEL prunes,
skill-library aging, inbox retention — and it is the only implementation of each: the heartbeat's duplicate
per-tick copies were deleted with the engine's re-homing, so there is no second cadence to
fall back to and none to drift from. ``resilience.remediation.enabled=false`` therefore means
what "disabled" means for every other automation: the pass does not run, and every job stays
callable on demand through ``POST /api/doctor/remediation/run``. That is criterion #6 ("the
old heartbeat maintenance no longer runs independently") at its literal strength.

**How it is driven (§4.3, PR2-8).** As ONE adaptive-clock trigger, ``system:self-remediation``,
``created_by: system``, visible and editable on the Triggers page like any other automation —
NOT from a private scheduler inside the heartbeat loop. ``action_providers/remediation_provider``
is the seam; this module knows nothing about triggers and is still callable directly (the Doctor
panel's Run-now does exactly that).

Cadence note: absorbed maintenance is now **deficit-driven, not clock-driven**. A job runs
when its measured backlog drops the health score below ``target_score``, so the deficits
below are weighted such that a *material* backlog crosses the default gate
(100 − 90 = 10 penalty points) rather than waiting for a fixed tick.

Cost: deterministic jobs (FTS rebuild, faiss re-index, prune) cost $0 and never block
on budget. Judgment jobs (re-extraction, semantic lint) would run through
``one_shot_completion`` under the SpendMeter — none are registered yet (their inputs
are future flywheel/knowledge infra); the two-lane mechanism is here for them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.operations.resilience.grammar import count_noun


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_LEDGER_FILE = "remediation.jsonl"
_JOBS_FILE = "jobs.json"
_LEDGER_CAP = 500

_DEFAULT_TARGET_SCORE = 90.0
_MIN_SCHEDULABLE_PENALTY = 100.0 - _DEFAULT_TARGET_SCORE

HEALTHY_SCORE = 95.0


def _doctor_dir() -> Path:
    return config_dir() / "doctor"


@dataclass(frozen=True)
class Deficit:
    """One measured health deficit.

    ``count`` is the current problem magnitude (0 = healthy). ``weight`` is the score
    penalty per unit, capped so one noisy source can't dominate. ``max_penalty`` is
    the ceiling this deficit can subtract (GBrain's ``max_reachable_score`` inverse):
    when the deficit is unfixable right now (e.g. no embedder bound), the caller sets
    ``reachable=False`` so the engine never burns budget on futile work.
    """

    key: str
    count: int
    weight: float
    max_penalty: float
    reachable: bool = True
    job_id: str = ""

    @property
    def penalty(self) -> float:
        return min(self.max_penalty, self.count * self.weight)


def measure_deficits() -> list[Deficit]:
    """Measure every deficit source that has a REAL count today. Read-only and
    exception-safe — a source that can't be read contributes nothing (never a guess).

    Sources with no count function yet (FTS desync, failed-run backlog, LEARN-R19
    staging) are deliberately absent — the plan forbids guessing.
    """
    out: list[Deficit] = []

    try:
        from gideon.cognition.knowledge import get_knowledge_store
        from gideon.extensions.providers.provider_bridge import can_resolve_use_case

        missing = int(get_knowledge_store().count_items_missing_embedding())
        out.append(
            Deficit(
                key="knowledge_missing_embeddings",
                count=missing,
                weight=0.5,
                max_penalty=20.0,
                reachable=can_resolve_use_case("embedding"),
                job_id="knowledge.reindex-embeddings",
            )
        )
    except Exception:
        logger.debug("deficit: knowledge embeddings measure failed", exc_info=True)

    try:
        from gideon.operations.resilience.fixes import _dead_locks

        out.append(
            Deficit(
                key="orphan_locks",
                count=len(_dead_locks()),
                weight=2.0,
                max_penalty=12.0,
                job_id="serving-fs.prune-orphans",
            )
        )
    except Exception:
        logger.debug("deficit: orphan-lock measure failed", exc_info=True)

    try:
        from gideon.extensions.skills.curator import run_aging

        due = int(getattr(run_aging(dry_run=True), "changed", 0))
        out.append(
            Deficit(
                key="skill_aging_due",
                count=due,
                weight=1.0,
                max_penalty=12.0,
                job_id="skills.age",
            )
        )
    except Exception:
        logger.debug("deficit: skill-aging measure failed", exc_info=True)

    try:
        from gideon.cognition.memory import MemoryJournal

        out.append(
            Deficit(
                key="memory_fts_desync",
                count=int(MemoryJournal().fts_desync_count()),
                weight=11.0,
                max_penalty=22.0,
                job_id="memory.rebuild-fts",
            )
        )
    except Exception:
        logger.debug("deficit: memory FTS desync measure failed", exc_info=True)

    try:
        from gideon.cognition.memory import MemoryJournal
        from gideon.core.config.loader import AppConfig

        keep_days = int(AppConfig.load().memory.history_max_days)
        out.append(
            Deficit(
                key="history_over_retention",
                count=int(MemoryJournal().count_history_over_retention(keep_days)),
                weight=11.0,
                max_penalty=15.0,
                job_id="memory.prune-history",
            )
        )
    except Exception:
        logger.debug("deficit: history retention measure failed", exc_info=True)

    try:
        from gideon.integrations.inbox_service import maintenance_backlog

        out.append(
            Deficit(
                key="inbox_maintenance_backlog",
                count=int(maintenance_backlog()),
                weight=1.0,
                max_penalty=12.0,
                job_id="inbox.maintenance",
            )
        )
    except Exception:
        logger.debug("deficit: inbox maintenance measure failed", exc_info=True)

    try:
        from gideon.security.sel import sel

        out.append(
            Deficit(
                key="sel_prunable_entries",
                count=int(sel().count_prunable()),
                weight=0.05,
                max_penalty=15.0,
                job_id="sel.prune",
            )
        )
    except Exception:
        logger.debug("deficit: SEL prune measure failed", exc_info=True)

    try:
        out.append(
            Deficit(
                key="skills_tampered",
                count=_count_tampered_skills(),
                weight=5.0,
                max_penalty=20.0,
                reachable=False,
            )
        )
    except Exception:
        logger.debug("deficit: skill-integrity measure failed", exc_info=True)

    return out


def _count_tampered_skills() -> int:
    """Installed skills whose on-disk hashes diverge from their install-time lock.

    Only *locked* skills are hashed — ``verify_skill_integrity`` returns ``unlocked``
    before reading any file — so bundled/hand-placed skills cost a single stat.
    """
    from gideon.engine.agent import _all_skill_paths
    from gideon.extensions.skills.marketplace import (
        _SKILL_FILENAME,
        verify_skill_integrity,
    )

    tampered = 0
    seen: set[str] = set()
    for base_str in _all_skill_paths():
        base = Path(base_str)
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or entry.name in seen:
                continue
            if not (entry / _SKILL_FILENAME).is_file():
                continue
            seen.add(entry.name)
            rep = verify_skill_integrity(entry)
            if not rep.unlocked and not rep.ok:
                tampered += 1
    return tampered


def health_score(deficits: list[Deficit]) -> float:
    """``100 − Σ penalties`` over REACHABLE deficits (an unreachable deficit is at its
    floor — it doesn't count against a score the engine can't improve). Clamped 0-100.
    """
    total = sum(d.penalty for d in deficits if d.reachable)
    return max(0.0, min(100.0, 100.0 - total))


@dataclass(frozen=True)
class RemediationJob:
    """A remediation step. ``run()`` returns a result string (deterministic jobs cost
    $0). ``after`` are job ids that must run first (dependency ordering). ``cooldown_hours``
    + success-only timestamps + ``content_hash`` are the storm guard."""

    id: str
    title: str
    run: Callable[[], str]
    lane: str = "deterministic"
    after: tuple[str, ...] = ()
    cooldown_hours: float = 0.0
    fixes_deficit: str = ""


_JOBS: dict[str, RemediationJob] = {}


def register_job(job: RemediationJob) -> None:
    _JOBS[job.id] = job


def all_jobs() -> list[RemediationJob]:
    return list(_JOBS.values())


def _ordered(jobs: list[RemediationJob]) -> list[RemediationJob]:
    """Topological order by ``after`` edges (stable; a missing/ cyclic edge degrades to
    insertion order rather than raising)."""
    by_id = {j.id: j for j in jobs}
    done: set[str] = set()
    out: list[RemediationJob] = []

    def visit(job: RemediationJob, stack: set[str]) -> None:
        if job.id in done or job.id in stack:
            return
        stack.add(job.id)
        for dep in job.after:
            if dep in by_id:
                visit(by_id[dep], stack)
        stack.discard(job.id)
        if job.id not in done:
            done.add(job.id)
            out.append(job)

    for j in jobs:
        visit(j, set())
    return out


def _jobs_state_path() -> Path:
    return _doctor_dir() / _JOBS_FILE


def _load_job_state() -> dict[str, dict]:
    p = _jobs_state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_job_state(state: dict[str, dict]) -> None:
    d = _doctor_dir()
    d.mkdir(parents=True, exist_ok=True)
    atomic_write(_jobs_state_path(), json.dumps(state, indent=2))


def _in_cooldown(job: RemediationJob, state: dict[str, dict], *, now: float) -> bool:
    if job.cooldown_hours <= 0:
        return False
    last = state.get(job.id, {}).get("last_success_ts")
    return isinstance(last, (int, float)) and (now - last) < job.cooldown_hours * 3600.0


@dataclass
class RunResult:
    score_before: float
    score_after: float
    jobs: list[dict] = field(default_factory=list)
    stopped_reason: str = ""


def run_remediation(
    *,
    target_score: float = 90.0,
    max_cost_usd: float = 1.0,
    now: float,
    dry_run: bool = False,
) -> RunResult:
    """Execute a dependency-ordered remediation plan under the caps. Re-measures the
    score after each step, stops at target/cost/exhausted. Charges the guardrails
    SpendMeter under run_key ``doctor`` for judgment jobs; deterministic jobs are free.

    ``dry_run`` computes the plan + score without running any job (the Doctor preview).
    """
    from gideon.security.guardrails.budgets import (
        get_meter,
        reset_current_run_key,
        set_current_run_key,
    )

    deficits = measure_deficits()
    score_before = health_score(deficits)
    result = RunResult(score_before=score_before, score_after=score_before)

    if score_before >= target_score:
        result.stopped_reason = "target_score already met"
        return result

    state = _load_job_state()
    meter = get_meter()
    candidates = [
        j
        for j in all_jobs()
        if j.fixes_deficit
        and any(
            d.key == j.fixes_deficit and d.reachable and d.count > 0 for d in deficits
        )
    ]
    plan = _ordered(candidates)

    for job in plan:
        if job.lane == "judgment":
            spent = meter.run_totals("doctor").dollars
            if spent >= max_cost_usd:
                result.stopped_reason = f"max_cost_usd ${max_cost_usd} reached"
                result.jobs.append(
                    {"id": job.id, "status": "skipped_budget", "cost": 0.0}
                )
                continue
        if _in_cooldown(job, state, now=now):
            result.jobs.append(
                {"id": job.id, "status": "skipped_cooldown", "cost": 0.0}
            )
            continue
        if dry_run:
            result.jobs.append({"id": job.id, "status": "would_run", "cost": 0.0})
            continue
        try:
            token = set_current_run_key("doctor")
            try:
                detail = job.run()
            finally:
                reset_current_run_key(token)
            state.setdefault(job.id, {})["last_success_ts"] = now
            result.jobs.append(
                {"id": job.id, "status": "ok", "cost": 0.0, "detail": detail[:200]}
            )
        except Exception as exc:
            logger.warning("remediation job %s failed", job.id, exc_info=True)
            result.jobs.append(
                {"id": job.id, "status": "error", "cost": 0.0, "error": str(exc)[:200]}
            )
            continue
        result.score_after = health_score(measure_deficits())
        if result.score_after >= target_score:
            result.stopped_reason = "target_score reached"
            break

    if not result.stopped_reason:
        result.stopped_reason = "plan exhausted"
    if not dry_run:
        _save_job_state(state)
        _write_ledger(result, now=now)
    else:
        result.score_after = score_before
    return result


def _write_ledger(result: RunResult, *, now: float) -> None:
    """Append one ledger row (trim at 2× cap, atomic-write rewrite — the audit.jsonl
    pattern)."""
    try:
        d = _doctor_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / _LEDGER_FILE
        row = {
            "ts": now,
            "score_before": round(result.score_before, 1),
            "score_after": round(result.score_after, 1),
            "jobs": result.jobs,
            "stopped_reason": result.stopped_reason,
        }
        existing = (
            path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        )
        existing.append(json.dumps(row))
        if len(existing) > _LEDGER_CAP * 2:
            existing = existing[-_LEDGER_CAP:]
        atomic_write(path, "\n".join(existing) + "\n")
    except Exception:
        logger.debug("remediation ledger write failed", exc_info=True)


def recent_runs(limit: int = 10) -> list[dict]:
    """Read recent remediation ledger rows newest-first (the Doctor rendering)."""
    path = _doctor_dir() / _LEDGER_FILE
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict] = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _job_prune_orphans() -> str:
    from gideon.operations.resilience.fixes import _orphan_prune_apply

    return _orphan_prune_apply()


def _job_age_skills() -> str:
    from gideon.extensions.skills.curator import run_aging

    report = run_aging()
    return (
        getattr(report, "summary", lambda: "aged")()
        if report.changed
        else "no skills due"
    )


def _job_reindex_embeddings() -> str:
    from gideon.cognition.knowledge import get_knowledge_embedder, get_knowledge_store

    embedder = get_knowledge_embedder()
    if embedder is None:
        return "no embedder bound — skipped"
    store = get_knowledge_store()
    report = store.reembed_all(embedder, only_missing=True)
    reembedded = int(report.get("reembedded") or 0)
    failed = int(report.get("failed") or 0)
    if failed and not reembedded:
        raise RuntimeError(
            f"embedded none of the {count_noun(failed, 'item')} missing a vector"
        )
    if failed:
        return (
            f"re-embedded {count_noun(reembedded, 'item')}; "
            f"{failed} still without a vector"
        )
    return f"re-embedded {count_noun(reembedded, 'item')}"


def _job_rebuild_memory_fts() -> str:
    from gideon.cognition.memory import MemoryJournal

    rebuilt = MemoryJournal().rebuild_index()
    return f"FTS index rebuilt: {count_noun(rebuilt, 'file')}"


def _job_prune_history() -> str:
    from gideon.cognition.memory import MemoryJournal
    from gideon.core.config.loader import AppConfig

    keep_days = int(AppConfig.load().memory.history_max_days)
    pruned = MemoryJournal().prune_history(keep_days=keep_days)
    return f"pruned {count_noun(pruned, 'history file')}"


def _job_inbox_maintenance() -> str:
    from gideon.integrations.inbox_service import run_live_maintenance

    return run_live_maintenance()


def _job_prune_sel() -> str:
    from gideon.security.sel import sel

    return f"pruned {count_noun(sel().prune(), 'security-event entry', 'security-event entries')}"


def _register_builtin_jobs() -> None:
    register_job(
        RemediationJob(
            id="serving-fs.prune-orphans",
            title="Prune orphaned locks + rollback leftovers",
            run=_job_prune_orphans,
            lane="deterministic",
            cooldown_hours=24.0,
            fixes_deficit="orphan_locks",
        )
    )
    register_job(
        RemediationJob(
            id="skills.age",
            title="Age the skill library (active→stale→archived)",
            run=_job_age_skills,
            lane="deterministic",
            cooldown_hours=24.0,
            fixes_deficit="skill_aging_due",
        )
    )
    register_job(
        RemediationJob(
            id="knowledge.reindex-embeddings",
            title="Backfill missing knowledge embeddings",
            run=_job_reindex_embeddings,
            lane="deterministic",
            after=("serving-fs.prune-orphans",),
            cooldown_hours=6.0,
            fixes_deficit="knowledge_missing_embeddings",
        )
    )
    register_job(
        RemediationJob(
            id="memory.prune-history",
            title="Prune daily history past its retention window",
            run=_job_prune_history,
            lane="deterministic",
            cooldown_hours=12.0,
            fixes_deficit="history_over_retention",
        )
    )
    register_job(
        RemediationJob(
            id="memory.rebuild-fts",
            title="Reconcile the memory full-text index with disk",
            run=_job_rebuild_memory_fts,
            lane="deterministic",
            after=("memory.prune-history",),
            cooldown_hours=0.25,
            fixes_deficit="memory_fts_desync",
        )
    )
    register_job(
        RemediationJob(
            id="inbox.maintenance",
            title="Prune the inbox (retention + dismissed set)",
            run=_job_inbox_maintenance,
            lane="deterministic",
            cooldown_hours=6.0,
            fixes_deficit="inbox_maintenance_backlog",
        )
    )
    register_job(
        RemediationJob(
            id="sel.prune",
            title="Prune the security event log (retention + size cap)",
            run=_job_prune_sel,
            lane="deterministic",
            cooldown_hours=12.0,
            fixes_deficit="sel_prunable_entries",
        )
    )


_register_builtin_jobs()
