import asyncio
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications.imports import commit, preview


def source(content='name,email,notes\nExample Friend,friend@example.com,Imported note', format='csv'):
    return {'format': format, 'content': content}


def decision(row_id='1', action='create', **extra):
    return {'row_id': row_id, 'action': action, **extra}


def request(store, data=None, decisions=None):
    data = data or source()
    projected = preview(store, data)
    return {**data, 'source_digest': projected['source_digest'],
            'decisions': decisions if decisions is not None else [decision(row['row_id']) for row in projected['rows']]}


def test_csv_preview_is_read_only_and_normalizes(tmp_path):
    store = PeopleStore(tmp_path)
    data = source('name,email,phone,handle,notes\n"Family, Friend",FRIEND@EXAMPLE.COM|other@example.com,+1 (202) 555-0123,ExampleHandle,"Line one\nLine two"')
    projected = preview(store, data)
    assert store.people() == []
    assert len(projected['source_digest']) == 64
    assert projected == preview(PeopleStore(tmp_path), data)
    assert len(projected['rows']) == 1
    row = projected['rows'][0]
    assert row['row_id'] == '1'
    assert row['name'] == 'Family, Friend'
    assert row['matches'] == []
    assert row['error'] == ''
    assert row['candidate']['notes'] == 'Line one\nLine two'
    assert row['candidate']['identities'] == [{'kind': 'email', 'value': 'friend@example.com'},
                                             {'kind': 'email', 'value': 'other@example.com'},
                                             {'kind': 'phone', 'value': '+12025550123'},
                                             {'kind': 'handle', 'value': 'ExampleHandle'}]
    assert row['candidate']['ring'] == 'tribe'
    assert row['candidate']['cadence_days'] == 30


def test_csv_import_preserves_provenance_and_replays(tmp_path):
    store = PeopleStore(tmp_path)
    payload = request(store)
    receipt, created = commit(store, payload)
    assert created is True
    assert receipt['source_digest'] == payload['source_digest']
    assert receipt['format'] == 'csv'
    assert receipt['committed_at'].endswith('+00:00')
    assert receipt['decisions'] == [decision()]
    assert receipt['source_rows'][0]['notes'] == 'Imported note'
    assert len(receipt['rows']) == 1
    person_id = receipt['rows'][0]['person_id']
    assert receipt['rows'][0]['revision'] == 1
    assert receipt['rows'][0]['action'] == 'create'
    assert store.get(person_id)['name'] == 'Example Friend'
    assert store.get(person_id)['notes'] == 'Imported note'
    reopened = PeopleStore(tmp_path)
    replay, created = commit(reopened, payload)
    assert created is False
    assert replay == receipt
    assert len(reopened.people()) == 1
    with sqlite3.connect(store.path) as db:
        row = db.execute('SELECT receipt FROM contact_imports').fetchone()
    assert json.loads(row[0]) == receipt
    changed = {**payload, 'decisions': [decision(action='skip')]}
    with pytest.raises(PeopleError) as failure:
        commit(store, changed)
    assert failure.value.status == 409
    assert store.get(person_id)['revision'] == 1


def test_explicit_update_unions_identities_preserves_person(tmp_path):
    store = PeopleStore(tmp_path)
    original = store.save({'name': 'Existing Name', 'notes': 'Original note', 'ring': 'support', 'cadence_days': 2,
                           'identities': [{'kind': 'email', 'value': 'friend@example.com'}]})
    data = source('name,email,phone,notes\nDifferent Name,FRIEND@EXAMPLE.COM,+12025550123,Replace this?')
    projected = preview(store, data)
    assert projected['rows'][0]['matches'] == [{'id': original['id'], 'name': 'Existing Name', 'revision': 1}]
    payload = request(store, data, [decision(action='update', person_id=original['id'], revision=1)])
    receipt, created = commit(store, payload)
    assert created is True
    assert receipt['rows'][0]['person_id'] == original['id']
    updated = store.get(original['id'])
    assert updated['name'] == 'Existing Name'
    assert updated['notes'] == 'Original note'
    assert updated['ring'] == 'support'
    assert updated['cadence_days'] == 2
    assert updated['revision'] == 2
    assert updated['identities'] == [{'kind': 'email', 'value': 'friend@example.com'}, {'kind': 'phone', 'value': '+12025550123'}]
    assert len(store.people()) == 1
    assert commit(PeopleStore(tmp_path), payload) == (receipt, False)
    assert store.get(original['id'])['revision'] == 2


