import asyncio
import json
import os
from gideon.core.sqlite_compat import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.memory_service import MemoryService
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_types import register
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.communications.store import PeopleStore
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.typed import TypedCapture, BoundHierarchy, BoundTasks
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools


@pytest.fixture
def typed(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    store = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    service = TypedCapture(store, home=tmp_path)
    yield service
    store.close()


def prepare(service, kind='idea', fields=None, key='typed-original-001'):
    original = service.inbox.create(key, 'An original imported record\nExact source text')
    values = fields if fields is not None else {'title': 'Reviewed thought', 'content': 'Something to develop', 'legacy': {'id': 19}}
    preview = service.preview({'capture_id': original['id'], 'kind': kind, 'fields': values})
    return preview, {key: preview[key] for key in ('capture_id', 'kind', 'fields', 'revision', 'preview_id')} | {'request_id': 'commit-' + key}


def commit(service, payload):
    return asyncio.run(service.commit(payload))


def test_preview_retains_unsupported_fields_without_writing(typed):
    preview, payload = prepare(typed)
    assert preview['unsupported_fields'] == ['legacy']
    assert preview['fields']['legacy'] == {'id': 19}
    assert preview['mapped_fields'] == {'title': 'Reviewed thought', 'content': 'Something to develop'}
    assert preview['destination'] == 'fleeting'
    assert preview['available']
    assert typed.list()['total'] == 0
    assert typed.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0
    again = typed.preview({key: payload[key] for key in ('capture_id', 'kind', 'fields')})
    assert again == preview
    assert typed.inbox.get(preview['capture_id'])['revision'] == 1


def test_idea_canonical_provenance_and_durable_replay(typed):
    preview, payload = prepare(typed)
    first = commit(typed, payload)
    item = typed.store.get_item(first['destination_id'])
    assert item['item_type'] == 'fleeting'
    assert item['title'] == preview['mapped_fields']['title']
    assert item['content'] == preview['mapped_fields']['content']
    assert item['file_metadata']['capture_id'] == preview['capture_id']
    assert first['original_fields']['legacy'] == {'id': 19}
    assert first['original_capture']['text'].endswith('Exact source text')
    assert item['file_metadata']['original_at'] == first['original_capture']['captured_at']
    assert first['source_link'] == '#/knowledge/item/' + item['id']
    reopened = TypedCapture(typed.store, home=typed.home)
    assert commit(reopened, payload) == first
    assert reopened.list()['total'] == 1
    assert reopened.db.execute('SELECT count(*) FROM items').fetchone()[0] == 1
    assert reopened.inbox.get(preview['capture_id'])['text'] == first['original_capture']['text']


@pytest.mark.parametrize('kind,fields', [('project', {'name': 'Canonical project', 'brief': 'Project purpose'}), ('admin', {'title': 'Canonical task', 'description': 'A concrete next action'})])
def test_project_and_admin_use_actual_canonical_stores(typed, kind, fields):
    preview, payload = prepare(typed, kind, fields)
    result = commit(typed, payload)
    if kind == 'project':
        target = BoundHierarchy(typed.home).get_project(result['destination_id'])
        assert target.name == fields['name']
        assert target.brief == fields['brief']
        assert result['source_link'] == '#/projects/' + target.id
        assert target.status == 'active'
        assert (typed.home / 'projects' / target.id / 'project.json').exists()
    else:
        target = asyncio.run(BoundTasks(typed.home).get_task(result['destination_id']))
        assert target.title == fields['title']
        assert target.description == fields['description']
        assert target.provider == 'native'
        assert target.project == ''
        assert result['source_link'] == '#/tasks?open=' + target.id
    assert target.id == result['destination_id']
    assert target.created_at
    assert commit(typed, payload) == result
    assert typed.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0


def test_person_canonical_normalization_and_atomic_import(typed):
    original = {'name': 'Ada', 'notes': 'Met at the observatory', 'identities': [{'kind': 'email', 'value': 'ADA@example.org'}], 'birthday': '1815-12-10'}
    preview, payload = prepare(typed, 'person', original)
    assert preview['unsupported_fields'] == ['birthday']
    assert preview['mapped_fields']['identities'][0]['value'] == 'ada@example.org'
    receipt = commit(typed, payload)
    people = PeopleStore(typed.home / 'capabilities/communications')
    person = people.get(receipt['destination_id'])
    assert person['name'] == 'Ada'
    assert person['notes'] == original['notes']
    assert person['identities'] == preview['mapped_fields']['identities']
    assert person['revision'] == 1
    assert receipt['original_fields'] == original
    assert receipt['source_link'].endswith(person['id'])
    assert commit(typed, payload) == receipt
    assert len(people.people()) == 1
    assert typed.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0


def test_real_memory_write_preserves_actual_id_and_source(typed):
    archive = SemanticArchive(db_path=typed.home / 'memory.db')
    archive.init()
    try:
        typed.memory = MemoryService.over_vector_store(archive)
        preview, payload = prepare(typed, 'memory', {'text': 'I remember visiting the northern observatory with my family in summer.'})
        assert preview['available']
        receipt = commit(typed, payload)
        row = archive.db.execute('SELECT * FROM episodic_memories WHERE id=?', (receipt['destination_id'],)).fetchone()
        assert row is not None
        assert row['text'] == payload['fields']['text']
        assert 'typed_capture:' + preview['capture_id'] in json.loads(row['tags'])
        assert row['embedding'] is None
        assert receipt['source_link'].endswith(row['id'])
        assert commit(typed, payload) == receipt
        assert archive.db.execute('SELECT count(*) FROM episodic_memories').fetchone()[0] == 1
        assert typed.inbox.get(preview['capture_id'])['captured_at'] == receipt['original_capture']['captured_at']
    finally:
        archive.close()


def test_memory_unavailable_is_honest_and_does_not_commit(typed):
    preview, payload = prepare(typed, 'memory', {'text': 'An original personal memory worth retaining.'})
    assert not preview['available']
    assert 'unavailable' in preview['unavailable_reason']
    with pytest.raises(CaptureError) as error:
        commit(typed, payload)
    assert error.value.status == 503
    assert typed.list()['total'] == 0
    assert typed.db.execute('SELECT count(*) FROM capability_knowledge_types').fetchone()[0] == 0


@pytest.mark.parametrize('change', [{'revision': 2}, {'preview_id': 'tampered'}, {'fields': {'title': 'Replaced without review'}}])
def test_stale_or_changed_preview_rejected(typed, change):
    _, payload = prepare(typed)
    with pytest.raises(CaptureError) as error:
        commit(typed, payload | change)
    assert error.value.status == 409
    assert typed.list()['items'] == []
    assert typed.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0


def test_conflicting_request_and_immutable_receipt(typed):
    _, payload = prepare(typed)
    result = commit(typed, payload)
    for change in ({'request_id': 'another-request'}, {'fields': {'title': 'New title'}}, {'kind': 'admin'}):
        with pytest.raises(CaptureError) as error:
            commit(typed, payload | change)
        assert error.value.status == 409
    for statement in ('UPDATE capability_knowledge_types SET payload="changed"', 'UPDATE capability_knowledge_types SET receipt="changed"', 'DELETE FROM capability_knowledge_types'):
        with pytest.raises(sqlite3.IntegrityError):
            typed.db.execute(statement)
        typed.db.rollback()
    assert typed.list()['items'] == [result]


def test_pending_project_recovers_existing_canonical_identity(typed):
    preview, payload = prepare(typed, 'project', {'name': 'Recovered expedition', 'brief': 'Stored before interruption'})
    typed.db.execute('INSERT INTO capability_knowledge_types VALUES (?,?,?,NULL)', (payload['capture_id'], payload['request_id'], json.dumps(payload, sort_keys=True, ensure_ascii=False)))
    typed.db.commit()
    target, link = asyncio.run(typed._destination(preview))
    receipt = commit(TypedCapture(typed.store, home=typed.home), payload)
    assert receipt['destination_id'] == target
    assert receipt['source_link'] == link
    assert BoundHierarchy(typed.home).get_project(target).name == 'Recovered expedition'
    assert len(list((typed.home / 'projects').glob('p-*'))) == 1


@pytest.mark.parametrize('kind,fields', [('person', {'name': '', 'identities': []}), ('person', {'name': 'Bad identity', 'identities': [{'kind': 'phone', 'value': '123'}]}), ('project', {'name': ''}), ('idea', {'title': ['bad']}), ('admin', {'title': 'x' * 301}), ('memory', {'text': None})])
def test_canonical_validation_rejects_invalid_fields(typed, kind, fields):
    with pytest.raises(ValueError):
        prepare(typed, kind, fields)
    assert typed.list()['total'] == 0


def test_pagination_bounds_and_stable_chronology(typed):
    for index in range(3):
        _, payload = prepare(typed, key=f'original-page-{index}')
        commit(typed, payload)
    first = typed.list(limit=2)
    assert first['total'] == 3
    assert first['next_offset'] == 2
    assert len(first['items']) == 2
    second = typed.list(limit=2, offset=2)
    assert len(second['items']) == 1
    assert second['next_offset'] is None
    assert first['items'][0]['request_id'].endswith('2')
    for limit, offset in ((0, 0), (101, 0), (20, -1), (True, 0)):
        with pytest.raises(CaptureError):
            typed.list(limit, offset)


def test_bound_runtime_home_cannot_drift(typed, tmp_path):
    before = os.environ.get('GIDEON_HOME')
    alternate = tmp_path / 'unrelated'
    requests = [prepare(typed, kind, fields, 'pinned-' + kind)[1] for kind, fields in [('project', {'name': 'Pinned project'}), ('admin', {'title': 'Pinned task'}), ('person', {'name': 'Pinned contact'}), ('idea', {'title': 'Pinned idea'})]]
    try:
        os.environ['GIDEON_HOME'] = str(alternate)
        for payload in requests:
            with pytest.raises(CaptureError) as error:
                commit(typed, payload)
            assert error.value.status == 409
        assert typed.list()['total'] == 0
        assert not (typed.home / 'projects').exists()
        assert not (typed.home / 'tasks').exists()
        assert not (typed.home / 'capabilities').exists()
        assert not alternate.exists()
    finally:
        if before is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = before
    for payload in requests:
        assert commit(typed, payload)['destination_id']
    assert typed.list()['total'] == 4


def test_native_tools_use_real_receipts_and_session_guards(typed):
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = typed.store
    provider = KnowledgeCapabilityTools(ActionServices(state=state, spawn_background=asyncio.create_task))
    provider._home = typed.home
    capture = typed.inbox.create('native-type-capture', 'A real native capture')
    arguments = {'capture_id': capture['id'], 'kind': 'idea', 'fields': {'title': 'Native review', 'content': 'Canonical native content'}}
    token = set_current_session_key('dashboard:typed-native')
    try:
        state._sessions['typed-native'] = _ChatSession('typed-native', memory_mode='temporary')
        denied = asyncio.run(provider.invoke('knowledge_type_preview', arguments))
        assert not denied.success
        assert denied.metadata['status'] == 403
        state._sessions['typed-native'].memory_mode = 'incognito'
        reviewed = asyncio.run(provider.invoke('knowledge_type_preview', arguments))
        assert reviewed.success
        preview = json.loads(reviewed.output)
        payload = arguments | {'request_id': 'native-typed-commit', 'revision': preview['revision'], 'preview_id': preview['preview_id']}
        denied = asyncio.run(provider.invoke('knowledge_type_commit', payload))
        assert not denied.success
        assert denied.metadata['status'] == 403
        state._sessions['typed-native'].memory_mode = 'persistent'
        saved = asyncio.run(provider.invoke('knowledge_type_commit', payload))
        assert saved.success
        receipt = json.loads(saved.output)
        assert typed.store.get_item(receipt['destination_id'])['content'] == 'Canonical native content'
        assert json.loads(asyncio.run(provider.invoke('knowledge_type_list', {})).output)['total'] == 1
        forbidden = asyncio.run(provider.invoke('knowledge_type_commit', payload | {'home': '/tmp/other'}))
        assert not forbidden.success
        tools = {tool.name: tool for tool in asyncio.run(provider.list_tools())}
        assert tools['knowledge_type_commit'].requires_approval
        assert not tools['knowledge_type_preview'].requires_approval
        assert tools['knowledge_type_list'].parameters['additionalProperties'] is False
    finally:
        reset_current_session_key(token)


def test_real_http_review_commit_and_store_isolation(typed, tmp_path):
    async def journey():
        state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        state._knowledge_store = typed.store
        app = web.Application()
        app['state'] = state
        register(app)
        other_store = KnowledgeStore(str(tmp_path / 'isolated.db'))
        other_state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        other_state._knowledge_store = other_store
        other = web.Application()
        other['state'] = other_state
        register(other)
        capture = typed.inbox.create('http-typed-capture', 'Actual HTTP input')
        root = '/api/capabilities/knowledge/types'
        arguments = {'capture_id': capture['id'], 'kind': 'idea', 'fields': {'title': 'HTTP thought', 'content': 'Reviewed through the server', 'custom': 10}}
        async with TestClient(TestServer(app)) as client, TestClient(TestServer(other)) as isolated:
            response = await client.post(root + '/preview', json=arguments)
            assert response.status == 200
            preview = await response.json()
            assert preview['unsupported_fields'] == ['custom']
            payload = arguments | {'request_id': 'http-typed-commit', 'preview_id': preview['preview_id'], 'revision': preview['revision']}
            response = await isolated.post(root + '/commit', json=payload)
            assert response.status == 404
            response = await client.post(root + '/commit', json=payload)
            assert response.status == 200
            receipt = await response.json()
            assert receipt['original_fields']['custom'] == 10
            repeat = await client.post(root + '/commit', json=payload)
            assert await repeat.json() == receipt
            assert (await (await client.get(root)).json())['total'] == 1
            assert (await (await isolated.get(root)).json())['total'] == 0
            for suffix in ('?home=/tmp/elsewhere', '?limit=1&limit=2', '?limit=101'):
                assert (await client.get(root + suffix)).status == 400
            assert (await client.post(root + '/preview', json=arguments | {'model': 'override'})).status == 400
            assert (await client.post(root + '/commit', json=payload | {'provider': 'other'})).status == 400
        other_store.close()
    asyncio.run(journey())
