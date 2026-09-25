import asyncio
import json
import sys
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.cognition.history import ConversationLog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.handlers.capabilities_experience import register, STORE
from gideon.interfaces.dashboard.chat_utils import persisted_history_key
from gideon.workspace.capabilities.experience import ExperienceStore, Conflict, NotFound
from gideon.workspace.capabilities.experience.native_calls import NativeCalls, get_native_calls
from gideon.workspace.capabilities.experience.tools import ExperienceTools


@pytest.fixture
def calls(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    return get_native_calls(ExperienceStore(tmp_path))


def configure(calls, value):
    (calls.home / 'capabilities' / 'native_call_config.json').write_text(json.dumps(value))


def test_actual_platform_readiness_and_bundled_native_source(calls):
    assert sys.platform != 'darwin'
    report = calls.readiness()
    assert not report['available']
    assert 'Native FaceTime control requires macOS' in report['errors']
    assert report['accessibility'] == 'unverified'
    assert report['audio_transport'] == 'unqualified'
    assert calls.helper.is_file()
    assert calls.list() == []
    assert calls.configuration() is None


@pytest.mark.parametrize('value', [None, [], {}, {'target_handle': '+1234567'}, {'target_handle': 'x;open()', 'target_name': 'Person'}, {'target_handle': '+1234567', 'target_name': '\nPerson'}, {'target_handle': True, 'target_name': 'Person'}, {'target_handle': '+1234567', 'target_name': ''}, {'target_handle': '+1234567', 'target_name': 'Person', 'helper': '/tmp/other'}])
def test_invalid_operator_configuration_is_unavailable(calls, value):
    configure(calls, value)
    with pytest.raises(ValueError):
        calls.configuration()
    report = calls.readiness()
    assert not report['available']
    assert report['target_name'] is None
    assert calls.list() == []


@pytest.mark.parametrize('handle', ['+12345678901', 'person@example.com'])
def test_configured_recipient_does_not_create_native_readiness_on_linux(calls, handle):
    configure(calls, {'target_handle': handle, 'target_name': 'Recipient'})
    assert calls.configuration()['target_handle'] == handle
    report = calls.readiness()
    assert report['target_name'] == 'Recipient'
    assert not report['available']
    assert len(report['errors']) == 1


@pytest.mark.asyncio
async def test_unavailable_command_replay_conflict_and_real_reopen(calls):
    row = await calls.command({'command': 'call', 'request_id': 'one'})
    assert row['state'] == 'unavailable'
    assert 'macOS' in row['result']
    assert row == await calls.command({'command': 'call', 'request_id': 'one'})
    with pytest.raises(Conflict):
        await calls.command({'command': 'hangup', 'request_id': 'one'})
    reopened = NativeCalls(ExperienceStore(calls.home))
    assert reopened.get(row['id']) == row
    assert reopened.list() == [row]
    with pytest.raises(NotFound):
        reopened.get('missing')


@pytest.mark.asyncio
async def test_concurrent_retries_have_one_durable_request(calls):
    rows = await asyncio.gather(*(calls.command({'command': 'probe', 'request_id': 'same'}) for _ in range(12)))
    assert len({row['id'] for row in rows}) == 1
    assert len(calls.list()) == 1
    assert rows[0]['state'] == 'unavailable'
    assert get_native_calls(ExperienceStore(calls.home)) is calls
    tool = ExperienceTools(calls.store)
    result = await tool.invoke('experience_native_calls_get', {})
    assert result.success
    assert json.loads(result.output)['requests'] == calls.list()
    assert tool._native_calls is calls


@pytest.mark.parametrize('body', [None, [], {}, {'command': [], 'request_id': 'a'}, {'command': 'other', 'request_id': 'a'}, {'command': 'call', 'request_id': ''}, {'command': 'call', 'request_id': 'a/b'}, {'command': 'call', 'request_id': 'a', 'target_handle': '+1234567'}])
@pytest.mark.asyncio
async def test_invalid_commands_do_not_create_requests(calls, body):
    with pytest.raises(ValueError):
        await calls.command(body)
    assert calls.list() == []


@pytest.mark.asyncio
async def test_isolated_homes_never_share_native_requests(calls, tmp_path):
    row = await calls.command({'command': 'answer', 'request_id': 'request'})
    other = get_native_calls(ExperienceStore(tmp_path / 'another'))
    assert other.list() == []
    assert other is not calls
    with pytest.raises(NotFound):
        other.get(row['id'])
    assert len(calls.list()) == 1


@pytest.mark.asyncio
async def test_real_http_transcript_handoff_is_supplied_not_captured(calls):
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time(), conversation_log=ConversationLog(calls.home / 'history'))
    session = state.get_or_create_session('existing')
    session.append('user', 'Earlier context')
    app = web.Application()
    app[STORE] = calls.store
    app['state'] = state
    register(app)
    async with TestClient(TestServer(app)) as client:
        prefix = '/api/capabilities/experience/native-calls'
        initial = await client.get(prefix)
        assert initial.status == 200
        assert not (await initial.json())['readiness']['available']
        response = await client.post(prefix, json={'command': 'call', 'request_id': 'attempt'})
        row = await response.json()
        assert row['state'] == 'unavailable'
        body = {'conversation': 'existing', 'messages': [{'role': 'user', 'text': 'User-provided conversation notes'}], 'request_id': 'handoff'}
        route = prefix + '/' + row['id'] + '/handoff'
        saved = await client.post(route, json=body)
        assert saved.status == 200
        receipt = await saved.json()
        assert receipt['source'] == 'user_supplied_transcript'
        assert receipt['agent_continuation'] == 'not_started'
        key = persisted_history_key(state.conversation_log, session.key)
        history = state.conversation_log.read_messages(key)
        assert len(history) == 2
        assert history[0]['content'] == 'Earlier context'
        assert 'Native observation: unavailable' in history[1]['content']
        assert 'not verified' in history[1]['content']
        assert history[1]['meta']['native_call_id'] == row['id']
        assert (await client.post(route, json=body)).status == 200
        assert state.conversation_log.read_messages(key) == history
        changed = {**body, 'messages': [{'role': 'user', 'text': 'Altered'}]}
        assert (await client.post(route, json=changed)).status == 409
        assert len(session.messages) == 2
        assert (await client.post(route, json={**body, 'conversation': 'missing'})).status == 404
        assert (await client.post(route, json={**body, 'messages': []})).status == 400
        assert (await client.post(prefix, json={'command': {}, 'request_id': 'x'})).status == 400
        state.get_or_create_session('private', app='another-app')
        assert (await client.post(route, json={**body, 'conversation': 'private'})).status == 404
        state.get_or_create_session('temporary', memory_mode='temporary')
        assert (await client.post(route, json={**body, 'conversation': 'temporary'})).status == 409
        reopened = ConversationLog(calls.home / 'history')
        assert reopened.read_messages(key) == history
        assert session.task is None


