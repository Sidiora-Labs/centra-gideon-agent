"""Inspect active workflow records and apply explicitly requested orphan repairs."""

from __future__ import annotations

import calendar
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows import store
from gideon.automation.workflows.models import (
    TERMINAL_STATES,
    InstanceState,
    RunStatus,
    WorkflowRun,
)

logger = logging.getLogger(__name__)

STALE_RUNNING_SECS = 6 * 60 * 60

EXPIRED_WAIT_GRACE_SECS = 60


class Finding:
    """Finding kinds. Strings, so a UI and the ledger can render them without importing
    the enum."""

    STALE_RUNNING = "stale_running"
    DEAD_GATE = "dead_gate"
    EXPIRED_WAIT = "expired_wait"
    LOST_RUN = "lost_run"
    ORPHANED_JOURNAL = "orphaned_journal"
    MISSING_SPEC = "missing_spec"
    PENDING_CANCEL = "pending_cancel"


@dataclass
class AuditFinding:
    """One diagnosis. `healed` is set only when a repair actually ran."""

    kind: str
    run_id: str
    detail: str = ""
    instance_path: str = ""
    heal: str = ""
    healed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "detail": self.detail,
            "instance_path": self.instance_path,
            "heal": self.heal,
            "healed": self.healed,
        }


@dataclass
class AuditReport:
    findings: list[AuditFinding] = field(default_factory=list)
    runs_scanned: int = 0
    dry_run: bool = True

    @property
    def healthy(self) -> bool:
        return not self.findings

    def by_kind(self) -> dict[str, int]:
        from collections import Counter

        return dict(Counter(finding.kind for finding in self.findings))

    def to_dict(self) -> dict[str, Any]:
        projection: dict = dict(
            healthy=self.healthy, dry_run=self.dry_run, runs_scanned=self.runs_scanned
        )
        projection.update(
            counts=self.by_kind(),
            findings=list(map(lambda finding: finding.to_dict(), self.findings)),
        )
        return projection


def audit(
    *,
    dry_run: bool = True,
    supervisor: Any = None,
    now: float = 0.0,
) -> AuditReport:
    reading = now or time.time()
    result = AuditReport(dry_run=dry_run)
    for run in store.active_runs():
        result.runs_scanned += 1
        controlled = _has_live_controller(supervisor, run.id)
        _audit_run(
            run, result, clock=reading, dry_run=dry_run, has_controller=controlled
        )
    return result


def _has_live_controller(supervisor: Any, run_id: str) -> bool:
    lookup = None if supervisor is None else getattr(supervisor, "controller", None)
    if callable(lookup):
        try:
            return lookup(run_id) is not None
        except Exception:
            logger.debug("audit: supervisor controller lookup failed", exc_info=True)
    return False


def _audit_run(
    run: WorkflowRun,
    report: AuditReport,
    *,
    clock: float,
    dry_run: bool,
    has_controller: bool,
) -> None:
    _RunInspection(run, report, clock, dry_run, has_controller).inspect()


def _mark_protocol_violation(inst: Any) -> None:
    from gideon.automation.workflows.models import Failure, FailureClass

    inst.state = InstanceState.BLOCKED
    inst.completed_at = _now()
    attributes: dict = dict(
        failure_class=FailureClass.PROTOCOL,
        cause_plain="node stopped without reporting a structured completion",
        remediation="the backing subagent or command vanished; re-run this node, or inspect the gateway log for why it died",
        terminal_reason="protocol_violation",
    )
    inst.failure = Failure(**attributes)


def _finalize_from_instances(run: WorkflowRun, instances: dict[str, Any]) -> None:
    from gideon.automation.workflows.controller import _ROOT_TO_RUN
    from gideon.automation.workflows.tick import _worst

    states = list(map(lambda instance: instance.state, instances.values()))
    state = InstanceState.DONE if not states else _worst(states)
    _RunRepair(run).terminal(_ROOT_TO_RUN.get(state, RunStatus.COMPLETE))
    logger.info("workflow audit finalized lost run %s → %s", run.id, run.status.value)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(ts: str | None) -> float:
    """Parse a UTC `...Z` stamp to a real epoch.

    `calendar.timegm`, NOT `time.mktime`: mktime interprets the struct as LOCAL time, so
    parsing a UTC stamp shifts it by the machine's offset. Here that offset silently
    cancelled the measured age — a node stale for hours read as fresh, and the whole
    stale-running check was inert in exactly the timezone the servers run in.
    """
    if not ts:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0


