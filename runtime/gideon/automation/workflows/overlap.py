"""Overlap start decisions and serialized recovery of durable queued drafts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from gideon.automation.workflows import store
from gideon.automation.workflows.models import OverlapPolicy, RunStatus, WorkflowRun
from gideon.core.concurrency import single_flight

logger = logging.getLogger(__name__)

QUEUED_KEY = "overlap_queued"

QUEUED_AT_KEY = "overlap_queued_at"

MAX_QUEUE_DEPTH = 1

_DRAFT_SCAN_LIMIT = 500


class OverlapAction(str, Enum):
    """What a start DOES, once the policy has been applied. One member per outcome a"""

    START = "start"
    SKIP = "skip"
    CANCEL_THEN_START = "cancel_then_start"
    QUEUE = "queue"
    DROP = "drop"


def decide(policy: OverlapPolicy, *, active: int, queued: int) -> OverlapAction:
    choices = (
        (
            OverlapPolicy.SKIP,
            lambda: OverlapAction.SKIP if active else OverlapAction.START,
        ),
        (OverlapPolicy.CANCEL_PREVIOUS, lambda: OverlapAction.CANCEL_THEN_START),
        (OverlapPolicy.QUEUE, lambda: _queue_action(active, queued)),
    )
    for declared, action in choices:
        if policy == declared:
            return action()
    raise AssertionError(
        f"no branch for OverlapPolicy.{getattr(policy, 'name', policy)} — a new member must "
        "declare its own behaviour here rather than inherit another policy's"
    )


def queued_extra() -> dict[str, Any]:
    """The marker block for a run created by a `queue` decision."""
    return {QUEUED_KEY: True, QUEUED_AT_KEY: datetime.now(timezone.utc).isoformat()}


def is_queued(run: WorkflowRun) -> bool:
    """True for a run the overlap policy queued. False for every hand-made draft."""
    extra = getattr(run, "extra", None)
    return bool(isinstance(extra, dict) and extra.get(QUEUED_KEY))


def queued_runs(workflow_name: str) -> list[WorkflowRun]:
    return _StartQueue(workflow_name).pending() if workflow_name else []


def queued_depth(workflow_name: str) -> int:
    return len(queued_runs(workflow_name))


def queued_names() -> list[str]:
    rows, _ = store.list_runs(status=RunStatus.DRAFT, limit=_DRAFT_SCAN_LIMIT)
    names = dict.fromkeys(
        run.workflow_name for run in rows if is_queued(run) and run.workflow_name
    )
    return list(names)


async def drain(workflow_name: str, supervisor: Any) -> str | None:
    if not workflow_name or supervisor is None:
        return None
    queue = _StartQueue(workflow_name)
    with single_flight(f"workflow-overlap-drain:{workflow_name}") as acquired:
        if acquired:
            return await queue.launch_next(supervisor)
        logger.debug("overlap drain for %s already in flight elsewhere", workflow_name)
    return None


async def drain_all(supervisor: Any) -> list[str]:
    async def launch(name: str) -> str | None:
        try:
            return await drain(name, supervisor)
        except Exception:
            logger.debug("overlap drain failed for %s", name, exc_info=True)
            return None

    results = []
    for name in queued_names():
        run_id = await launch(name)
        if run_id:
            results.append(run_id)
    return results


def _queue_action(active: int, queued: int) -> OverlapAction:
    if not (active or queued):
        return OverlapAction.START
    return OverlapAction.DROP if queued >= MAX_QUEUE_DEPTH else OverlapAction.QUEUE


class _StartQueue:
    def __init__(self, name: str):
        self.name = name

    def pending(self) -> list[WorkflowRun]:
        rows, _ = store.list_runs(
            workflow_name=self.name, status=RunStatus.DRAFT, limit=_DRAFT_SCAN_LIMIT
        )
        return sorted(filter(is_queued, rows), key=lambda run: (run.created_at, run.id))

    def candidate(self, supervisor: Any) -> WorkflowRun | None:
        pending = self.pending()
        if not pending:
            return None
        controller = getattr(supervisor, "controller", None)
        checks = (
            lambda: callable(controller)
            and any(controller(run.id) is not None for run in pending),
            lambda: any(run.workflow_name == self.name for run in store.active_runs()),
        )
        if any(check() for check in checks):
            return None
        return pending[0]

    async def launch_next(self, supervisor: Any) -> str | None:
        run = self.candidate(supervisor)
        if run is None:
            return None
        spec = store.read_spec(run.id)
        if isinstance(spec, dict) and spec.get("root"):
            try:
                await supervisor.launch(run, spec)
            except Exception:
                logger.exception("overlap drain could not launch queued run %s", run.id)
                return None
            logger.info("overlap drain launched queued run %s (%s)", run.id, self.name)
            return run.id
        logger.warning("queued run %s has no readable spec; failing it", run.id)
        run.status = RunStatus.FAILED
        run.error_message = "queued run could not be started: its spec is missing"
        store.save(run)
        return None
