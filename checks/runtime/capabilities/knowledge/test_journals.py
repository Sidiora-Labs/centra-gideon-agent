import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_journals import register
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.journals import DateJournals, COLUMNS
from gideon.workspace.capabilities.knowledge.anniversaries import anniversaries
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools


@pytest.fixture
def journals(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    store = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    result = DateJournals(store, tmp_path)
    yield result
    store.close()


def body(**changes):
    return {'request_id': 'journal-first-save', 'date': '2025-09-25', 'timezone': 'UTC', 'revision': 0, 'fingerprint': '', 'preview_id': '', 'title': 'Day at the observatory', 'content': 'Recorded what actually happened.', **changes}


def update(service, receipt, **changes):
    current = service.get(receipt['date'], receipt['timezone'])['journal']
    return service.save(body(request_id='journal-second-save', revision=current['revision'], fingerprint=current['fingerprint'], **changes))


def test_date_timezone_identity_and_original_date_preserved(journals):
    assert journals.get('2025-09-25')['journal'] is None
    first = journals.save(body())
    assert first['revision'] == 1
    assert first['fingerprint']
    assert first['source_link'] == '#/knowledge/item/' + first['id']
    source = journals.store.get_item(first['id'])
    assert source['item_type'] == 'journal'
    assert source['content'] == body()['content']
    assert source['file_metadata']['original_at'] == '2025-09-25'
    assert source['file_metadata']['journal_timezone'] == 'UTC'
    assert journals.save(body()) == first
    other = journals.save(body(request_id='journal-second-zone', timezone='Europe/Berlin'))
    assert other['id'] != first['id']
    tomorrow = journals.save(body(request_id='journal-next-date', date='2025-09-26'))
    assert tomorrow['id'] not in (first['id'], other['id'])
    result = anniversaries(journals.store, date='2026-09-25', timezone='UTC')
    assert {row['source_id'] for row in result['items']} == {first['id'], other['id']}
    assert all(row['original_at'] == '2025-09-25' for row in result['items'])
    assert all(row['years_ago'] == 1 for row in result['items'])


def test_canonical_edit_revision_fts_and_immutable_replay(journals):
    first = journals.save(body())
    second = update(journals, first, title='Telescope observations', content='Saturn rings were visible.')
    assert second['id'] == first['id']
    assert second['revision'] == 2
    assert second['fingerprint'] != first['fingerprint']
    assert journals.store.get_item(first['id'])['content'] == 'Saturn rings were visible.'
    assert journals.store.search_items_fts('Saturn')[0]['id'] == first['id']
    assert journals.store.search_items_fts('Recorded') == []
    assert journals.save(body()) == first
    assert journals.get('2025-09-25')['journal']['content'] == second['content']
    with pytest.raises(CaptureError) as failure:
        journals.save(body(request_id='journal-stale-edit', revision=1, fingerprint=first['fingerprint']))
    assert failure.value.status == 409
    assert journals.store.get_item(first['id'])['content'] == second['content']
    with pytest.raises(CaptureError):
        journals.save(body(content='Conflicting request reuse'))


def test_existing_editor_changes_detected_without_overwriting(journals):
    first = journals.save(body())
    journals.store.update_item(first['id'], title='Changed in canonical editor', content='Canonical editor owns this correction.')
    with pytest.raises(CaptureError) as failure:
        journals.save(body(request_id='journal-external-stale', revision=1, fingerprint=first['fingerprint'], content='Old tab overwrites'))
    assert failure.value.status == 409
    current = journals.get('2025-09-25')['journal']
    assert current['revision'] == 1
    assert current['fingerprint'] != first['fingerprint']
    second = journals.save(body(request_id='journal-after-reload', revision=1, fingerprint=current['fingerprint'], content=current['content'] + '\nReviewed addition.'))
    assert second['revision'] == 2
    assert second['content'].startswith('Canonical editor owns this correction.')
    journals.store.update_item(first['id'], is_archived=1)
    current = journals.get('2025-09-25')['journal']
    with pytest.raises(CaptureError) as failure:
        journals.save(body(request_id='journal-archived-save', revision=2, fingerprint=current['fingerprint']))
    assert failure.value.status == 409
    assert journals.store.get_item(first['id'])['is_archived'] == 1


def test_reviewed_activity_draft_keeps_actual_citations_and_rejects_changes(journals):
    source = journals.store.create_typed_item(item_type='note', title='Actual visit', content='We visited the observatory.')
    journals.db.execute('UPDATE items SET created_at=? WHERE id=?', ('2025-09-25T12:00:00Z', source))
    journals.db.commit()
    draft = journals.draft('2025-09-25')
    assert len(draft['sources']) == 1
    assert draft['sources'][0]['source_id'] == source
    assert '#/knowledge/item/' + source in draft['content']
    assert 'not an immutable completion event' in draft['limitations'][0]
    first = journals.save(body(preview_id=draft['preview_id'], content=draft['content'] + '\nMy reflection.'))
    stored = journals.store.get_item(first['id'])
    assert stored['file_metadata']['journal_activity_draft'] == draft
    assert journals.draft('2025-09-25')['preview_id'] == draft['preview_id']
    journals.store.update_item(source, title='Corrected source title')
    with pytest.raises(CaptureError) as failure:
        update(journals, first, preview_id=draft['preview_id'])
    assert failure.value.status == 409
    assert 'My reflection.' in journals.store.get_item(first['id'])['content']
    assert journals.draft('2025-09-25')['preview_id'] != draft['preview_id']
    assert journals.draft('2025-09-24')['sources'] == []


def test_store_compare_and_update_uses_real_fts_and_default_contract(journals):
    first = journals.save(body())
    raw = journals.raw(journals.key('2025-09-25', 'UTC'))
    expected = {field: raw[field] for field in COLUMNS}
    assert journals.store.update_item(first['id'], expected=expected, content='Neptune observation') is True
    assert journals.store.update_item(first['id'], expected=expected, content='Stale overwrite') is False
    assert journals.store.get_item(first['id'])['content'] == 'Neptune observation'
    assert journals.store.search_items_fts('Neptune')[0]['id'] == first['id']
    assert journals.store.search_items_fts('Stale') == []
    assert journals.store.update_item(first['id'], title='Normal existing editor') is None
    with pytest.raises(ValueError):
        journals.store.update_item(first['id'], expected={'sql expression': 'invalid'}, content='Rejected')
    assert journals.store.get_item(first['id'])['title'] == 'Normal existing editor'
    assert journals.store.update_item('missing-id', expected=expected, content='Missing') is False


def test_concurrent_canonical_cas_admits_one_writer(journals):
    first = journals.save(body())
    raw = journals.raw(journals.key('2025-09-25', 'UTC'))
    expected = {field: raw[field] for field in COLUMNS}
    connections = [KnowledgeStore(str(journals.reviews.home / 'knowledge.db')) for _ in range(2)]
    barrier = Barrier(2)
    def writer(pair):
        index, store = pair
        barrier.wait()
        return store.update_item(first['id'], expected=expected, content='Concurrent observation ' + str(index))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, enumerate(connections)))
        assert sorted(results) == [False, True]
        assert journals.store.get_item(first['id'])['content'].startswith('Concurrent observation')
        assert journals.store.search_items_fts('Concurrent')[0]['id'] == first['id']
    finally:
        for store in connections:
            store.close()