def test_stale_update_rolls_back_new_people_and_receipt(tmp_path):
    store = PeopleStore(tmp_path)
    original = store.save({'name': 'Existing', 'identities': [{'kind': 'email', 'value': 'friend@example.com'}]})
    data = source('name,email\nNew Person,new@example.com\nExisting,friend@example.com')
    payload = request(store, data, [decision(), decision('2', 'update', person_id=original['id'], revision=1)])
    changed = store.save({'name': 'Changed', 'notes': 'Latest', 'identities': original['identities'], 'revision': 1}, original['id'])
    with pytest.raises(PeopleError) as failure:
        commit(store, payload)
    assert failure.value.status == 409
    assert store.people() == [changed]
    payload['decisions'][1]['revision'] = 2
    receipt, created = commit(store, payload)
    assert created is True
    assert len(receipt['rows']) == 2
    assert len(store.people()) == 2
    assert store.get(original['id'])['notes'] == 'Latest'


def test_ambiguity_is_visible_and_never_merges_people(tmp_path):
    store = PeopleStore(tmp_path)
    left = store.save({'name': 'Left', 'identities': [{'kind': 'email', 'value': 'left@example.com'}]})
    right = store.save({'name': 'Right', 'identities': [{'kind': 'email', 'value': 'right@example.com'}]})
    data = source('name,email\nAmbiguous,left@example.com|right@example.com')
    projected = preview(store, data)
    assert {m['id'] for m in projected['rows'][0]['matches']} == {left['id'], right['id']}
    for choices in ([decision()], [decision(action='update', person_id=left['id'], revision=1)]):
        with pytest.raises(PeopleError) as failure:
            commit(store, request(store, data, choices))
        assert failure.value.status == 409
    assert store.get(left['id']) == left
    assert store.get(right['id']) == right
    receipt, created = commit(store, request(store, data, [decision(action='skip')]))
    assert created is True
    assert receipt['rows'] == [{'row_id': '1', 'action': 'skip', 'person_id': None}]
    assert len(store.people()) == 2


def test_duplicate_source_rows_require_selected_skip(tmp_path):
    store = PeopleStore(tmp_path)
    data = source('name,email\nFirst,friend@example.com\nSecond,FRIEND@EXAMPLE.COM')
    with pytest.raises(PeopleError) as failure:
        commit(store, request(store, data))
    assert failure.value.status == 409
    assert store.people() == []
    receipt, _ = commit(store, request(store, data, [decision(), decision('2', 'skip')]))
    assert len(store.people()) == 1
    assert store.people()[0]['name'] == 'First'
    assert receipt['rows'][1]['person_id'] is None


def test_invalid_rows_can_be_reviewed_and_skipped(tmp_path):
    store = PeopleStore(tmp_path)
    data = source('name,email\nValid,valid@example.com\nInvalid,not-an-email')
    rows = preview(store, data)['rows']
    assert rows[0]['error'] == ''
    assert rows[1]['error'] == 'Invalid email identity'
    assert rows[1]['candidate'] is None
    with pytest.raises(PeopleError):
        commit(store, request(store, data))
    assert store.people() == []
    receipt, _ = commit(store, request(store, data, [decision(), decision('2', 'skip')]))
    assert len(store.people()) == 1
    assert receipt['source_rows'][1]['identities'][0]['value'] == 'not-an-email'


def test_vcard_folded_fields_unicode_and_escapes(tmp_path):
    store = PeopleStore(tmp_path)
    content = 'BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Éxample\r\n Friend\r\nEMAIL;TYPE=HOME:EXAMPLE@EXAMPLE.COM\r\nTEL;VALUE=uri:tel:+12025550123\r\nNOTE:One\\nTwo\\,three\\;four\\\\five\r\nEND:VCARD\r\nBEGIN:VCARD\r\nVERSION:3.0\r\nFN:Second\r\nitem1.EMAIL:second@example.com\r\nEND:VCARD'
    data = source(content, 'vcard')
    rows = preview(store, data)['rows']
    assert len(rows) == 2
    assert rows[0]['name'] == 'ÉxampleFriend'
    assert rows[0]['candidate']['notes'] == 'One\nTwo,three;four\\five'
    assert rows[0]['candidate']['identities'][0]['value'] == 'example@example.com'
    assert rows[0]['candidate']['identities'][1]['value'] == '+12025550123'
    receipt, _ = commit(store, request(store, data))
    assert receipt['format'] == 'vcard'
    assert len(store.people()) == 2
    assert store.get(receipt['rows'][1]['person_id'])['name'] == 'Second'


