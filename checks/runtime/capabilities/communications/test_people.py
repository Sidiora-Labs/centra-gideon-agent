import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore, care


def person(**changes):
    return {'name': 'Example Friend', 'notes': 'Preserve this note', 'ring': 'core',
            'cadence_days': 7, 'identities': [{'kind': 'email', 'value': 'friend@example.com'}], **changes}


def touch(**changes):
    return {'source': 'manual', 'external_id': 'meeting-1', 'occurred_at': '2026-09-01T10:00:00+02:00',
            'direction': 'mutual', 'summary': 'Met for lunch', **changes}


def test_person_persistence_and_correction(tmp_path):
    store = PeopleStore(tmp_path)
    created = store.save(person())
    assert created['revision'] == 1
    assert created['name'] == 'Example Friend'
    assert created['id']
    assert store.path.name == 'people.sqlite3'
    reopened = PeopleStore(tmp_path)
    assert reopened.get(created['id']) == created
    assert reopened.people() == [created]
    updated = reopened.save(person(name='Updated Friend', revision=1), created['id'])
    assert updated['revision'] == 2
    assert updated['notes'] == 'Preserve this note'
    assert PeopleStore(tmp_path).get(created['id']) == updated
    assert len(reopened.people()) == 1


def test_normalized_identities_and_ambiguity(tmp_path):
    store = PeopleStore(tmp_path)
    identities = [{'kind': 'email', 'value': ' Friend@Example.COM '},
                  {'kind': 'email', 'value': 'friend@example.com'},
                  {'kind': 'phone', 'value': '+1 (202) 555-0123'},
                  {'kind': 'handle', 'value': 'ExampleHandle'}]
    first = store.save(person(identities=identities))
    assert first['identities'] == [{'kind': 'email', 'value': 'friend@example.com'},
                                   {'kind': 'phone', 'value': '+12025550123'},
                                   {'kind': 'handle', 'value': 'ExampleHandle'}]
    with pytest.raises(PeopleError) as failure:
        store.save(person(name='Other person'))
    assert failure.value.status == 409
    assert 'ambiguity' in str(failure.value)
    assert len(store.people()) == 1
    second = store.save(person(name='Other person', identities=[]))
    with pytest.raises(PeopleError) as failure:
        store.save(person(revision=1), second['id'])
    assert failure.value.status == 409
    assert store.get(second['id'])['identities'] == []
    changed = store.save(person(identities=[{'kind': 'email', 'value': 'new@example.com'}], revision=1), first['id'])
    assert changed['identities'][0]['value'] == 'new@example.com'
    adopted = store.save(person(revision=1), second['id'])
    assert adopted['identities'][0]['value'] == 'friend@example.com'


@pytest.mark.parametrize('change', [
    {'name': ''}, {'name': 'a' * 201}, {'name': None}, {'notes': 'a' * 10001},
    {'notes': '\x00'}, {'ring': 'unknown'}, {'cadence_days': 0}, {'cadence_days': 3651},
    {'cadence_days': True}, {'cadence_days': 1.5}, {'identities': {}},
    {'identities': [{'kind': 'email', 'value': 'invalid'}]},
    {'identities': [{'kind': 'phone', 'value': '2025550123'}]},
    {'identities': [{'kind': 'phone', 'value': '+012345678'}]},
    {'identities': [{'kind': 'handle', 'value': ''}]},
    {'identities': [{'kind': 'unknown', 'value': 'name'}]},
    {'identities': [{'kind': 'email', 'value': 'one@example.com', 'home': '/tmp'}]},
    {'identities': [{'kind': 'handle', 'value': str(i)} for i in range(101)]},
    {'root': '/tmp'}, {'revision': 1}, {'id': 'chosen'},
])
def test_invalid_person_is_not_persisted(tmp_path, change):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError) as failure:
        store.save(person(**change))
    assert failure.value.status == 400
    assert store.people() == []


