import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.core.sqlite_compat import sqlite3
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_archives import register
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.knowledge.archive import ConversationArchive, MAX_BYTES
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools


def node(identity, parent, role, text, stamp=1700000000):
    return {'id': identity, 'parent': parent, 'children': [], 'message': {'id': identity, 'author': {'role': role}, 'create_time': stamp, 'content': {'content_type': 'text', 'parts': [text]}}}


def record(identity='conversation-one'):
    return {'id': identity, 'title': 'Observatory conversation', 'create_time': 1700000000, 'update_time': 1700000120,
        'current_node': 'alternative', 'mapping': {'root': {'id': 'root', 'parent': None, 'children': ['user'], 'message': None},
            'user': node('user', 'root', 'user', 'How can we observe Jupiter?'),
            'answer': node('answer', 'user', 'assistant', 'Use the observatory telescope.', 1700000060),
            'alternative': node('alternative', 'user', 'assistant', 'Try binoculars under clear skies.', 1700000120)}}


def body(records=None):
    return {'format': 'chatgpt', 'content': json.dumps(records if records is not None else [record()], ensure_ascii=False, indent=2)}


@pytest.fixture
def archive(tmp_path):
    store = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    yield ConversationArchive(store)
    store.close()


def payload(service, data=None, key='archive-request-001', selected=None):
    data = data or body()
    preview = service.preview(data)
    return {**data, 'request_id': key, 'source_digest': preview['source_digest'], 'conversation_ids': selected or [row['id'] for row in preview['conversations'] if row['valid']]}


def test_preview_preserves_all_branches_and_does_not_write(archive):
    result = archive.preview(body())
    row = result['conversations'][0]
    assert result['format'] == 'chatgpt'
    assert result['total'] == 1
    assert len(result['source_digest']) == 64
    assert row['id'] == 'conversation-one'
    assert row['valid']
    assert row['error'] == ''
    assert row['created_at'] == '2023-11-14T22:13:20+00:00'
    assert row['updated_at'] == '2023-11-14T22:15:20+00:00'
    assert row['branch_count'] == 1
    assert row['message_count'] == 3
    assert row['unsupported_parts'] == 0
    assert row['existing_id'] is None
    assert archive.list()['total'] == 0
    assert archive.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0
    assert not archive.files_root.exists()


def test_real_canonical_notes_raw_source_and_search(archive):
    source = record()
    source['mapping']['answer']['message']['content']['parts'].append({'content_type': 'image_asset_pointer', 'asset_pointer': 'missing-image-reference'})
    source['custom_metadata'] = {'retained': True}
    data = body([source])
    receipt = archive.commit(payload(archive, data))
    assert len(receipt['items']) == 1
    target = archive.store.get_item(receipt['items'][0]['destination_id'])
    original = archive.store.get_item(receipt['source_item_id'])
    assert target['item_type'] == 'note'
    assert target['title'] == source['title']
    assert 'Use the observatory telescope.' in target['content']
    assert 'Try binoculars under clear skies.' in target['content']
    assert '## user' in target['content']
    assert 'parent: user' in target['content']
    assert target['content'].index('observe Jupiter') < target['content'].index('Use the observatory')
    assert target['file_metadata']['original_at'] == '2023-11-14T22:13:20+00:00'
    assert target['file_metadata']['archive_source_id'] == original['id']
    assert target['file_metadata']['unsupported_parts'] == 1
    assert original['item_type'] == 'document'
    assert original['mime_type'] == 'application/json'
    assert Path(original['file_path']).read_bytes() == data['content'].encode()
    assert archive.original(original['id']) == data['content'].encode()
    assert json.loads(archive.original(original['id']))[0]['custom_metadata'] == {'retained': True}
    assert any(row['id'] == target['id'] for row in archive.store.search_items_fts('telescope'))
    assert receipt['source_link'].endswith(original['id'])
    assert receipt['items'][0]['status'] == 'imported'


def test_replay_and_whitespace_archive_deduplicate_sources(archive):
    data = body()
    request = payload(archive, data)
    first = archive.commit(request)
    assert ConversationArchive(archive.store).commit(request) == first
    assert archive.db.execute('SELECT count(*) FROM items').fetchone()[0] == 2
    reformatted = {'format': 'chatgpt', 'content': json.dumps(json.loads(data['content']))}
    second = archive.commit(payload(archive, reformatted, 'archive-request-002'))
    assert second['source_digest'] != first['source_digest']
    assert second['source_item_id'] != first['source_item_id']
    assert second['items'][0]['destination_id'] == first['items'][0]['destination_id']
    assert second['items'][0]['status'] == 'existing'
    assert archive.db.execute('SELECT count(*) FROM items WHERE item_type="note"').fetchone()[0] == 1
    assert archive.preview(reformatted)['conversations'][0]['existing_id'] == first['items'][0]['destination_id']


