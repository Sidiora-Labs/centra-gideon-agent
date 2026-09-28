"""Project workflow runs launched as loops onto the shared loop wire shape."""

from __future__ import annotations

import calendar
import logging
import time
from typing import Any

from gideon.automation.loop.loop import LoopStatus, LoopStopReason
from gideon.automation.workflows import journal, store
from gideon.automation.workflows.models import Node, NodeKind, RunStatus, WorkflowRun

logger = logging.getLogger(__name__)

_STATUS: dict[RunStatus, LoopStatus] = {
    RunStatus.DRAFT: LoopStatus.READY,
    RunStatus.RUNNING: LoopStatus.RUNNING,
    RunStatus.PAUSED: LoopStatus.PAUSED,
    RunStatus.NEEDS_INPUT: LoopStatus.NEEDS_INPUT,
    RunStatus.COMPLETE: LoopStatus.COMPLETE,
    RunStatus.FAILED: LoopStatus.FAILED,
    RunStatus.CANCELLED: LoopStatus.STOPPED,
    RunStatus.DECLINED: LoopStatus.STOPPED,
    RunStatus.ESCALATED: LoopStatus.COMPLETE,
}

RUN_ACTION_SOURCE_STATES: dict[str, frozenset[LoopStatus]] = {
    "start": frozenset({LoopStatus.READY}),
    "pause": frozenset({LoopStatus.RUNNING}),
    "resume": frozenset({LoopStatus.PAUSED}),
    "stop": frozenset(
        {LoopStatus.READY, LoopStatus.RUNNING, LoopStatus.PAUSED, LoopStatus.NEEDS_INPUT}
    ),
}


def loop_status(run: WorkflowRun) -> LoopStatus:
    return _STATUS[run.status]


def _epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if not value:
        return value
    from gideon.security.security import redact_for_display

    return redact_for_display(str(value))


def _loop_root(spec: dict[str, Any] | None) -> tuple[str, Node] | None:
    if not isinstance(spec, dict):
        return None
    try:
        root = Node.from_dict(spec.get("root") or {})
    except (TypeError, ValueError):
        return None
    return ("root", root) if root.kind == NodeKind.LOOP else None


def _cycles_completed(run_id: str, path: str) -> int:
    try:
        rows = journal.ledger(run_id, kinds={journal.ITERATION})
    except Exception:
        logger.warning("loop view: iteration read failed for %s", run_id, exc_info=True)
        return 0
    return len({
        int(row["iteration"])
        for row in rows
        if row.get("instance_path") == path
        and isinstance(row.get("iteration"), int)
        and int(row["iteration"]) >= 0
    })


def run_loop_view(run: WorkflowRun) -> dict[str, Any]:
    extra = run.extra if isinstance(run.extra, dict) else {}
    spec = store.read_spec(run.id)
    root = _loop_root(spec)
    cap = int(run.policy_overrides.get("max_cycles") or 0)
    if not cap and root is not None:
        value = (root[1].config or {}).get("max_iterations")
        cap = value if isinstance(value, int) and value > 0 else 0
    inputs = run.inputs if isinstance(run.inputs, dict) else {}
    task = str(inputs.get("task") or "")
    reason = ""
    if run.status == RunStatus.COMPLETE:
        reason = LoopStopReason.DONE.value
    elif run.status in (RunStatus.CANCELLED, RunStatus.DECLINED):
        reason = LoopStopReason.USER.value
    elif run.status in (RunStatus.ESCALATED, RunStatus.FAILED):
        reason = LoopStopReason.WORKER_FAILED.value
        if run.status == RunStatus.ESCALATED and (run.attention or {}).get("reason") == "max_iterations":
            reason = LoopStopReason.CYCLE_BUDGET.value
    error = run.error_message or ""
    if not error and run.status == RunStatus.ESCALATED:
        error = str((run.attention or {}).get("detail") or (run.attention or {}).get("reason") or "")
    elapsed = max(0.0, float(run.elapsed_seconds or 0.0))
    if run.status == RunStatus.RUNNING and run.started_at:
        elapsed = max(elapsed, time.time() - (_epoch(run.started_at) or time.time()))
    return {
        "id": run.id,
        "run_id": run.id,
        "kind": str(extra.get("loop_kind") or ""),
        "name": _text(extra.get("loop_name") or task[:60] or run.workflow_name),
        "task": _text(task),
        "summary": _text(run.intent),
        "status": loop_status(run).value,
        "stop_reason": reason,
        "error_message": _text(error),
        "attended": bool(run.policy_overrides.get("attended", True)),
        "max_cycles": cap,
        "total_cycles": _cycles_completed(run.id, root[0]) if root else 0,
        "idle_secs": int(run.policy_overrides.get("idle_secs") or 0),
        "success_criteria": _text(inputs.get("exit_condition")) or None,
        "created_at": _epoch(run.created_at) or 0.0,
        "started_at": _epoch(run.started_at),
        "completed_at": _epoch(run.completed_at),
        "elapsed_seconds": elapsed,
        "project_id": run.project_id,
        "execution": "solo",
        "agent": "",
        "model": "",
        "session_key": "",
        "kind_config": {},
        "plan": [],
        "phase_tracked": False,
        "findings": [],
        "workflow_name": run.workflow_name,
    }


def list_loop_views(*, project_id: str = "", kind: str = "") -> list[dict[str, Any]]:
    result = []
    for run in store.list_loop_runs(project_id=project_id, kind=kind):
        try:
            result.append(run_loop_view(run))
        except Exception:
            logger.warning("loop view: could not project run %s", run.id, exc_info=True)
    return result


def get_loop_view(run_id: str) -> dict[str, Any] | None:
    run = store.get(run_id)
    if run is None or not isinstance(run.extra, dict) or not run.extra.get("loop_kind"):
        return None
    return run_loop_view(run)
