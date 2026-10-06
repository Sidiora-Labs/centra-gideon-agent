"""New-loop mode defaults through authenticated creation and native lifecycle."""
import json
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.loop import manager, store
from gideon.automation.loop.loop import Loop
from gideon.automation.triggers.nudge import AutoNudgeService
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.loop_routes import register_unified_loop_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.security.guardrails.policy import is_unattended_session


@pytest.mark.asyncio
@pytest.mark.parametrize('owner_mode', [None, False, True])
async def test_authenticated_create_start_pause_resume_retains_owner_mode(tmp_path, monkeypatch, owner_mode):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    state = ConsoleState(ConversationDirectory(AppConfig.load()), 0)
    service = AutoNudgeService(base_dir=tmp_path)
    await service.start()
    app = web.Application(middlewares=[token_auth_middleware(port=19429)])
    app['state'] = state
    register_unified_loop_routes(app)
    cookies = {'gideon_token_19429': generate_token('loop-creator', kind='desktop')}
    payload = {'kind': 'goal', 'task': 'Investigate the next release plan', 'name': 'Release investigation'}
    if owner_mode is not None:
        payload['attended'] = owner_mode
    expected = True if owner_mode is None else owner_mode
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/api/loops', json=payload, cookies=cookies)
            assert response.status == 201, await response.text()
            created = await response.json()
            identity = created['id']
            assert created['attended'] is expected
            for action in ('start', 'pause', 'resume'):
                response = await client.patch(f'/api/loops/{identity}', json={'action': action}, cookies=cookies)
                assert response.status == 200, await response.text()
                row = await response.json()
                assert row['attended'] is expected
                actual = store.get(identity)
                assert actual.attended is expected
                session = state._sessions[manager.session_key(identity)]
                assert session._trust is (not expected)
                assert session._unattended is (not expected)
                assert is_unattended_session(session.key) is (not expected)
                nudge = service.get_by_session(session.key)
                assert nudge is not None and nudge.active is (action != 'pause')
            # A fresh decoder must retain the persisted choice independently of constructor defaults.
            decoded = Loop.from_dict(json.loads(json.dumps(store.get(identity).to_dict())))
            assert decoded.attended is expected
    finally:
        service.stop()


def test_new_model_defaults_and_legacy_serialized_record_are_distinct(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    fresh = store.create(Loop(id='d123abcd', name='Fresh loop', kind='goal', task='Investigate release risks'))
    assert store.get(fresh.id).attended is True
    legacy_record = {'id': 'e123abcd', 'name': 'Existing loop', 'kind': 'goal', 'task': 'Preserve prior owner mode'}
    restored = store.create(Loop.from_dict(legacy_record))
    assert restored.attended is False
    assert store.get(restored.id).attended is False
    store.update_spec(restored.id, {'name': 'Renamed existing loop'})
    assert store.get(restored.id).attended is False
