"""Native workflow ownership and cancellation materialization."""
import asyncio
import pytest

from gideon.automation.workflows import store, journal
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import WorkflowRun, RunStatus, InstanceState, NodeInstance
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.integrations import mcp_workflows

@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)


def persisted(spec):
    run = store.create(WorkflowRun(id="", workflow_name=spec['name']))
    store.write_spec(run.id, spec)
    return run


def test_worker_tool_launch_lives_on_supervisor_loop(monkeypatch):
    async def run_test():
        spec = {'name': 'home-loop', 'root': {'kind': 'transform', 'id': 'value', 'config': {'expr': '1'}}}
        run = persisted(spec)
        watchdog = WorkflowWatchdog(services=EngineServices())
        watchdog.start()
        monkeypatch.setattr(mcp_workflows, '_supervisor', lambda: watchdog)
        controller = await asyncio.to_thread(mcp_workflows._on_engine, lambda owner: owner.launch(run, spec))
        assert controller._task.get_loop() is asyncio.get_running_loop()
        assert await controller.run_to_completion(timeout=10) == RunStatus.COMPLETE
        assert store.get(run.id).status == RunStatus.COMPLETE
        with pytest.raises(RuntimeError, match='must be awaited'):
            watchdog.run_threadsafe(lambda: asyncio.sleep(0))
        await watchdog.stop()
    asyncio.run(run_test())


def test_resumed_controller_requeues_lost_attempt_without_new_epoch():
    async def run_test():
        spec = {'name': 'lost-attempt', 'root': {'kind': 'transform', 'id': 'value', 'config': {'expr': '2'}}}
        run = persisted(spec)
        run.started_at = '2026-10-06T12:00:00Z'
        run.status = RunStatus.RUNNING
        store.save(run)
        store.write_state(run.id, {'root': NodeInstance(path='root', state=InstanceState.RUNNING, attempt=1, epoch=3)})
        controller = RunController(store.get(run.id), spec)
        assert await controller.run_to_completion(timeout=10) == RunStatus.COMPLETE
        assert controller.instances['root'].epoch == 3
        assert controller.instances['root'].attempt == 1
    asyncio.run(run_test())


def test_cancelled_actual_stage_reaches_ledger_without_false_failure():
    async def run_test():
        started = asyncio.Event()
        ended = asyncio.Event()
        async def completion(prompt, *, use_case='background', output_type=None, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        spec = {'name': 'cancel-stage', 'root': {'kind': 'infer', 'id': 'work', 'config': {'prompt': 'Wait'}}}
        run = persisted(spec)
        controller = RunController(run, spec, services=EngineServices(completion=completion))
        await controller.start()
        await asyncio.wait_for(started.wait(), 10)
        controller.request_cancel()
        controller.wake()
        assert await controller.run_to_completion(timeout=10) == RunStatus.CANCELLED
        assert ended.is_set()
        records = [e for e in journal.ledger(run.id) if e.get('kind') == journal.STEP_CANCELLED]
        assert len(records) == 1
        totals = journal.run_totals(run.id)
        assert totals['steps_cancelled'] == 1
        assert totals['steps_completed'] == totals['steps_failed'] == 0
        assert store.read_state(run.id)['root'].state == InstanceState.CANCELLED
    asyncio.run(run_test())


def test_cancelled_usage_is_persisted_and_folded_neutrally():
    from gideon.automation.workflows.step_usage import StepUsage
    spec = {'name': 'usage', 'root': {'kind': 'transform', 'config': {'expr': '1'}}}
    run = persisted(spec)
    journal.Journal(run.id).step_cancelled('root', 'value', epoch=2, attempt=1, usage=StepUsage(tokens=13, cost_usd=0.2))
    assert [e for e in journal.ledger(run.id) if e.get('kind') == journal.STEP_CANCELLED][0]['tokens'] == 13
    totals = journal.run_totals(run.id)
    assert totals['tokens'] == 13
    assert totals['steps_cancelled'] == 1
    assert totals['steps_completed'] == totals['steps_failed'] == 0


def test_dead_controller_is_replaced_by_real_watchdog():
    async def run_test():
        spec = {'name': 'adopt-dead', 'root': {'kind': 'transform', 'id': 'value', 'config': {'expr': '3'}}}
        run = persisted(spec)
        run.status = RunStatus.RUNNING
        run.started_at = '2026-10-06T12:00:00Z'
        store.save(run)
        store.write_state(run.id, {'root': NodeInstance(path='root', state=InstanceState.RUNNING, attempt=1)})
        old = RunController(store.get(run.id), spec)
        old._task = asyncio.create_task(asyncio.sleep(60))
        old._task.cancel()
        await asyncio.gather(old._task, return_exceptions=True)
        assert old.dead
        watchdog = WorkflowWatchdog()
        watchdog.register(old)
        assert watchdog.controller(run.id) is None
        replacement = await watchdog.launch(store.get(run.id), spec)
        assert replacement is not old
        assert await replacement.run_to_completion(timeout=10) == RunStatus.COMPLETE
        await watchdog.stop()
    asyncio.run(run_test())
