import asyncio
import json
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
    assert item['instrument'] == 'piano'
    assert item['notation'] == {'format': 'plain', 'text': ''}
    assert item['artist'] == '' and item['tags'] == [] and item['key'] == ''
    assert item['capo'] == 0 and item['tuning'] == '' and item['source_url'] == ''
    assert item['attachment_availability'] == []
    assert item['due_at'] is None
    assert item['interval'] == 0
    assert item['stage'] == 'new'
    assert item['practice_history'] == []
    assert item['repetitions'] == 0
    assert item['ease'] == 2.5
    assert item['rules_version'] == 'sm2-v1'
    assert store_at(tmp_path).get(item['id']) == item


def test_songbook_metadata_notation_and_attachment_availability(tmp_path):
    store = store_at(tmp_path)
    score = store.artifacts.create(name='Public-domain chart', kind='document', content='immutable score', source='import')
    ref = {'slug': score.slug, 'version': score.version}
    item = store.create({
        'title': 'Example Song', 'artist': 'The Placeholders', 'instrument': 'GUITAR',
        'body': 'Practice the transition slowly.', 'tags': ['folk', 'public-domain', 'folk'],
        'key': 'Am', 'capo': 3, 'tuning': 'EADGBE standard',
        'notation': {'format': 'chordpro', 'text': '  [Am]Example line\n'},
        'source_url': 'https://example.com/public-domain-chart',
        'links': [{'type': 'round', 'id': 'round-1', 'label': 'Opening round'}],
        'scroll_duration_seconds': 90, 'attachment_refs': [ref],
    })
    assert item['artist'] == 'The Placeholders' and item['instrument'] == 'guitar'
    assert item['tags'] == ['folk', 'public-domain']
    assert item['key'] == 'Am' and item['capo'] == 3 and item['tuning'] == 'EADGBE standard'
    assert item['notation'] == {'format': 'chordpro', 'text': '  [Am]Example line\n'}
    assert item['source_url'] == 'https://example.com/public-domain-chart'
    assert item['links'] == [{'type': 'round', 'id': 'round-1', 'label': 'Opening round'}]
    assert item['scroll_duration_seconds'] == 90
    assert item['attachment_availability'] == [{**ref, 'available': True, 'name': 'Public-domain chart',
        'kind': 'document', 'mime': '', 'source': 'import'}]
    reopened = store_at(tmp_path)
    assert reopened.get(item['id']) == item
    store.artifacts.delete(score.slug)
    missing = reopened.get(item['id'])
    assert missing['attachment_refs'] == [ref]
    assert missing['attachment_availability'] == [{**ref, 'available': False, 'name': '', 'kind': '', 'mime': '', 'source': ''}]


def test_legacy_row_get_update_and_practice_receive_additive_defaults(tmp_path):
    store = store_at(tmp_path)
    legacy = {'id': 'legacy-song', 'title': 'Legacy chart', 'instrument': 'Piano', 'body': 'C G Am F',
              'attachment_refs': [], 'stage': 'new', 'practice_history': [], 'due_at': None,
              'ease': 2.5, 'interval': 0, 'repetitions': 0, 'revision': 1, 'rules_version': 'sm2-v1'}
    with sqlite3.connect(store.path) as connection:
        connection.execute('INSERT INTO items VALUES (?,?)', (legacy['id'], json.dumps(legacy)))
    read = store.get(legacy['id'])
    assert read['instrument'] == 'Piano' and read['body'] == 'C G Am F'
    assert read['notation'] == {'format': 'plain', 'text': ''} and read['artist'] == ''
    assert read['links'] == [] and read['scroll_duration_seconds'] is None
    practiced = store.practice(legacy['id'], attempt(read))['item']
    assert practiced['revision'] == 2 and practiced['practice_history'][0]['grade'] == 4
    edited = store.update(legacy['id'], {'revision': 2, 'capo': 2, 'key': 'C'})
    assert edited['body'] == 'C G Am F' and edited['instrument'] == 'Piano'
    assert edited['capo'] == 2 and edited['key'] == 'C'