def test_stale_edit_does_not_overwrite_notes_or_touchpoints(tmp_path):
    store = PeopleStore(tmp_path)
    created = store.save(person())
    point, _ = store.record(created['id'], touch())
    corrected = store.save(person(notes='New note', revision=1), created['id'])
    assert corrected['revision'] == 2
    assert store.touchpoints(created['id']) == [point]
    with pytest.raises(PeopleError) as failure:
        store.save(person(notes='Stale note', revision=1), created['id'])
    assert failure.value.status == 409
    assert store.get(created['id'])['notes'] == 'New note'
    with pytest.raises(PeopleError) as failure:
        store.save(person(), created['id'])
    assert failure.value.status == 400
    assert PeopleStore(tmp_path).touchpoints(created['id']) == [point]


def test_touchpoint_replay_and_source_conflicts(tmp_path):
    store = PeopleStore(tmp_path)
    first = store.save(person())
    second = store.save(person(name='Other', identities=[]))
    point, created = store.record(first['id'], touch())
    assert created is True
    assert point['occurred_at'] == '2026-09-01T08:00:00+00:00'
    assert point['person_id'] == first['id']
    replay, created = PeopleStore(tmp_path).record(first['id'], touch(occurred_at='2026-09-01T08:00:00Z'))
    assert replay == point
    assert created is False
    assert store.touchpoints(first['id']) == [point]
    for changes in ({'summary': 'Changed'}, {'direction': 'inbound'}, {'occurred_at': '2026-09-02T08:00:00Z'}):
        with pytest.raises(PeopleError) as failure:
            store.record(first['id'], touch(**changes))
        assert failure.value.status == 409
    with pytest.raises(PeopleError) as failure:
        store.record(second['id'], touch())
    assert failure.value.status == 409
    assert store.touchpoints(second['id']) == []
    different, created = store.record(first['id'], touch(source='calendar'))
    assert created is True
    assert different['id'] != point['id']
    assert len(store.touchpoints(first['id'])) == 2


@pytest.mark.parametrize('change', [
    {'source': ''}, {'source': 'x' * 81}, {'external_id': ''}, {'external_id': 'x' * 501},
    {'occurred_at': '2026-09-01T10:00:00'}, {'occurred_at': 'yesterday'},
    {'occurred_at': None}, {'occurred_at': '2026-02-30T10:00:00Z'},
    {'direction': 'sideways'}, {'summary': 'x' * 2001}, {'summary': False},
    {'person_id': 'other'}, {'root': '/tmp'},
])
def test_invalid_touchpoint_does_not_persist(tmp_path, change):
    store = PeopleStore(tmp_path)
    created = store.save(person())
    with pytest.raises(PeopleError) as failure:
        store.record(created['id'], touch(**change))
    assert failure.value.status == 400
    assert store.touchpoints(created['id']) == []


def test_reference_checks(tmp_path):
    store = PeopleStore(tmp_path)
    for action in (lambda: store.get('missing'), lambda: store.touchpoints('missing'),
                   lambda: store.record('missing', touch()), lambda: store.save(person(revision=1), 'missing')):
        with pytest.raises(PeopleError) as failure:
            action()
        assert failure.value.status == 404
    assert store.people() == []


def test_care_timezone_boundaries_and_future_exclusion(tmp_path):
    store = PeopleStore(tmp_path)
    created = store.save(person(cadence_days=1))
    now = datetime(2026, 9, 3, 0, 30, tzinfo=timezone.utc)
    assert care(created, [], now=now) == {'state': 'missing', 'last_contact': None, 'days_since': None, 'days_overdue': None}
    point, _ = store.record(created['id'], touch(occurred_at='2026-09-01T23:30:00Z'))
    utc = care(created, [point], 'UTC', now)
    berlin = care(created, [point], 'Europe/Berlin', now)
    assert utc['days_since'] == 2
    assert utc['state'] == 'overdue'
    assert utc['days_overdue'] == 1
    assert berlin['days_since'] == 1
    assert berlin['state'] == 'current'
    assert berlin['days_overdue'] == 0
    future, _ = store.record(created['id'], touch(external_id='future', occurred_at='2027-01-01T00:00:00Z'))
    assert care(created, [point, future], 'UTC', now) == utc
    assert care(created, [future], 'UTC', now)['state'] == 'missing'
    external = {**created, 'ring': 'external'}
    assert care(external, [], now=now)['state'] == 'excluded'
    with pytest.raises(PeopleError):
        care(created, [point], 'Not/A_Zone', now)
    with pytest.raises(PeopleError):
        care(created, [point], now=datetime(2026, 1, 1))


def test_concurrent_retry_claim_and_optimistic_edit(tmp_path):
    store = PeopleStore(tmp_path)
    created = store.save(person())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: PeopleStore(tmp_path).record(created['id'], touch()), range(4)))
    assert sum(is_new for _, is_new in results) == 1
    assert len({row['id'] for row, _ in results}) == 1
    def edit(note):
        try:
            return PeopleStore(tmp_path).save(person(notes=note, revision=1), created['id'])
        except PeopleError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        edits = list(pool.map(edit, ['first', 'second']))
    assert sum(isinstance(item, dict) for item in edits) == 1
    assert 409 in edits
    winner = next(item for item in edits if isinstance(item, dict))
    assert store.get(created['id']) == winner
    assert len(store.touchpoints(created['id'])) == 1


def test_distinct_homes_and_subprocess_reload(tmp_path):
    left = PeopleStore(tmp_path / 'left')
    right = PeopleStore(tmp_path / 'right')
    first = left.save(person())
    assert right.people() == []
    other = right.save(person(notes='Other home'))
    assert other['id'] != first['id']
    assert left.get(first['id'])['notes'] == 'Preserve this note'
    with pytest.raises(PeopleError):
        right.get(first['id'])
    script = 'import json,sys; from pathlib import Path; from gideon.workspace.capabilities.communications import PeopleStore; print(json.dumps(PeopleStore(Path(sys.argv[1])).people()))'
    completed = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'left')], capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout) == [first]


def test_real_http_workflow(tmp_path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(tmp_path)
    async def journey():
        app = web.Application()
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f'http://127.0.0.1:{port}/api/capabilities/communications/people'
        try:
            async with ClientSession() as client:
                async with client.get(base) as response:
                    assert response.status == 200
                    assert (await response.json())['people'] == []
                async with client.get(base + '?timezone=Invalid/Zone') as response:
                    assert response.status == 400
                async with client.post(base, json=person()) as response:
                    assert response.status == 201
                    created = (await response.json())['person']
                url = base + '/' + created['id']
                async with client.post(url + '/touchpoints', json=touch()) as response:
                    assert response.status == 201
                    point = (await response.json())['touchpoint']
                async with client.post(url + '/touchpoints', json=touch()) as response:
                    assert response.status == 200
                    assert (await response.json()) == {'touchpoint': point, 'created': False}
                async with client.put(url, json=person(notes='HTTP edit', revision=1)) as response:
                    assert response.status == 200
                    assert (await response.json())['person']['revision'] == 2
                async with client.put(url, json=person(notes='stale', revision=1)) as response:
                    assert response.status == 409
                async with client.get(url + '?timezone=Europe/Berlin') as response:
                    assert response.status == 200
                    body = await response.json()
                    assert body['person']['notes'] == 'HTTP edit'
                    assert body['touchpoints'] == [point]
                    assert body['timezone'] == 'Europe/Berlin'
                async with client.get(base) as response:
                    body = await response.json()
                    assert len(body['people']) == 1
                    assert body['people'][0]['id'] == created['id']
                    assert body['people'][0]['care']['last_contact'] == point['occurred_at']
                async with client.get(base + '/missing') as response:
                    assert response.status == 404
                async with client.post(base, data='not-json', headers={'Content-Type': 'application/json'}) as response:
                    assert response.status == 400
                for forbidden in ('home', 'root', 'provider', 'model'):
                    async with client.post(base, json=person(**{forbidden: 'value'})) as response:
                        assert response.status == 400
                async with client.post(base, json=[]) as response:
                    assert response.status == 400
        finally:
            await runner.cleanup()
    try:
        asyncio.run(journey())
        records = PeopleStore(tmp_path / 'capabilities' / 'communications').people()
        assert len(records) == 1
        assert records[0]['notes'] == 'HTTP edit'
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous
