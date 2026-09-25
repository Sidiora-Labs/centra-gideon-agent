import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.sqlite_compat import sqlite3
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.typed import TypedCapture


@pytest.fixture
def typed(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    store = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    service = TypedCapture(store, home=tmp_path)
    yield service
    store.close()


def request(service, key='concurrent-source'):
    capture = service.inbox.create(key, 'Original immutable idea source')
    preview = service.preview({'capture_id': capture['id'], 'kind': 'idea', 'fields': {'title': 'Concurrent review', 'content': 'Canonical content from reviewed capture'}})
    return {key: preview[key] for key in ('capture_id', 'kind', 'fields', 'revision', 'preview_id')} | {'request_id': 'commit-' + key}


def test_eight_connections_replay_one_canonical_idea_and_receipt(typed):
    payload = request(typed)
    barrier = Barrier(8)
    def worker(_):
        store = KnowledgeStore(str(typed.home / 'knowledge.db'))
        try:
            service = TypedCapture(store, home=typed.home)
            barrier.wait(timeout=20)
            return asyncio.run(service.commit(payload))
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(worker, range(8)))
    assert all(receipt == receipts[0] for receipt in receipts)
    destination = receipts[0]['destination_id']
    assert typed.store.get_item(destination)['content'] == 'Canonical content from reviewed capture'
    rows = typed.db.execute('SELECT id FROM items WHERE guid=?', ('typed_capture:' + payload['capture_id'],)).fetchall()
    assert [row[0] for row in rows] == [destination]
    assert typed.list()['total'] == 1
    assert typed.inbox.get(payload['capture_id'])['text'] == 'Original immutable idea source'
    assert typed.db.execute('SELECT count(*) FROM capability_knowledge_types').fetchone()[0] == 1


def test_competing_requests_cannot_change_same_capture(typed):
    first = request(typed)
    second = first | {'request_id': 'competing-request'}
    barrier = Barrier(2)
    def worker(payload):
        store = KnowledgeStore(str(typed.home / 'knowledge.db'))
        try:
            service = TypedCapture(store, home=typed.home)
            barrier.wait(timeout=20)
            try:
                return asyncio.run(service.commit(payload))
            except CaptureError as error:
                return {'error': str(error), 'status': error.status}
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [first, second]))
    saved = [item for item in results if 'destination_id' in item]
    refused = [item for item in results if 'error' in item]
    assert len(saved) == len(refused) == 1
    assert refused[0]['status'] == 409
    assert typed.list()['items'] == saved
    assert typed.db.execute('SELECT count(*) FROM items').fetchone()[0] == 1


def test_legacy_duplicates_remain_readable_without_destructive_migration(typed):
    payload = request(typed, 'legacy-source')
    previous = asyncio.run(typed.commit(payload))
    original = typed.store.get_item(previous['destination_id'])
    typed.db.execute('DROP INDEX typed_capture_guid')
    typed.db.commit()
    duplicate = typed.store.create_typed_item(item_type='fleeting', title='Second historical record', content='Different retained original content', guid=original['guid'])
    reloaded = TypedCapture(typed.store, home=typed.home)
    assert reloaded.list()['items'] == [previous]
    assert asyncio.run(reloaded.commit(payload)) == previous
    assert reloaded.store.get_item(original['id'])['content'] == original['content']
    assert reloaded.store.get_item(duplicate)['content'] == 'Different retained original content'
    assert reloaded.db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='typed_capture_guid'").fetchone() is None
    pending = request(reloaded, 'new-source-while-conflicted')
    with pytest.raises(CaptureError) as error:
        asyncio.run(reloaded.commit(pending))
    assert error.value.status == 409
    assert original['guid'] in str(error.value)
    assert reloaded.list()['total'] == 1
    assert reloaded.db.execute('SELECT count(*) FROM items').fetchone()[0] == 2
    assert reloaded.db.execute('SELECT count(*) FROM capability_knowledge_types').fetchone()[0] == 1
    assert reloaded.inbox.get(pending['capture_id'])['text'] == 'Original immutable idea source'


def test_unique_index_rejects_direct_canonical_duplicate_without_rewriting(typed):
    payload = request(typed)
    receipt = asyncio.run(typed.commit(payload))
    original = typed.store.get_item(receipt['destination_id'])
    second = KnowledgeStore(str(typed.home / 'knowledge.db'))
    try:
        duplicate = second.create_typed_item(item_type='fleeting', title='Unauthorized duplicate identity', content='Must not replace canonical original', guid=original['guid'])
        assert duplicate is None
        assert second.get_item(original['id'])['content'] == original['content']
        assert second.db.execute('SELECT count(*) FROM items').fetchone()[0] == 1
        assert asyncio.run(TypedCapture(second, home=typed.home).commit(payload)) == receipt
    finally:
        second.close()


def test_explicit_legacy_resolution_allows_index_and_original_retry(typed):
    typed.db.execute('DROP INDEX typed_capture_guid')
    typed.db.commit()
    first = typed.store.create_typed_item(item_type='fleeting', title='Keep this original', guid='typed_capture:legacy-pair')
    second = typed.store.create_typed_item(item_type='fleeting', title='User-selected duplicate', guid='typed_capture:legacy-pair')
    service = TypedCapture(typed.store, home=typed.home)
    payload = request(service, 'resolved-source')
    with pytest.raises(CaptureError):
        asyncio.run(service.commit(payload))
    service.store.delete_item(second)
    assert service.store.get_item(second) is None
    result = asyncio.run(service.commit(payload))
    assert result['destination_id']
    assert service.store.get_item(first)['title'] == 'Keep this original'
    assert service.db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='typed_capture_guid'").fetchone()[0] == 'typed_capture_guid'
    assert asyncio.run(service.commit(payload)) == result