@pytest.mark.parametrize('data', [
    source('email\none@example.com'), source('name,name\none,two'), source('name,secret\none,two'),
    source('name,email\nOne'), source('name,email\nOne,a@example.com,extra'), source('name\n"Unterminated'),
    source('name'), source('x', 'json'), source('BEGIN:VCARD\nVERSION:2.1\nFN:Old\nEND:VCARD', 'vcard'),
    source('BEGIN:VCARD\nFN:No version\nEND:VCARD', 'vcard'), source('FN:Outside', 'vcard'),
    source('BEGIN:VCARD\nVERSION:4.0\nFN:Unfinished', 'vcard'),
    source('BEGIN:VCARD\nBEGIN:VCARD', 'vcard'), source('BEGIN:VCARD\nBad', 'vcard'),
    source('BEGIN:VCARD\nVERSION:3.0\nFN;ENCODING=QUOTED-PRINTABLE:Encoded\nEND:VCARD', 'vcard'),
    source('name\n' + 'a' * 262144), source('name\n' + 'é' * 140000),
    source('name\n' + '\n'.join('Example' for _ in range(501))),
])
def test_malformed_or_excessive_import_is_refused(tmp_path, data):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError) as failure:
        preview(store, data)
    assert failure.value.status == 400
    assert store.people() == []


@pytest.mark.parametrize('changes', [
    {'source_digest': 'changed'}, {'decisions': []}, {'decisions': [{}]},
    {'decisions': [decision(action='merge')]}, {'decisions': [decision('2')]},
    {'decisions': [decision(person_id='injected')]}, {'root': '/tmp'},
])
def test_invalid_commit_contract_leaves_people_empty(tmp_path, changes):
    store = PeopleStore(tmp_path)
    payload = {**request(store), **changes}
    with pytest.raises(PeopleError):
        commit(store, payload)
    assert store.people() == []


def test_update_target_and_identity_bound_checks(tmp_path):
    store = PeopleStore(tmp_path)
    values = [{'kind': 'handle', 'value': f'handle-{n}'} for n in range(100)]
    original = store.save({'name': 'Full', 'identities': values})
    data = source('name,email\nNew,new@example.com')
    for target, revision, status in [('missing', 1, 404), (original['id'], True, 400), (original['id'], 1, 400)]:
        with pytest.raises(PeopleError) as failure:
            commit(store, request(store, data, [decision(action='update', person_id=target, revision=revision)]))
        assert failure.value.status == status
    assert store.get(original['id']) == original
    assert len(store.people()) == 1


def test_concurrent_same_import_is_one_commit(tmp_path):
    store = PeopleStore(tmp_path)
    payload = request(store)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: commit(PeopleStore(tmp_path), payload), range(3)))
    assert sum(created for _, created in results) == 1
    assert all(receipt == results[0][0] for receipt, _ in results)
    assert len(store.people()) == 1


def test_actual_http_preview_commit_and_receipt(tmp_path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(tmp_path)
    async def journey():
        app = web.Application()
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        origin = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        base = origin + '/api/capabilities/communications/import'
        try:
            async with ClientSession() as client:
                async with client.post(base + '/preview', json=source()) as response:
                    assert response.status == 200
                    projected = await response.json()
                    assert projected['rows'][0]['candidate']['name'] == 'Example Friend'
                async with client.get(origin + '/api/capabilities/communications/people') as response:
                    assert (await response.json())['people'] == []
                payload = {**source(), 'source_digest': projected['source_digest'], 'decisions': [decision()]}
                async with client.post(base + '/commit', json=payload) as response:
                    assert response.status == 201
                    body = await response.json()
                    assert body['created'] is True
                    receipt = body['receipt']
                async with client.post(base + '/commit', json=payload) as response:
                    assert response.status == 200
                    assert (await response.json()) == {'receipt': receipt, 'created': False}
                person_id = receipt['rows'][0]['person_id']
                async with client.get(origin + '/api/capabilities/communications/people/' + person_id) as response:
                    person = (await response.json())['person']
                    assert person['notes'] == 'Imported note'
                    assert person['identities'][0]['value'] == 'friend@example.com'
                async with client.post(base + '/commit', json={**payload, 'source_digest': 'wrong'}) as response:
                    assert response.status == 409
                async with client.post(base + '/preview', json={**source(), 'home': '/elsewhere'}) as response:
                    assert response.status == 400
        finally:
            await runner.cleanup()
    try:
        asyncio.run(journey())
        assert len(PeopleStore(tmp_path / 'capabilities' / 'communications').people()) == 1
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous
