import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_music import register
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music import DomainError, RepertoireStore


def store_at(home):
    artifacts = NativeArtifactProvider(root=home / 'artifacts')
    return RepertoireStore(home / 'capabilities' / 'music', artifacts)


def create(store, title='Canon in D'):
    return store.create({'title': title, 'instrument': 'Piano', 'body': 'D A Bm F#m\nG D G A'})


def attempt(item, grade=4, instant='2026-03-28T10:00:00+01:00', key='take-1', zone='Europe/Berlin'):
    return {'attempt_id': key, 'grade': grade, 'occurred_at': instant, 'timezone': zone, 'revision': item['revision']}


def test_new_document_has_unscheduled_distinct_state(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    assert item['body'] == 'D A Bm F#m\nG D G A'
    assert item['title'] == 'Canon in D'
    assert item['instrument'] == 'Piano'
    assert item['due_at'] is None
    assert item['interval'] == 0
    assert item['stage'] == 'new'
    assert item['practice_history'] == []
    assert item['repetitions'] == 0
    assert item['ease'] == 2.5
    assert item['rules_version'] == 'sm2-v1'
    assert store_at(tmp_path).get(item['id']) == item


def test_grade_sequence_retains_clock_time_across_daylight_saving(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    first = store.practice(item['id'], attempt(item, grade=5))
    assert first['item']['due_at'] == '2026-03-29T08:00:00+00:00'
    assert first['item']['interval'] == 1
    assert first['item']['ease'] == 2.6
    assert first['item']['repetitions'] == 1
    second = store.practice(item['id'], attempt(first['item'], grade=5, instant='2026-03-29T10:00:00+02:00', key='take-2'))
    assert second['item']['due_at'] == '2026-04-04T08:00:00+00:00'
    assert second['item']['interval'] == 6
    assert second['item']['stage'] == 'review'
    third = store.practice(item['id'], attempt(second['item'], grade=4, instant='2026-04-04T10:00:00+02:00', key='take-3'))
    assert third['item']['interval'] == 16
    assert third['item']['due_at'] == '2026-04-20T08:00:00+00:00'
    assert third['item']['ease'] == 2.7
    assert [row['grade'] for row in third['item']['practice_history']] == [5, 5, 4]
    assert store_at(tmp_path).get(item['id']) == third['item']


def test_failure_resets_repetitions_but_keeps_history(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    for index in range(2):
        item = store.practice(item['id'], attempt(item, key=str(index)))['item']
    assert item['stage'] == 'review'
    failed = store.practice(item['id'], attempt(item, grade=0, key='failed'))['item']
    assert failed['stage'] == 'learning'
    assert failed['repetitions'] == 0
    assert failed['interval'] == 1
    assert failed['ease'] == 1.7
    assert [row['attempt_id'] for row in failed['practice_history']] == ['0', '1', 'failed']
    retried = store.practice(item['id'], attempt(failed, grade=3, key='relearn'))['item']
    assert retried['repetitions'] == 1
    assert retried['interval'] == 1
    assert retried['ease'] == 1.56


@pytest.mark.parametrize('grade,ease', [(0, 1.7), (1, 1.96), (2, 2.18), (3, 2.36), (4, 2.5), (5, 2.6)])
def test_independent_grade_vectors(tmp_path, grade, ease):
    store = store_at(tmp_path)
    item = create(store)
    receipt = store.practice(item['id'], attempt(item, grade=grade))
    assert receipt['attempt']['grade'] == grade
    assert receipt['item']['ease'] == ease
    assert receipt['item']['repetitions'] == (1 if grade >= 3 else 0)
    assert receipt['attempt']['rules_version'] == 'sm2-v1'


def test_ease_floor_and_restart_idempotency(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    first_request = attempt(item, grade=0)
    first = store.practice(item['id'], first_request)
    item = store.practice(item['id'], attempt(first['item'], grade=0, key='failed-again'))['item']
    assert item['ease'] == 1.3
    restored = store_at(tmp_path)
    replay = restored.practice(item['id'], first_request)
    assert replay == {**first, 'replayed': True}
    assert restored.get(item['id']) == item
    assert len(restored.get(item['id'])['practice_history']) == 2
    with pytest.raises(DomainError) as err:
        restored.practice(item['id'], {**first_request, 'grade': 1})
    assert err.value.status == 409
    assert err.value.code == 'attempt_conflict'


def test_attempt_identity_cannot_be_rebound_to_another_item(tmp_path):
    store = store_at(tmp_path)
    one = create(store)
    two = create(store, 'Second piece')
    body = attempt(one)
    store.practice(one['id'], body)
    with pytest.raises(DomainError) as err:
        store.practice(two['id'], body)
    assert err.value.code == 'attempt_conflict'
    assert store.get(two['id'])['practice_history'] == []


def test_cross_connection_replay_is_atomic(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    body = attempt(item)
    stores = [store_at(tmp_path), store_at(tmp_path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda repository: repository.practice(item['id'], body), stores))
    assert sorted(row['replayed'] for row in receipts) == [False, True]
    assert receipts[0]['item'] == receipts[1]['item']
    assert store.get(item['id'])['revision'] == 2
    assert len(store.get(item['id'])['practice_history']) == 1


def test_concurrent_different_attempts_refuse_stale_revision(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    def perform(key):
        try:
            return store_at(tmp_path).practice(item['id'], attempt(item, key=key))
        except DomainError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(perform, ['left', 'right']))
    assert outcomes.count('revision_conflict') == 1
    assert len(store.get(item['id'])['practice_history']) == 1


def test_edit_preserves_practice_and_detects_conflicts(tmp_path):
    store = store_at(tmp_path)
    original = create(store)
    practiced = store.practice(original['id'], attempt(original))['item']
    edited = store.update(original['id'], {'revision': 2, 'title': 'Updated title', 'body': ''})
    assert edited['title'] == 'Updated title'
    assert edited['body'] == ''
    assert edited['instrument'] == 'Piano'
    assert edited['practice_history'] == practiced['practice_history']
    assert edited['due_at'] == practiced['due_at']
    with pytest.raises(DomainError) as err:
        store.update(original['id'], {'revision': 2, 'title': 'Lost edit'})
    assert err.value.code == 'revision_conflict'
    assert store_at(tmp_path).get(original['id']) == edited


def test_canonical_attachment_is_validated_and_not_copied(tmp_path):
    store = store_at(tmp_path)
    art = store.artifacts.create(name='Score', kind='document', content='C D E F', source='manual')
    ref = {'slug': art.slug, 'version': art.version}
    item = store.create({'title': 'Attached score', 'attachment_refs': [ref, ref]})
    assert item['attachment_refs'] == [ref]
    assert store.artifacts.get(art.slug, version=art.version).content == 'C D E F'
    assert store_at(tmp_path).get(item['id'])['attachment_refs'] == [ref]
    assert sorted(path.name for path in store.root.iterdir()) == ['repertoire.sqlite3']
    with pytest.raises(DomainError) as err:
        store.update(item['id'], {'revision': 1, 'attachment_refs': [{'slug': art.slug, 'version': 900}]})
    assert err.value.code == 'attachment_not_found'
    assert store.get(item['id'])['revision'] == 1


def test_two_homes_never_share_records_or_attachments(tmp_path):
    first = store_at(tmp_path / 'one')
    second = store_at(tmp_path / 'two')
    art = first.artifacts.create(name='Private score', kind='document', content='Private melody')
    item = create(first)
    assert second.list() == []
    with pytest.raises(DomainError) as err:
        second.get(item['id'])
    assert err.value.status == 404
    with pytest.raises(DomainError) as err:
        second.create({'title': 'Cross home', 'attachment_refs': [{'slug': art.slug, 'version': art.version}]})
    assert err.value.code == 'attachment_not_found'
    assert second.list() == []


@pytest.mark.parametrize('payload', [
    {}, {'title': ''}, {'title': '   '}, {'title': 8}, {'title': 'x' * 201},
    {'title': 'Piece', 'instrument': False}, {'title': 'Piece', 'body': None},
    {'title': 'Piece', 'revision': 1}, {'title': 'Piece', 'stage': 'review'},
    {'title': 'Piece', 'practice_history': []}, {'title': 'Piece', 'attachment_refs': {}},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x'}]},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x', 'version': True}]},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x', 'version': 0}]},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x', 'version': 1, 'path': '/tmp/a'}]},
])
def test_bad_document_input_is_not_persisted(tmp_path, payload):
    store = store_at(tmp_path)
    with pytest.raises(DomainError):
        store.create(payload)
    assert store.list() == []