def test_updated_source_version_retains_previous_note(archive):
    first = archive.commit(payload(archive))
    revised = record()
    revised['mapping']['answer']['message']['content']['parts'] = ['Use a larger telescope after sunset.']
    second = archive.commit(payload(archive, body([revised]), 'archive-request-002'))
    assert first['items'][0]['destination_id'] != second['items'][0]['destination_id']
    original = archive.store.get_item(first['items'][0]['destination_id'])
    assert 'observatory telescope' in original['content']
    assert archive.db.execute('SELECT count(*) FROM items WHERE item_type="note"').fetchone()[0] == 2
    assert json.loads(archive.original(first['source_item_id']))[0]['mapping']['answer']['message']['content']['parts'] == ['Use the observatory telescope.']


def test_reviewed_selection_imports_only_chosen_and_keeps_full_source(archive):
    data = body([record('first'), record('second')])
    receipt = archive.commit(payload(archive, data, selected=['second']))
    assert [row['conversation_id'] for row in receipt['items']] == ['second']
    assert len(json.loads(archive.original(receipt['source_item_id']))) == 2
    assert archive.preview(data)['conversations'][0]['existing_id'] is None
    assert archive.preview(data)['conversations'][1]['existing_id'] == receipt['items'][0]['destination_id']
    assert archive.db.execute('SELECT count(*) FROM items WHERE item_type="note"').fetchone()[0] == 1


@pytest.mark.parametrize('mutation', ['cycle', 'missing_parent', 'bad_parts', 'bad_timestamp', 'empty', 'no_messages'])
def test_malformed_conversation_is_visible_and_not_importable(archive, mutation):
    source = record()
    if mutation == 'cycle':
        source['mapping']['user']['parent'] = 'answer'
    elif mutation == 'missing_parent':
        source['mapping']['user']['parent'] = 'absent'
    elif mutation == 'bad_parts':
        source['mapping']['user']['message']['content']['parts'] = 'not-a-list'
    elif mutation == 'bad_timestamp':
        source['create_time'] = 'yesterday'
    elif mutation == 'empty':
        source['mapping'] = {}
    else:
        source['mapping'] = {'root': source['mapping']['root']}
    data = body([source, record('valid')])
    result = archive.preview(data)
    assert not result['conversations'][0]['valid']
    assert result['conversations'][0]['error']
    assert result['conversations'][1]['valid']
    request = payload(archive, data, selected=['conversation-one'])
    with pytest.raises(CaptureError):
        archive.commit(request)
    assert archive.list()['total'] == 0
    assert not archive.files_root.exists()


@pytest.mark.parametrize('data', [{'format': 'unknown', 'content': '[]'}, {'format': 'chatgpt', 'content': '{}'}, {'format': 'chatgpt', 'content': '[NaN]'}, {'format': 'chatgpt', 'content': '['}, {'format': 'chatgpt', 'content': 'x' * (MAX_BYTES + 1)}, body([record(), record()])])
def test_archive_admission_is_bounded_and_strict(archive, data):
    with pytest.raises(CaptureError):
        archive.preview(data)
    assert archive.list()['items'] == []
    assert not archive.files_root.exists()


def test_changed_review_and_request_conflicts_never_overwrite(archive):
    request = payload(archive)
    with pytest.raises(CaptureError) as error:
        archive.commit(request | {'content': request['content'] + ' '})
    assert error.value.status == 409
    assert archive.list()['total'] == 0
    first = archive.commit(request)
    other = payload(archive, body([record('another')]))
    with pytest.raises(CaptureError) as error:
        archive.commit(other)
    assert error.value.status == 409
    assert archive.list()['items'] == [first]
    with pytest.raises(sqlite3.IntegrityError):
        archive.db.execute('UPDATE capability_knowledge_archives SET receipt="changed"')
    archive.db.rollback()
    assert archive.list()['items'] == [first]


def test_original_hash_and_path_guards(archive, tmp_path):
    receipt = archive.commit(payload(archive))
    item = archive.store.get_item(receipt['source_item_id'])
    path = Path(item['file_path'])
    path.write_text('changed original')
    with pytest.raises(CaptureError) as error:
        archive.original(item['id'])
    assert error.value.status == 409
    foreign = tmp_path / 'outside.json'
    foreign.write_text('secret outside source root')
    archive.store.update_item(item['id'], file_path=str(foreign))
    with pytest.raises(CaptureError) as error:
        archive.original(item['id'])
    assert error.value.status == 404
    with pytest.raises(CaptureError):
        archive.original(receipt['items'][0]['destination_id'])