def test_stable_archive_import_is_idempotent_and_supports_guarded_rollback(tmp_path):
    store = store_at(tmp_path)
    score = store.artifacts.create(name='Archive score', kind='document', content='C G Am F', source='archive')
    arguments = dict(
        import_id='migration-25:song:donor-song-7', source_fingerprint='sha256:archive-song-7',
        item_id='donor-song-7', created_at='2024-01-02T03:04:05+00:00',
        updated_at='2025-02-03T04:05:06+00:00',
        data={'title': 'Stable archive song', 'artist': 'Archive Artist', 'instrument': 'guitar',
              'key': 'Dm', 'capo': 2, 'tuning': 'DADGAD', 'tags': ['archive'],
              'notation': {'format': 'chordpro', 'text': '[Dm]Archive'},
              'links': [{'type': 'round', 'id': 'round-7', 'label': 'Archive round'}],
              'scroll_duration_seconds': 120,
              'attachment_refs': [{'slug': score.slug, 'version': score.version}]},
        schedule={'stage': 'learned', 'ease': 2.7, 'interval': 14, 'repetitions': 8,
                  'due_at': '2026-10-01T09:30:00+00:00',
                  'last_practiced_at': '2026-09-17T09:30:00+00:00', 'last_grade': 4},
    )
    created = store.import_song(**arguments)
    assert created['replayed'] is False and created['item']['id'] == 'donor-song-7'
    assert created['item']['created_at'] == arguments['created_at']
    assert created['item']['stage'] == 'learned' and created['item']['repetitions'] == 8
    assert store_at(tmp_path).import_song(**arguments)['replayed'] is True
    with pytest.raises(DomainError) as err:
        store.import_song(**{**arguments, 'data': {**arguments['data'], 'title': 'Changed'}})
    assert err.value.code == 'import_conflict'
    assert store.rollback_import(arguments['import_id'], arguments['source_fingerprint'])['deleted'] is True
    with pytest.raises(DomainError):
        store.get('donor-song-7')
    assert store.import_song(**arguments)['replayed'] is False
    edited = store.update('donor-song-7', {'revision': 1, 'capo': 3})
    assert edited['revision'] == 2
    with pytest.raises(DomainError) as err:
        store.rollback_import(arguments['import_id'], arguments['source_fingerprint'])
    assert err.value.code == 'revision_conflict'


def import_arguments(store, *, import_id='archive:piece-1', item_id='piece-1'):
    chart = store.artifacts.create(name='Imported score', kind='document', content='Dm A', source='archive')
    return {
        'import_id': import_id,
        'source_fingerprint': 'sha256:0123456789abcdef',
        'item_id': item_id,
        'created_at': '2024-01-02T03:04:05+00:00',
        'updated_at': '2025-02-03T04:05:06+00:00',
        'data': {
            'title': 'Imported piece',
            'artist': 'Archivist',
            'instrument': 'piano',
            'body': 'Retained notes',
            'tags': ['archive', 'recital'],
            'key': 'Dm',
            'capo': 0,
            'tuning': 'standard',
            'notation': {'format': 'plain', 'text': 'Dm A'},
            'source_url': 'https://example.com/imported-piece',
            'links': [{'type': 'track', 'id': 'track-2', 'label': 'Reference track'}],
            'scroll_duration_seconds': 180,
            'attachment_refs': [{'slug': chart.slug, 'version': chart.version}],
        },
        'schedule': {
            'stage': 'memorized',
            'ease': 2.8,
            'interval': 30,
            'repetitions': 12,
            'due_at': '2026-10-20T12:00:00+00:00',
            'last_practiced_at': '2026-09-20T12:00:00+00:00',
            'last_grade': 5,
        },
    }


def test_import_validation_is_atomic_and_rejects_existing_stable_id(tmp_path):
    cases = []
    base_store = store_at(tmp_path)
    base = import_arguments(base_store)
    cases.append({**base, 'import_id': ''})
    cases.append({**base, 'source_fingerprint': ''})
    cases.append({**base, 'item_id': ''})
    cases.append({**base, 'created_at': '2024-01-02T03:04:05'})
    cases.append({**base, 'updated_at': '2023-01-02T03:04:05+00:00'})
    cases.append({**base, 'data': {**base['data'], 'title': ''}})
    cases.append({**base, 'schedule': {**base['schedule'], 'stage': 'retired'}})
    cases.append({**base, 'schedule': {**base['schedule'], 'ease': True}})
    cases.append({**base, 'schedule': {**base['schedule'], 'ease': 1.2}})
    cases.append({**base, 'schedule': {**base['schedule'], 'interval': -1}})
    cases.append({**base, 'schedule': {**base['schedule'], 'repetitions': -1}})
    cases.append({**base, 'schedule': {**base['schedule'], 'due_at': 'tomorrow'}})
    cases.append({**base, 'schedule': {**base['schedule'], 'last_practiced_at': '2026-09-20'}})
    cases.append({**base, 'schedule': {**base['schedule'], 'last_grade': 6}})
    cases.append({**base, 'schedule': {key: value for key, value in base['schedule'].items() if key != 'last_grade'}})
    for arguments in cases:
        with pytest.raises(DomainError):
            base_store.import_song(**arguments)
    assert base_store.list() == []
    ordinary = base_store.create({'title': 'Existing piece'})
    collision = {**base, 'item_id': ordinary['id'], 'import_id': 'archive:collision'}
    with pytest.raises(DomainError) as err:
        base_store.import_song(**collision)
    assert err.value.code == 'item_conflict'
    assert base_store.get(ordinary['id'])['title'] == 'Existing piece'
    assert len(base_store.list()) == 1


def test_concurrent_import_replay_and_cross_connection_schedule_continuity(tmp_path):
    seed = store_at(tmp_path)
    arguments = import_arguments(seed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: store_at(tmp_path).import_song(**arguments), range(2)))
    assert sorted(receipt['replayed'] for receipt in receipts) == [False, True]
    assert all(receipt['item']['id'] == 'piece-1' for receipt in receipts)
    assert all(receipt['source_fingerprint'] == arguments['source_fingerprint'] for receipt in receipts)
    reopened = store_at(tmp_path)
    imported = reopened.get('piece-1')
    assert imported['title'] == 'Imported piece'
    assert imported['artist'] == 'Archivist'
    assert imported['stage'] == 'memorized'
    assert imported['ease'] == 2.8
    assert imported['interval'] == 30
    assert imported['repetitions'] == 12
    assert imported['last_grade'] == 5
    assert imported['last_practiced_at'] == '2026-09-20T12:00:00+00:00'
    assert imported['attachment_availability'][0]['available'] is True
    practiced = reopened.practice('piece-1', attempt(imported, grade=4, instant='2026-10-20T12:00:00+00:00'))
    assert practiced['replayed'] is False
    assert practiced['item']['stage'] == 'review'
    assert practiced['item']['repetitions'] == 13
    assert practiced['item']['interval'] == 84
    assert practiced['item']['ease'] == 2.8
    assert practiced['item']['last_grade'] == 4
    assert practiced['item']['last_practiced_at'] == '2026-10-20T12:00:00+00:00'
    assert practiced['item']['created_at'] == arguments['created_at']
    assert practiced['item']['updated_at'] == arguments['updated_at']
    assert practiced['item']['notation'] == arguments['data']['notation']
    assert practiced['item']['links'] == arguments['data']['links']
    assert practiced['item']['scroll_duration_seconds'] == 180