@pytest.mark.parametrize('field,value', [
    ('grade', True), ('grade', -1), ('grade', 6), ('grade', 2.5),
    ('grade', '4'), ('revision', 0), ('revision', True),
    ('attempt_id', ''), ('attempt_id', 'x' * 101),
    ('occurred_at', '2026-03-28T10:00:00'), ('occurred_at', 'yesterday'),
    ('timezone', 'Mars/Olympus'), ('timezone', ''),
])
def test_bad_attempt_does_not_change_schedule(tmp_path, field, value):
    store = store_at(tmp_path)
    item = create(store)
    with pytest.raises(DomainError):
        store.practice(item['id'], {**attempt(item), field: value})
    assert store.get(item['id']) == item


def test_out_of_order_attempt_is_rejected_without_consuming_id(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    current = store.practice(item['id'], attempt(item))['item']
    invalid = attempt(current, instant='2026-03-27T10:00:00+01:00', key='earlier')
    with pytest.raises(DomainError):
        store.practice(item['id'], invalid)
    valid = {**invalid, 'occurred_at': '2026-03-29T10:00:00+02:00'}
    assert store.practice(item['id'], valid)['replayed'] is False
    assert len(store.get(item['id'])['practice_history']) == 2


def test_pagination_has_stable_nonoverlapping_pages(tmp_path):
    store = store_at(tmp_path)
    ids = {create(store, str(index))['id'] for index in range(7)}
    first = store.list(limit=3)
    second = store.list(offset=3, limit=3)
    third = store.list(offset=6, limit=3)
    assert len(first) == 3
    assert len(second) == 3
    assert len(third) == 1
    assert {row['id'] for row in first + second + third} == ids
    assert [row['id'] for row in first + second + third] == sorted(ids)
    assert store.list(offset=7) == []
    with pytest.raises(DomainError):
        store.list(limit=101)
    with pytest.raises(DomainError):
        store.list(offset=-1)


@pytest.mark.asyncio
async def test_real_http_create_edit_practice_retry_and_reopen(tmp_path):
    store = store_at(tmp_path)
    app = web.Application()
    register(app, store)
    async with TestClient(TestServer(app)) as client:
        response = await client.post('/api/capabilities/music/items', json={'title': 'HTTP piece', 'body': 'Am F C G'})
        assert response.status == 201
        item = (await response.json())['item']
        body = attempt(item, grade=2)
        response = await client.post(f'/api/capabilities/music/items/{item["id"]}/practice', json=body)
        assert response.status == 200
        receipt = await response.json()
        assert receipt['item']['stage'] == 'learning'
        replay = await client.post(f'/api/capabilities/music/items/{item["id"]}/practice', json=body)
        assert (await replay.json()) == {**receipt, 'replayed': True}
        response = await client.patch(f'/api/capabilities/music/items/{item["id"]}', json={'revision': 2, 'instrument': 'Guitar'})
        assert response.status == 200
        edited = (await response.json())['item']
        assert edited['practice_history'] == receipt['item']['practice_history']
    reopened = web.Application()
    register(reopened, store_at(tmp_path))
    async with TestClient(TestServer(reopened)) as client:
        response = await client.get(f'/api/capabilities/music/items/{item["id"]}')
        assert response.status == 200
        assert (await response.json())['item'] == edited
        listing = await client.get('/api/capabilities/music/items')
        assert (await listing.json())['items'] == [edited]


@pytest.mark.asyncio
async def test_http_rejects_malformed_missing_and_conflicting_requests(tmp_path):
    app = web.Application()
    store = store_at(tmp_path)
    item = create(store)
    register(app, store)
    prefix = '/api/capabilities/music/items'
    async with TestClient(TestServer(app)) as client:
        invalid = await client.post(prefix, data='not-json', headers={'Content-Type': 'application/json'})
        assert invalid.status == 400
        assert (await invalid.json())['error'] == 'invalid_input'
        array = await client.post(prefix, json=[])
        assert array.status == 400
        missing = await client.get(prefix + '/missing')
        assert missing.status == 404
        assert (await missing.json())['error'] == 'not_found'
        pagination = await client.get(prefix + '?limit=abc')
        assert pagination.status == 400
        stale = await client.patch(prefix + '/' + item['id'], json={'revision': 9, 'title': 'Stale'})
        assert stale.status == 409
        assert (await stale.json())['error'] == 'revision_conflict'
        unknown = await client.post(prefix + '/' + item['id'] + '/practice', json={**attempt(item), 'path': '/tmp'})
        assert unknown.status == 400
        assert store.get(item['id']) == item


def test_sqlite_schema_and_failed_write_are_transactional(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
        connection.execute("CREATE TRIGGER reject_attempt BEFORE INSERT ON attempts BEGIN SELECT RAISE(ABORT, 'disk constraint'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.practice(item['id'], attempt(item))
    assert store_at(tmp_path).get(item['id']) == item
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
        connection.execute('DROP TRIGGER reject_attempt')
    receipt = store.practice(item['id'], attempt(item))
    assert receipt['replayed'] is False
    assert receipt['item']['revision'] == 2