class _RunRepair:
    def __init__(self, run: WorkflowRun):
        self.run = run

    def terminal(self, status: RunStatus, error: str | None = None) -> None:
        self.run.status = status
        if error is not None:
            self.run.error_message = error
        self.run.completed_at = self.run.completed_at or _now()
        store.save(self.run)

    def cancel(self) -> None:
        self.terminal(RunStatus.CANCELLED)
        store.clear_cancel(self.run.id)


class _RunInspection:
    def __init__(self, run, report, clock, dry_run, controlled):
        self.run, self.report, self.clock = run, report, clock
        self.controlled = controlled
        self.can_heal = not dry_run and not controlled
        self.repair = _RunRepair(run)

    def record(self, kind, detail, heal, *, path="", apply=None) -> bool:
        finding = AuditFinding(
            kind=kind, run_id=self.run.id, detail=detail, instance_path=path, heal=heal
        )
        if self.can_heal and apply is not None:
            apply()
            finding.healed = True
        self.report.findings.append(finding)
        return finding.healed

    def inspect(self) -> None:
        if store.read_spec(self.run.id) is None:
            self.record(
                Finding.MISSING_SPEC,
                "run spec is missing or unreadable; the run cannot be resumed",
                "mark the run failed",
                apply=lambda: self.repair.terminal(
                    RunStatus.FAILED, "run spec is missing or unreadable"
                ),
            )
            return
        if store.cancel_requested(self.run.id) and not self.controlled:
            self.record(
                Finding.PENDING_CANCEL,
                "a sticky cancel intent was never consumed (no live controller)",
                "finalize the run as cancelled",
                apply=self.repair.cancel,
            )
            return
        instances = store.read_state(self.run.id)
        dirty = False
        for path, instance in instances.items():
            changed = self.instance(path, instance)
            dirty = dirty or changed
        self.terminal(instances)
        if dirty:
            store.write_state(self.run.id, instances)

    def instance(self, path, instance) -> bool:
        if instance.state == InstanceState.RUNNING:
            elapsed = self.clock - _epoch(instance.started_at)
            if instance.started_at and elapsed > STALE_RUNNING_SECS:
                return self.record(
                    Finding.STALE_RUNNING,
                    f"node has been RUNNING for {int(elapsed // 3600)}h with no completion",
                    "transition to blocked{protocol_violation}",
                    path=path,
                    apply=lambda: _mark_protocol_violation(instance),
                )
        elif instance.state == InstanceState.WAITING:
            if instance.wake_at:
                if self.clock - instance.wake_at > EXPIRED_WAIT_GRACE_SECS:
                    return self.record(
                        Finding.EXPIRED_WAIT,
                        "a WAITING deadline passed with nothing scheduled to wake it",
                        "clear the deadline so the next tick resolves it",
                        path=path,
                        apply=lambda: self.wake(instance),
                    )
            elif self.run.status != RunStatus.NEEDS_INPUT:
                self.record(
                    Finding.DEAD_GATE,
                    "node parked on an unanswered gate but the run is not "
                    f"surfaced as needs_input (status={self.run.status.value})",
                    "surface the run as needs_input",
                    path=path,
                )
        return False

    @staticmethod
    def wake(instance) -> None:
        instance.wake_at = 0.0
        instance.state = InstanceState.PENDING

    def terminal(self, instances) -> None:
        if not instances or not all(
            instance.state in TERMINAL_STATES for instance in instances.values()
        ):
            return
        if self.controlled or self.run.status in (
            RunStatus.COMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.ESCALATED,
        ):
            return
        self.record(
            Finding.LOST_RUN,
            "every node is terminal but the run status was never written",
            "write the derived terminal status",
            apply=lambda: _finalize_from_instances(self.run, instances),
        )