def test_concurrent_journal_request_replays_one_destination(journals):
    connections = [KnowledgeStore(str(journals.reviews.home / 'knowledge.db')) for _ in range(4)]
    services = [DateJournals(store, journals.reviews.home) for store in connections]
    barrier = Barrier(4)
    def writer(service):
        barrier.wait()
        return service.save(body())
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(writer, services))
        assert all(row == receipts[0] for row in receipts)
        assert journals.db.execute("SELECT count(*) FROM items WHERE item_type='journal'").fetchone()[0] == 1
        assert journals.db.execute('SELECT count(*) FROM capability_knowledge_journal_mutations').fetchone()[0] == 1
    finally:
        for store in connections:
            store.close()


def test_home_drift_allows_reads_and_completed_replay_only(journals, monkeypatch):
    first = journals.save(body())
    foreign = journals.reviews.home / 'other-allocation'
    monkeypatch.setenv('GIDEON_HOME', str(foreign))
    assert journals.save(body()) == first
    assert journals.get('2025-09-25')['journal']['id'] == first['id']
    assert journals.draft('2025-09-25')['date'] == '2025-09-25'
    with pytest.raises(CaptureError) as failure:
        update(journals, first)
    assert failure.value.status == 409
    assert not foreign.exists()
    monkeypatch.setenv('GIDEON_HOME', str(journals.reviews.home))
    assert update(journals, first)['revision'] == 2


@pytest.mark.parametrize('changes', [{'date': 'invalid'}, {'timezone': 'invalid'}, {'revision': True}, {'revision': -1}, {'title': ''}, {'content': ''}, {'home': '/tmp/other'}, {'request_id': 'short'}])
def test_invalid_input_leaves_canonical_journals_empty(journals, changes):
    with pytest.raises(CaptureError):
        journals.save(body(**changes))
    assert journals.get('2025-09-25')['journal'] is None
    assert journals.db.execute('SELECT count(*) FROM capability_knowledge_journal_mutations').fetchone()[0] == 0


def test_native_journal_session_policy_and_real_consumer(journals):
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = journals.store
    provider = KnowledgeCapabilityTools(ActionServices(state=state, spawn_background=asyncio.create_task))
    token = set_current_session_key('dashboard:journal-native')
    try:
        state._sessions['journal-native'] = _ChatSession('journal-native', memory_mode='persistent')
        args = {'date': '2025-09-25', 'timezone': 'UTC'}
        assert json.loads(asyncio.run(provider.invoke('knowledge_journal_get', args)).output)['journal'] is None
        draft = asyncio.run(provider.invoke('knowledge_journal_draft', args))
        assert draft.success
        assert json.loads(draft.output)['sources'] == []
        state._sessions['journal-native'].memory_mode = 'incognito'
        assert not asyncio.run(provider.invoke('knowledge_journal_save', body())).success
        state._sessions['journal-native'].memory_mode = 'persistent'
        saved = asyncio.run(provider.invoke('knowledge_journal_save', body()))
        assert saved.success
        assert journals.store.get_item(json.loads(saved.output)['id'])['item_type'] == 'journal'
        state._sessions['journal-native'].memory_mode = 'temporary'
        assert not asyncio.run(provider.invoke('knowledge_journal_get', args)).success
        definitions = {row.name: row for row in asyncio.run(provider.list_tools())}
        assert definitions['knowledge_journal_save'].requires_approval
        assert not definitions['knowledge_journal_draft'].requires_approval
    finally:
        reset_current_session_key(token)


def test_real_http_journal_canonical_read_and_selector_rejection(journals):
    async def journey():
        state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        state._knowledge_store = journals.store
        app = web.Application()
        app['state'] = state
        register(app)
        root = '/api/capabilities/knowledge/journals'
        async with TestClient(TestServer(app)) as client:
            assert (await (await client.get(root + '?date=2025-09-25&timezone=UTC')).json())['journal'] is None
            response = await client.post(root, json=body())
            assert response.status == 200
            receipt = await response.json()
            result = await (await client.get(root + '?date=2025-09-25&timezone=UTC')).json()
            assert result['journal']['id'] == receipt['id']
            assert result['journal']['content'] == body()['content']
            assert (await client.get(root + '/draft?date=2025-09-25&timezone=UTC')).status == 200
            for query in ('home=/tmp/other', 'model=x', 'date=2025-09-25&date=2025-09-26'):
                assert (await client.get(root + '?' + query)).status == 400
            assert (await client.post(root, json={**body(), 'account_id': 'other'})).status == 400
            secret = 'sk-' + 'a' * 48
            journals.store.update_item(receipt['id'], content='Credential ' + secret)
            result = await (await client.get(root + '?date=2025-09-25&timezone=UTC')).json()
            assert secret not in json.dumps(result)
            assert secret in journals.store.get_item(receipt['id'])['content']
    asyncio.run(journey())
