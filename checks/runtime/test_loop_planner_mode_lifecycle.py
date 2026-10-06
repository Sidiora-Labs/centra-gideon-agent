"""Persisted loop mode governs native planner sessions, approvals, and lifecycle."""
import asyncio
import time

import pytest

from gideon.automation.loop import files, kinds, store
from gideon.automation.loop.loop import Loop, LoopStatus
from gideon.automation.loop.plan_walkthrough import _run_pass, planner_session_key, STEPS_SENTINEL
from gideon.automation.triggers.nudge import AutoNudgeService
from gideon.cognition.planning import runner
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import YOU, agent
from gideon.security.guardrails import incident
from gideon.security.guardrails.policy import is_unattended_session


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.setattr(runner, 'PLANNER_POLL_SECS', .01)
    incident.reset_incident_mirror()
    sessions = ConversationDirectory(AppConfig.load())
    state = ConsoleState(sessions, 0)
    service = AutoNudgeService(base_dir=tmp_path)
    kinds.ensure_loaded()
    return state, service


async def running_pass(state, service, loop, *, timeout=.3):
    task = asyncio.create_task(_run_pass(state, service, loop, kinds.get('code').walkthrough(),
                                       brief='Inspect the plan', sentinel=STEPS_SENTINEL, timeout_secs=timeout))
    key = planner_session_key(loop.id)
    deadline = time.monotonic() + 2
    while service.get_by_session(key) is None and not task.done() and time.monotonic() < deadline:
        await asyncio.sleep(.002)
    assert service.get_by_session(key) is not None
    return key, task


@pytest.mark.asyncio
@pytest.mark.parametrize('attended', [True, False])
@pytest.mark.parametrize('provider', ['', 'codex'])
async def test_real_planner_nudge_mode_runtime_and_terminal_cleanup(native, attended, provider):
    state, service = native
    loop = store.create(Loop(id='a123abcd', name='Planning', kind='code', task='Plan work',
                             status='planning', attended=attended, model='selected-model',
                             provider=provider, provider_agent='selected-agent', reasoning_effort='high'))
    key, task = await running_pass(state, service, loop)
    session = state._sessions[key]
    assert (session.model, session.acp_provider, session.acp_provider_agent, session.reasoning_effort) == (
        'selected-model', provider, 'selected-agent', 'high')
    assert session._trust is (not attended)
    assert session._unattended is (not attended)
    assert session.acp_mode == ('' if attended else 'bypassPermissions')
    assert is_unattended_session(key) is (not attended)
    nudge = service.get_by_session(key)
    assert ('autonomous' in nudge.message.lower()) is (not attended)
    session.queue_append('queued planner work')
    files.planner_sentinel_path(loop.id, STEPS_SENTINEL).write_text('actual plan output')
    assert await task == 'actual plan output'
    assert service.get_by_session(key) is None
    assert not session._queue
    assert not files.planner_sentinel_path(loop.id, STEPS_SENTINEL).exists()


@pytest.mark.asyncio
async def test_actual_owner_wait_holds_timeout_and_rearm_withdraws_planner(native):
    state, service = native
    loop = store.create(Loop(id='b123abcd', name='Owner planning', kind='code', task='Plan work', status='planning', attended=True))
    key, task = await running_pass(state, service, loop, timeout=.08)
    approval = asyncio.create_task(state.request_approval('planner-owner', 'agent', 'write_file', session=key, owner_only=True))
    while 'planner-owner' not in state._pending_approvals:
        await asyncio.sleep(.002)
    assert state.waiting_on_owner(key)
    assert not state.resolve_approval('planner-owner', True, by=agent(key))
    await asyncio.sleep(.15)
    assert not task.done()
    assert state.refuse_ended_owner('planner-owner') == ''
    assert state.resolve_approval('planner-owner', True, by=YOU)
    assert await approval is True
    assert not state.waiting_on_owner(key)
    files.planner_sentinel_path(loop.id, STEPS_SENTINEL).write_text('owner-approved plan')
    assert await task == 'owner-approved plan'
    second = asyncio.create_task(state.request_approval('planner-rearm', 'agent', 'write_file', session=key, owner_only=True))
    while 'planner-rearm' not in state._pending_approvals:
        await asyncio.sleep(.002)
    assert state.rearm_loop_approval_posture(loop.id, attended=False) >= 1
    assert await second is False
    assert 'planner-rearm' not in state._pending_approvals
    assert state.ended_as('planner-rearm') == 'cancelled'


@pytest.mark.asyncio
async def test_real_incident_hold_and_stopped_loop_do_not_restart_planner(native):
    state, service = native
    loop = store.create(Loop(id='c123abcd', name='Held planning', kind='code', task='Plan work', status='planning', attended=True))
    incident.activate('Hold planning')
    key, task = await running_pass(state, service, loop, timeout=.05)
    await asyncio.sleep(.12)
    assert not task.done()
    incident.resume()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.get_by_session(key) is None
    store.update_status(loop.id, LoopStatus.STOPPED)
    assert await _run_pass(state, service, loop, kinds.get('code').walkthrough(),
                           brief='Do not restart', sentinel=STEPS_SENTINEL, timeout_secs=0) is None
    assert service.get_by_session(key) is None