@pytest.mark.asyncio
async def test_handoff_refuses_home_change_ephemeral_and_malformed_records(calls, monkeypatch, tmp_path):
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time(), conversation_log=ConversationLog(calls.home / 'history'))
    session = state.get_or_create_session('existing')
    ephemeral = state.get_or_create_session('ephemeral', ephemeral=True)
    app = web.Application()
    app[STORE] = calls.store
    app['state'] = state
    register(app)
    row = await calls.command({'command': 'probe', 'request_id': 'probe'})
    route = '/api/capabilities/experience/native-calls/' + row['id'] + '/handoff'
    body = {'conversation': 'existing', 'messages': [{'role': 'user', 'text': 'Supplied text'}], 'request_id': 'record'}
    async with TestClient(TestServer(app)) as client:
        rejected = await client.post(route, json={**body, 'conversation': 'ephemeral'})
        assert rejected.status == 409
        assert ephemeral.messages == []
        for messages in [None, [], [None], [{'role': 'system', 'text': 'x'}], [{'role': 'user', 'text': ''}], [{'role': 'user', 'text': 'x', 'native': True}]]:
            assert (await client.post(route, json={**body, 'messages': messages})).status == 400
        assert session.messages == []
        other_home = tmp_path / 'other-home'
        monkeypatch.setenv('GIDEON_HOME', str(other_home))
        assert (await client.post(route, json=body)).status == 409
        assert not other_home.exists()
        assert session.messages == []
        monkeypatch.setenv('GIDEON_HOME', str(calls.home))
        assert (await client.post(route, json=body)).status == 200
        assert len(session.messages) == 1
        assert session.messages[0]['meta']['source'] == 'user_supplied_transcript'
        assert calls.get(row['id'])['state'] == 'unavailable'


def test_recover_only_unfinished_requests_and_keep_shared_service(calls):
    with calls.store.connection() as db:
        db.execute('INSERT INTO native_call_requests VALUES(?,?,?,?,?,?)', ('interrupted-vector', 'interrupted-vector', 'probe', 'running', '', time.time()))
    assert get_native_calls(ExperienceStore(calls.home)) is calls
    assert calls.get('interrupted-vector')['state'] == 'running'
    reopened = NativeCalls(ExperienceStore(calls.home))
    assert reopened.get('interrupted-vector')['state'] == 'interrupted'
    assert 'inspect FaceTime' in reopened.get('interrupted-vector')['result']
    assert NativeCalls(ExperienceStore(calls.home)).list() == reopened.list()
