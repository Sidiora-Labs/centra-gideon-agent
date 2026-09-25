import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_memory import register
from gideon.workspace.capabilities.wellbeing.memory_practice import MemoryPracticeStore, next_schedule
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def card(**changes):
    value = dict(request_id='create', front='Capital of France?', back='Paris', source='personal study', tags=['geography'])
    value.update(changes)
    return value


def grade(row, value='good', request='practice'):
    return dict(request_id=request, revision=row['revision'], grade=value)


def test_canonical_artifact_content_ledger_reference_and_reopen(tmp_path):
    store = MemoryPracticeStore(tmp_path)
    row = store.create(card())
    assert row['front'] == 'Capital of France?'
    assert row['back'] == 'Paris'
    assert row['source'] == 'personal study'
    assert row['tags'] == ['geography']
    assert row['revision'] == 1
    assert row['archived'] is False
    assert row['schedule']['rules_version'] == 1
    assert row['schedule']['last_grade'] is None
    assert row['schedule']['interval_days'] == 0
    assert row['schedule']['due_at'] == row['created_at']
    assert row['practice'] is None
    artifact = store.artifacts.get(row['artifact']['slug'], version=1)
    assert artifact.readonly
    assert json.loads(artifact.content) == dict(front=row['front'], back='Paris', tags=['geography'])
    assert hashlib.sha256(artifact.content.encode()).hexdigest() == row['artifact']['sha256']
    with store.connection() as db:
        data = json.loads(db.execute('SELECT data FROM memory_card_revisions').fetchone()[0])
    assert 'front' not in data
    assert 'back' not in data
    assert 'tags' not in data
    assert data['artifact'] == row['artifact']
    assert store.create(card()) == row
    assert MemoryPracticeStore(tmp_path).get(row['id']) == row
    assert store.list_cards(due_only=True) == [row]
    assert store.list_cards(as_of='2000-01-01T00:00:00Z', due_only=True) == []


def test_content_revisions_remain_pinned_and_source_immutable(tmp_path):
    store = MemoryPracticeStore(tmp_path)
    original = store.create(card())
    payload = dict(request_id='edit', revision=1, front='Capital city of France?', tags=['geography', 'review'])
    edited = store.update(original['id'], payload)
    assert edited['front'] == 'Capital city of France?'
    assert edited['back'] == 'Paris'
    assert edited['tags'] == ['geography', 'review']
    assert edited['artifact'] != original['artifact']
    assert edited['revision'] == 2
    assert edited['created_at'] == original['created_at']
    assert edited['source'] == original['source']
    assert edited['schedule'] == original['schedule']
    assert store.update(original['id'], payload) == edited
    assert store.history(original['id']) == [original, edited]
    assert json.loads(store.artifacts.get(original['artifact']['slug'], version=1).content)['front'] == original['front']
    with pytest.raises(MeasurementError, match='changed'):
        store.update(original['id'], dict(payload, request_id='stale'))
    with pytest.raises(MeasurementError, match='Request ID'):
        store.update(original['id'], dict(payload, back='different'))
    with pytest.raises(MeasurementError, match='immutable'):
        store.update(original['id'], dict(request_id='source', revision=2, source='different'))
    assert store.get(original['id']) == edited


def test_practice_progression_resets_and_due_projection(tmp_path):
    store = MemoryPracticeStore(tmp_path)
    row = store.create(card())
    revisions = [row]
    for index, (value, interval) in enumerate([('good', 1), ('good', 3), ('good', 6), ('hard', 7.2), ('easy', 21.6), ('again', 0), ('good', 1)]):
        prior = row
        payload = grade(row, value, f'practice-{index}')
        row = store.practice(row['id'], payload)
        revisions.append(row)
        schedule = row['schedule']
        assert schedule['interval_days'] == interval
        assert schedule['last_grade'] == value
        assert schedule['rules_version'] == 1
        assert row['artifact'] == prior['artifact']
        assert row['source'] == prior['source']
        assert row['practice']['previous_due_at'] == prior['schedule']['due_at']
        assert row['practice']['next_due_at'] == schedule['due_at']
        observed = datetime.fromisoformat(schedule['last_practiced_at'])
        due = datetime.fromisoformat(schedule['due_at'])
        assert due - observed == (timedelta(minutes=10) if value == 'again' else timedelta(days=interval))
        assert store.practice(row['id'], payload) == row
    assert row['schedule']['repetitions'] == 1
    assert row['schedule']['lapses'] == 1
    assert store.history(row['id']) == revisions
    assert MemoryPracticeStore(tmp_path).get(row['id']) == row
    due = datetime.fromisoformat(row['schedule']['due_at'])
    assert store.list_cards(due_only=True, as_of=(due - timedelta(microseconds=1)).isoformat()) == []
    assert store.list_cards(due_only=True, as_of=due.isoformat()) == [row]
    assert store.list_cards(due_only=False) == [row]


def test_schedule_cap_and_explicit_versioned_rules():
    at = datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
    previous = dict(interval_days=300, repetitions=5, lapses=2)
    easy = next_schedule(previous, 'easy', at)
    assert easy['interval_days'] == 365
    assert easy['repetitions'] == 6
    assert easy['lapses'] == 2
    assert datetime.fromisoformat(easy['due_at']) - at == timedelta(days=365)
    again = next_schedule(easy, 'again', at)
    assert again['repetitions'] == 0
    assert again['lapses'] == 3
    assert datetime.fromisoformat(again['due_at']) - at == timedelta(minutes=10)
    hard = next_schedule(again, 'hard', at)
    assert hard['interval_days'] == 1
    assert hard['repetitions'] == 1


def test_archive_preserves_history_but_prevents_new_practice(tmp_path):
    store = MemoryPracticeStore(tmp_path)
    row = store.create(card())
    archived = store.update(row['id'], dict(request_id='archive', revision=1, archived=True))
    assert archived['artifact'] == row['artifact']
    assert archived['archived'] is True
    assert store.list_cards() == []
    assert store.list_cards(include_archived=True) == [archived]
    with pytest.raises(MeasurementError, match='archived'):
        store.practice(row['id'], grade(archived))
    restored = store.update(row['id'], dict(request_id='restore', revision=2, archived=False))
    assert store.list_cards() == [restored]
    practiced = store.practice(row['id'], grade(restored))
    assert practiced['schedule']['repetitions'] == 1
    assert store.history(row['id']) == [row, archived, restored, practiced]


@pytest.mark.parametrize('changes', [dict(front=''), dict(back=''), dict(source=''), dict(front='a' * 10001), dict(back='a' * 20001), dict(tags=['a', 'a']), dict(tags=['']), dict(tags=[1]), dict(tags=[{}]), dict(tags='tag'), dict(tags=[str(n) for n in range(21)]), dict(extra=True)])
def test_invalid_content_has_no_card_or_artifact(tmp_path, changes):
    store = MemoryPracticeStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.create(card(**changes))
    assert store.list_cards(include_archived=True) == []
    assert not list((tmp_path / 'artifacts').glob('memory-card-*'))


def test_invalid_practice_updates_and_filters_preserve_history(tmp_path):
    store = MemoryPracticeStore(tmp_path)
    row = store.create(card())
    for value in ['bad', None, 1, [], '']:
        with pytest.raises(MeasurementError):
            store.practice(row['id'], grade(row, value))
    for payload in [dict(request_id='bool', revision=True, archived=True), dict(request_id='archived', revision=1, archived='true'), dict(request_id='empty', revision=1, front='')]:
        with pytest.raises(MeasurementError):
            store.update(row['id'], payload)
    for filters in [dict(due_only=1), dict(include_archived='true'), dict(limit=True), dict(limit=0), dict(limit=501), dict(as_of='2026-09-25')]:
        with pytest.raises(MeasurementError):
            store.list_cards(**filters)
    assert store.history(row['id']) == [row]


def test_concurrent_receipts_artifact_integrity_and_isolation(tmp_path):
    store = MemoryPracticeStore(tmp_path / 'one')
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: MemoryPracticeStore(tmp_path / 'one').create(card()), range(4)))
    assert rows.count(rows[0]) == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        practiced = list(pool.map(lambda _: MemoryPracticeStore(tmp_path / 'one').practice(rows[0]['id'], grade(rows[0])), range(4)))
    assert practiced.count(practiced[0]) == 4
    assert len(store.history(rows[0]['id'])) == 2
    other = MemoryPracticeStore(tmp_path / 'two')
    assert other.list_cards() == []
    for method in (other.get, other.history):
        with pytest.raises(MeasurementError) as caught:
            method(rows[0]['id'])
        assert caught.value.status == 404
    reference = rows[0]['artifact']
    artifact = store.artifacts.get(reference['slug'], version=1)
    assert artifact.readonly
    with pytest.raises(PermissionError):
        store.artifacts.update(reference['slug'], content='changed')
    assert store.get(rows[0]['id']) == practiced[0]


@pytest.mark.asyncio
async def test_real_http_card_edit_grade_filter_and_archive(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        base = '/api/capabilities/wellbeing/memory/cards'
        response = await client.post(base, json=card())
        assert response.status == 200
        row = await response.json()
        path = base + '/' + row['id']
        assert await (await client.get(path)).json() == row
        assert (await (await client.get(base + '?due_only=true')).json())['cards'] == [row]
        response = await client.post(path + '/practice', json=grade(row))
        assert response.status == 200
        practiced = await response.json()
        assert practiced['schedule']['interval_days'] == 1
        assert (await (await client.get(base + '?due_only=true')).json())['cards'] == []
        response = await client.put(path, json=dict(request_id='edit', revision=2, back='Paris, France'))
        edited = await response.json()
        assert edited['back'] == 'Paris, France'
        assert (await (await client.get(path + '/history')).json())['history'] == [row, practiced, edited]
        assert (await client.put(path, json=dict(request_id='stale', revision=1, back='stale'))).status == 409
        assert (await client.get(base + '?due_only=invalid')).status == 400
        assert (await client.get(base + '?limit=invalid')).status == 400
        assert (await client.get(base + '/missing')).status == 404
        assert (await client.post(base, data='broken', headers={'Content-Type': 'application/json'})).status == 400


@pytest.mark.asyncio
async def test_native_provider_consumes_canonical_artifact_and_schedule(tmp_path):
    provider = WellbeingProvider(tmp_path)
    async def invoke(name, identity=None, payload=None):
        result = await provider.invoke('wellbeing_records', dict(operation='memory_' + name, id=identity, payload=payload or {}))
        assert result.success, result.error
        return json.loads(result.output)
    row = await invoke('create', payload=card())
    assert await invoke('get', row['id']) == row
    assert await invoke('list') == [row]
    updated = await invoke('update', row['id'], dict(request_id='edit', revision=1, tags=['review']))
    practiced = await invoke('practice', row['id'], grade(updated))
    assert practiced['schedule']['interval_days'] == 1
    assert await invoke('history', row['id']) == [row, updated, practiced]
    assert await invoke('list', payload=dict(due_only=True)) == []
    assert MemoryPracticeStore(tmp_path).get(row['id']) == practiced
    failed = await provider.invoke('wellbeing_records', {'operation': 'memory_unknown'})
    assert not failed.success
    assert 'Unknown' in failed.error
    assert 'memory_practice' in (await provider.list_tools())[0].parameters['properties']['operation']['enum']
