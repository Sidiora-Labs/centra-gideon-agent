"""Observe native work completion without taking ownership of its execution."""

import asyncio
import json

from gideon.integrations.action_providers.base import ActionResult


async def agent_result(manager, info):
    while not info.done:
        await asyncio.sleep(0.2)
        current = manager.get(info.id)
        if current is None:
            return ActionResult(False, error="agent completion record disappeared", work_id=info.id)
        info = current
    if info.cancelled:
        return ActionResult(False, error=info.error or "agent cancelled", outcome="interrupted", work_id=info.id)
    return ActionResult(not bool(info.error), error=info.error, stdout=info.result, work_id=info.id)


def agent_launch(manager, info, label):
    if info is None:
        return ActionResult(False, error="agent admission returned no work")
    if info.done and (info.error or info.cancelled):
        return ActionResult(False, error=info.error or "agent cancelled", outcome="interrupted" if info.cancelled else "", work_id=info.id)
    return ActionResult(True, stdout=label, outcome="queued" if info.queued else "launched", work_id=info.id, completion=lambda: agent_result(manager, info))


async def workflow_result(supervisor, run_id):
    owner = supervisor.event_loop
    if owner is not None and owner is not asyncio.get_running_loop():
        return await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(_workflow_result(supervisor, run_id), owner)
        )
    return await _workflow_result(supervisor, run_id)


async def _workflow_result(supervisor, run_id):
    from gideon.automation.workflows import store
    from gideon.automation.workflows.models import RunStatus

    while True:
        controller = supervisor.controller(run_id)
        if controller is not None:
            status = await controller.run_to_completion()
            run = controller.run
            break
        run = store.get(run_id)
        if run is None:
            return ActionResult(False, error="workflow completion record disappeared", work_id=run_id)
        status = run.status
        if status in {RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.DECLINED, RunStatus.ESCALATED}:
            break
        await asyncio.sleep(0.2)
    ok = status == RunStatus.COMPLETE
    return ActionResult(ok, outcome="interrupted" if status == RunStatus.CANCELLED else "", error="" if ok else f"workflow ended: {status.value}", stdout=json.dumps({"run_id": run_id, "status": status.value}), work_id=run_id)
