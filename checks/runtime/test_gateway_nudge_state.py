import asyncio
import logging
import time

from gideon.automation.loop.journal import LoopJournal
from gideon.automation.triggers.nudge import AutoNudgeService
from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.nudge_dispatch import (
    NudgeDispatch,
    NudgeLimits,
    NudgeTurn,
    SupervisorAssembly,
)
from gideon.engine.session import ConversationDirectory
from gideon.integrations.action_providers.services import (
    ActionServices,
    get_action_services,
    set_action_services,
)
from gideon.interfaces.dashboard.handlers.autonudge import render_nudge_message
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


def context(tmp_path):
    config = AppConfig()
    runtime = RuntimeCoordinator(config)
    runtime.sessions = ConversationDirectory(config)
    runtime.dashboard_state = ConsoleState(runtime.sessions, time.time())
    dispatch = NudgeDispatch(
        runtime,
        render=render_nudge_message,
        logger=logging.getLogger(__name__),
        limits=NudgeLimits(0, 0, 3, "Finish the finding"),
    )
    runtime.autonudge_svc = AutoNudgeService(
        base_dir=tmp_path / "nudges", on_fire=dispatch.fire
    )
    return runtime, dispatch


def test_actual_fire_registers_one_task_and_drops_busy_or_missing_targets(tmp_path):
    async def exercise():
        runtime, dispatch = context(tmp_path)
        session = _ChatSession("chat-1")
        state = runtime.dashboard_state
        state._sessions[session.key] = session
        loop = await runtime.autonudge_svc.add(session.key, "Continue")
        session.task = asyncio.current_task()
        assert await dispatch.fire(loop) is False
        assert not session.messages and session.queue_depth == 0
        session.task = None
        session._suppress_autonudge_rearm = True
        assert await dispatch.fire(loop) is False
        session._suppress_autonudge_rearm = False
        assert await dispatch.fire(loop) is True
        task = session.task
        assert session.running and task in state._background_tasks
        assert runtime._session_tasks[session.key] is task
        assert session.messages[-1]["role"] == "nudge"
        assert "[auto-nudge cycle 1]" in session.messages[-1]["content"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not session.running and task not in state._background_tasks
        del state._sessions[session.key]
        assert await dispatch.fire(loop) is False
        assert runtime.autonudge_svc.get_by_session(session.key) is None

    asyncio.run(exercise())


def test_real_expired_turn_marks_error_releases_suppression_and_reads_ledger(tmp_path):
    async def exercise():
        runtime, dispatch = context(tmp_path)
        session = _ChatSession("loop-a1b2c3d4-worker")
        session._app = "loop"
        turn = NudgeTurn(dispatch, runtime.dashboard_state, session, "Continue")
        assert turn.finding_count() == 0
        LoopJournal.open("a1b2c3d4").cycle(1, {"summary": "first finding"})
        assert turn.finding_count() == 1
        await turn.run()
        assert session._last_turn_errored
        assert not session._suppress_autonudge_rearm
        assert not session.running
        assert not session.messages

    asyncio.run(exercise())


def test_supervisor_instances_are_shared_by_actual_consumers(tmp_path):
    async def exercise():
        runtime, _ = context(tmp_path)
        runtime._cfg.workflows.enabled = True
        previous = get_action_services()
        services = ActionServices(runtime.dashboard_state, asyncio.create_task)
        set_action_services(services)
        try:
            SupervisorAssembly(runtime, logging.getLogger(__name__)).start()
            tasks = [runtime.loop_watchdog._task, runtime.workflow_watchdog._task]
            for task in tasks:
                task.cancel()
            assert runtime.dashboard_state.workflows is runtime.workflow_watchdog
            assert services.workflows is runtime.workflow_watchdog
            from gideon.automation.workflows.verify import run_verify_block

            assert runtime.workflow_watchdog._services.verify is run_verify_block
            assert not runtime.workflow_watchdog._services.memory.has_vector
            await runtime.loop_watchdog.stop()
            await runtime.workflow_watchdog.stop()
            assert all(task.done() for task in tasks)
        finally:
            set_action_services(previous)

    asyncio.run(exercise())