def test_archive_pinned_store_and_files_survive_home_change(archive, tmp_path):
    before = os.environ.get('GIDEON_HOME')
    foreign = tmp_path / 'foreign-allocation'
    try:
        os.environ['GIDEON_HOME'] = str(foreign)
        request = payload(archive)
        with pytest.raises(CaptureError) as error:
            archive.commit(request)
        assert error.value.status == 409
        assert not foreign.exists()
        assert not archive.files_root.exists()
        assert archive.list()['total'] == 0
    finally:
        if before is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = before
    receipt = archive.commit(request)
    assert archive.original(receipt['source_item_id']) == body()['content'].encode()
    assert archive.files_root.is_dir()


def test_native_import_invokes_real_store_and_session_policy(archive):
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = archive.store
    provider = KnowledgeCapabilityTools(ActionServices(state=state, spawn_background=asyncio.create_task))
    token = set_current_session_key('dashboard:archive-native')
    try:
        state._sessions['archive-native'] = _ChatSession('archive-native', memory_mode='temporary')
        denied = asyncio.run(provider.invoke('knowledge_archive_preview', body()))
        assert not denied.success
        assert denied.metadata['status'] == 403
        state._sessions['archive-native'].memory_mode = 'incognito'
        reviewed = asyncio.run(provider.invoke('knowledge_archive_preview', body()))
        assert reviewed.success
        request = {**body(), 'request_id': 'native-archive-import', 'source_digest': json.loads(reviewed.output)['source_digest'], 'conversation_ids': ['conversation-one']}
        denied = asyncio.run(provider.invoke('knowledge_archive_commit', request))
        assert not denied.success
        assert denied.metadata['status'] == 403
        state._sessions['archive-native'].memory_mode = 'persistent'
        imported = asyncio.run(provider.invoke('knowledge_archive_commit', request))
        assert imported.success
        receipt = json.loads(imported.output)
        assert archive.store.get_item(receipt['items'][0]['destination_id'])['item_type'] == 'note'
        assert json.loads(asyncio.run(provider.invoke('knowledge_archive_list', {})).output)['total'] == 1
        rejected = asyncio.run(provider.invoke('knowledge_archive_commit', request | {'runtime': 'other'}))
        assert not rejected.success
        tools = {tool.name: tool for tool in asyncio.run(provider.list_tools())}
        assert tools['knowledge_archive_commit'].requires_approval
        assert not tools['knowledge_archive_preview'].requires_approval
    finally:
        reset_current_session_key(token)


def test_http_original_bytes_selection_and_isolation(archive, tmp_path):
    async def journey():
        state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        state._knowledge_store = archive.store
        app = web.Application()
        app['state'] = state
        register(app)
        other_store = KnowledgeStore(str(tmp_path / 'other.db'))
        other_state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        other_state._knowledge_store = other_store
        other = web.Application()
        other['state'] = other_state
        register(other)
        root = '/api/capabilities/knowledge/archives'
        async with TestClient(TestServer(app)) as client, TestClient(TestServer(other)) as isolated:
            response = await client.post(root + '/preview', json=body())
            assert response.status == 200
            preview = await response.json()
            request = {**body(), 'request_id': 'http-archive-import', 'source_digest': preview['source_digest'], 'conversation_ids': ['conversation-one']}
            response = await client.post(root + '/commit', json=request)
            assert response.status == 200
            receipt = await response.json()
            repeat = await client.post(root + '/commit', json=request)
            assert await repeat.json() == receipt
            source = await client.get(root + '/sources/' + receipt['source_item_id'])
            assert source.status == 200
            assert source.headers['Content-Disposition'].startswith('attachment;')
            assert await source.read() == body()['content'].encode()
            assert (await isolated.get(root + '/sources/' + receipt['source_item_id'])).status == 404
            assert (await (await isolated.get(root)).json())['total'] == 0
            for suffix in ('?home=/tmp/other', '?provider=x', '?offset=-1', '?limit=2&limit=3'):
                assert (await client.get(root + suffix)).status == 400
            assert (await client.post(root + '/commit', json=request | {'model': 'override'})).status == 400
            assert (await client.get(root + '/sources/' + receipt['source_item_id'] + '?runtime=other')).status == 400
        other_store.close()
    asyncio.run(journey())