def test_import_rollback_authenticates_fingerprint_and_is_transactional(tmp_path):
    store = store_at(tmp_path)
    arguments = import_arguments(store)
    receipt = store.import_song(**arguments)
    assert receipt['item_id'] == 'piece-1'
    with pytest.raises(DomainError) as err:
        store.rollback_import(arguments['import_id'], 'sha256:different')
    assert err.value.code == 'import_conflict'
    assert store.get('piece-1')['revision'] == 1
    deleted = store_at(tmp_path).rollback_import(arguments['import_id'], arguments['source_fingerprint'])
    assert deleted == {
        'import_id': arguments['import_id'],
        'source_fingerprint': arguments['source_fingerprint'],
        'item_id': arguments['item_id'],
        'revision': 1,
        'deleted': True,
    }
    assert store.list() == []
    with pytest.raises(DomainError) as err:
        store.rollback_import(arguments['import_id'], arguments['source_fingerprint'])
    assert err.value.code == 'import_not_found'
    restored = store.import_song(**arguments)
    assert restored['replayed'] is False
    assert restored['item']['id'] == arguments['item_id']


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
    assert edited['instrument'] == 'piano'
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
    {}, {'title': ''}, {'title': '   '}, {'title': 8}, {'title': 'x' * 301},
    {'title': 'Piece', 'instrument': False}, {'title': 'Piece', 'body': None},
    {'title': 'Piece', 'revision': 1}, {'title': 'Piece', 'stage': 'review'},
    {'title': 'Piece', 'practice_history': []}, {'title': 'Piece', 'attachment_refs': {}},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x'}]},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x', 'version': True}]},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x', 'version': 0}]},
    {'title': 'Piece', 'attachment_refs': [{'slug': 'x', 'version': 1, 'path': '/tmp/a'}]},
    {'title': 'Piece', 'instrument': 'kazoo'}, {'title': 'Piece', 'capo': 13},
    {'title': 'Piece', 'tags': ['']}, {'title': 'Piece', 'notation': {'format': 'midi', 'text': 'x'}},
    {'title': 'Piece', 'notation': {'format': 'tab'}}, {'title': 'Piece', 'source_url': 'file:///tmp/chart'},
    {'title': 'Piece', 'links': [{'type': 'round', 'id': 'one'}]},
    {'title': 'Piece', 'links': [{'type': '../round', 'id': 'one', 'label': ''}]},
    {'title': 'Piece', 'scroll_duration_seconds': 14}, {'title': 'Piece', 'scroll_duration_seconds': True},
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
async def test_http_songbook_metadata_partial_edit_practice_and_attachment_loss(tmp_path):
    store = store_at(tmp_path)
    score = store.artifacts.create(name='Imported chart', kind='document', content='C G Am F', source='import')
    app = web.Application();register(app, store)
    prefix = '/api/capabilities/music/items'
    payload = {
        'title': 'Archive song', 'artist': 'The Placeholders', 'instrument': 'ukulele',
        'body': 'Practice notes', 'tags': ['archive', 'folk'], 'key': 'C', 'capo': 1,
        'tuning': 'GCEA', 'notation': {'format': 'tab', 'text': 'A|--3--|'},
        'source_url': 'https://example.com/archive-song',
        'links': [{'type': 'track', 'id': 'track-1', 'label': 'Backing track'}],
        'scroll_duration_seconds': 75,
        'attachment_refs': [{'slug': score.slug, 'version': score.version}],
    }
    async with TestClient(TestServer(app)) as client:
        response = await client.post(prefix, json=payload)
        assert response.status == 201
        item = (await response.json())['item']
        assert {key: item[key] for key in payload} == payload
        assert item['attachment_availability'][0]['available'] is True
        assert item['attachment_availability'][0]['name'] == 'Imported chart'

        response = await client.patch(prefix + '/' + item['id'], json={
            'revision': item['revision'], 'capo': 4,
            'notation': {'format': 'chordpro', 'text': '[C]Archive line'},
        })
        assert response.status == 200
        edited = (await response.json())['item']
        assert edited['capo'] == 4 and edited['notation']['format'] == 'chordpro'
        assert edited['artist'] == payload['artist'] and edited['tuning'] == payload['tuning']
        assert edited['attachment_refs'] == payload['attachment_refs']

        response = await client.post(prefix + '/' + item['id'] + '/practice', json=attempt(edited, key='metadata-practice'))
        practiced = (await response.json())['item']
        assert practiced['artist'] == payload['artist'] and practiced['notation'] == edited['notation']
        assert practiced['practice_history'][0]['attempt_id'] == 'metadata-practice'

        store.artifacts.delete(score.slug)
        reread = (await (await client.get(prefix + '/' + item['id'])).json())['item']
        assert reread['attachment_refs'] == payload['attachment_refs']
        assert reread['attachment_availability'][0]['available'] is False
        assert reread['revision'] == 3


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
