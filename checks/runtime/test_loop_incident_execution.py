"""Incident holds stop loop work without consuming due cycles or claiming progress."""

import asyncio
import json
import logging
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gideon.automation.loop import files, manager, store, watchdog
from gideon.automation.loop.loop import INCIDENT_HOLD, Loop, LoopStatus
from gideon.automation.triggers import idle_poll, nudge
from gideon.security.guardrails import incident


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(files, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(nudge, "_INSTANCE", None)
    incident.reset_incident_mirror()
    yield tmp_path
    incident.reset_incident_mirror()


@pytest.mark.asyncio
async def test_actual_idle_nudge_remains_due_and_fires_once_after_release(
    isolated, monkeypatch
):
    fired = []

    async def on_fire(row):
        fired.append(row.session_name)
        return True

    service = nudge.AutoNudgeService(isolated, on_fire=on_fire)
    monkeypatch.setattr(nudge, "_INSTANCE", service)
    row = await service.add(
        session_name="loop-abcd1234", message="next cycle", idle_secs=60, max_cycles=5
    )
    idle_poll.save_state(
        row.id, idle_poll.IdleState(armed_at=100, created_ts=100), base_dir=isolated
    )
    before = idle_poll.load_state(row.id, base_dir=isolated)
    incident.activate("hold")
    count, skipped = await idle_poll.poll(
        service._store, None, None, now=161, base_dir=isolated
    )
    assert count == 0 and skipped == [
        {"trigger_id": row.id, "reason": "incident_active"}
    ]
    assert fired == [] and idle_poll.load_state(row.id, base_dir=isolated) == before
    assert service.get_by_session(row.session_name).active
    incident.resume()
    count, skipped = await idle_poll.poll(
        service._store, None, None, now=162, base_dir=isolated
    )
    assert count == 1 and not skipped and fired == [row.session_name]
    assert idle_poll.load_state(row.id, base_dir=isolated).cycle_count == 1
    assert (
        await idle_poll.poll(service._store, None, None, now=163, base_dir=isolated)
    )[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("attended", [True, False])
async def test_real_watchdog_stops_native_provider_without_crediting_cycle(
    isolated, monkeypatch, attended
):
    from gideon.core.config import AppConfig
    from gideon.core.constants import dashboard_session_key
    from gideon.engine.session import ConversationDirectory, _Session
    from gideon.interfaces.dashboard.state import _ChatSession

    directory = ConversationDirectory(AppConfig.load())
    sessions = {}
    providers = []
    tasks = []
    for key in ["loop-abcd1234", "loop-abcd1234-task-one", "interactive"]:
        session = _ChatSession(key)
        session._queue = deque(["queued turn"])
        task = asyncio.create_task(asyncio.Event().wait())
        tasks.append(task)
        session.task = task

        async def cancel(*, wait_ack_timeout, task=task):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return "acked"

        provider = SimpleNamespace(cancel=AsyncMock(side_effect=cancel))
        directory._sessions[dashboard_session_key(key)] = _Session(provider)
        sessions[key] = session
        providers.append(provider)
    state = SimpleNamespace(_sessions=sessions, sessions=directory, push_refresh=Mock())
    loop = store.create(
        Loop(
            id="abcd1234",
            name="Hold check",
            task="Observe",
            kind="goal",
            attended=attended,
            status="running",
            started_at=time.time(),
        )
    )
    wd = watchdog.LoopWatchdog(state, SimpleNamespace())
    wd._swept = True
    monkeypatch.setattr(wd, "_handle_question", lambda *args, **kwargs: False)
    wd._running_since[loop.id] = 1
    wd._last_activity[loop.id] = 1
    credit = Mock(side_effect=AssertionError("held loop credited a cycle"))
    monkeypatch.setattr(files, "record_cycle_findings", credit)
    incident.activate("hold")
    try:
        await wd._poll_once()
        assert (
            store.get(loop.id).status == "running"
            and store.get_redacted(loop.id)["held"] == INCIDENT_HOLD
        )
        assert store.list_redacted()[0]["held"] == INCIDENT_HOLD
        assert (
            not sessions["loop-abcd1234"]._queue
            and not sessions["loop-abcd1234-task-one"]._queue
        )
        assert list(sessions["interactive"]._queue) == ["queued turn"]
        assert providers[0].cancel.await_count == providers[1].cancel.await_count == 1
        assert providers[2].cancel.await_count == 0 and credit.call_count == 0
        assert (
            not sessions["loop-abcd1234"].running
            and not sessions["loop-abcd1234-task-one"].running
        )
        assert sessions["interactive"].running
        assert loop.id not in wd._running_since and wd._last_activity[loop.id] > 1
        for _ in range(5):
            wd.record_turn_outcome(loop.id, ok=False)
        assert (
            wd._consec_errors.get(loop.id, 0) == 0
            and store.get(loop.id).status == "running"
        )
        incident.resume()
        monkeypatch.setattr(files, "record_cycle_findings", Mock())
        await wd._poll_once()
        assert loop.id not in wd._held and not store.get_redacted(loop.id)["held"]
        assert store.get(loop.id).status == "running"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize(
    "status", [value.value for value in LoopStatus if value != LoopStatus.RUNNING]
)
def test_nonrunning_loop_never_attributes_its_state_to_incident(status):
    incident.activate("hold")
    from gideon.automation.loop.loop import held_reason

    assert held_reason(status) == ""


@pytest.mark.asyncio
async def test_logical_cycle_does_not_reprompt_after_incident_or_recount_cancellation(
    monkeypatch,
):
    from gideon.engine.nudge_dispatch import NudgeLimits, NudgeTurn

    service = SimpleNamespace(
        get_by_session=lambda key: SimpleNamespace(active=True),
        notify_turn_complete=Mock(),
    )
    session = SimpleNamespace(
        key="loop-abcd1234", _app="loop", _last_turn_errored=False, append=Mock()
    )
    state = SimpleNamespace(_sessions={session.key: session})
    driver = SimpleNamespace(
        runtime=SimpleNamespace(autonudge_svc=service),
        limits=NudgeLimits(5, 5, 3, "retry"),
        logger=logging.getLogger("test"),
    )
    turn = NudgeTurn(driver, state, session, "work")

    async def first(message):
        incident.activate("hold")
        session._last_turn_errored = True

    turn.run_once = AsyncMock(side_effect=first)
    turn.finding_count = lambda: 0
    monkeypatch.setattr(nudge, "_INSTANCE", service)
    await turn.run()
    assert turn.run_once.await_count == 1 and session.append.call_count == 0
    service.notify_turn_complete.assert_called_once_with(session.key, errored=False)


@pytest.mark.asyncio
async def test_operation_time_nudge_admission_rejects_incident():
    from gideon.engine.nudge_dispatch import NudgeDispatch, NudgeLimits

    runtime = SimpleNamespace(dashboard_state=SimpleNamespace(_sessions={}))
    dispatch = NudgeDispatch(
        runtime,
        render=Mock(),
        logger=logging.getLogger("test"),
        limits=NudgeLimits(5, 5, 3, "retry"),
    )
    incident.activate("hold")
    assert await dispatch.fire(SimpleNamespace(id="nudge:worker")) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("app", ["loop", "loops"])
async def test_hidden_loop_work_never_requests_followup_model_call(monkeypatch, app):
    from gideon.interfaces.dashboard import chat_followups

    monkeypatch.setattr(chat_followups, "_followups_enabled", lambda: True)
    generated = AsyncMock()
    monkeypatch.setattr(chat_followups, "_generate_followups", generated)
    await chat_followups._maybe_followups(
        None, SimpleNamespace(_app=app, is_restricted=False)
    )
    generated.assert_not_awaited()


@pytest.mark.asyncio
async def test_incident_watch_observes_external_file_resume_and_survives_callback_failure(
    isolated,
):
    seen = []
    changed = asyncio.Event()

    def observe(state):
        seen.append(state.active)
        changed.set()
        if state.active:
            raise ValueError("unavailable subscriber")

    task = asyncio.create_task(incident.watch(observe, interval=0.001))
    try:
        await asyncio.sleep(0.002)
        incident.activate("hold")
        await asyncio.wait_for(changed.wait(), 1)
        changed.clear()
        (isolated / "incident.json").write_text(
            json.dumps({"active": False, "reason": "", "started_at": ""})
        )
        await asyncio.wait_for(changed.wait(), 1)
        assert seen == [True, False]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_planner_hold_preserves_timeout_budget_and_returns_only_real_output(
    isolated, monkeypatch
):
    from gideon.cognition.planning import runner

    clock = {"now": 100.0, "polls": 0}
    session = SimpleNamespace(
        key="loop-plan-abcd1234", running=True, _queue=deque(["queued"])
    )
    stop = AsyncMock(return_value="soft")
    state = SimpleNamespace(
        _sessions={session.key: session},
        sessions=SimpleNamespace(stop_turn=stop),
        get_or_create_session=lambda **kwargs: session,
        push_sessions_update=Mock(),
    )
    service = SimpleNamespace(
        add=AsyncMock(),
        remove=AsyncMock(),
        get_by_session=lambda key: SimpleNamespace(id="planner", active=True),
    )
    monkeypatch.setattr(runner.time, "time", lambda: clock["now"])

    async def poll(_):
        clock["now"] += 1
        clock["polls"] += 1
        if clock["polls"] == 5:
            incident.resume()
            (isolated / "out.txt").write_text("real output")

    monkeypatch.setattr(runner.asyncio, "sleep", poll)
    incident.activate("hold")
    output = await runner.run_planner_pass(
        state,
        service,
        session_key=session.key,
        agent_name="planner",
        workspace_dir=str(isolated),
        files_dir=str(isolated),
        sentinel="out.txt",
        brief="plan",
        app="loops",
        timeout_secs=2,
    )
    assert output == "real output" and clock["polls"] == 5
    assert not session._queue and stop.await_count == 1
    service.remove.assert_awaited_once_with("planner")
