import asyncio
import base64
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

import pytest
from aiohttp import ClientSession, web
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications import desktop
from gideon.workspace.capabilities.communications.evidence import report
from gideon.workspace.capabilities.communications.tools import create_provider


def snapshot(source='imessage', messages=None, group=False):
    db = sqlite3.connect(':memory:')
    messages = messages if messages is not None else [('message-1', 'Imported desktop hello', 'incoming', 1700000000000)]
    if source == 'imessage':
        db.executescript('CREATE TABLE message (guid TEXT,date INTEGER,text TEXT,is_from_me INTEGER,handle_id INTEGER); CREATE TABLE handle (id TEXT); CREATE TABLE chat (guid TEXT); CREATE TABLE chat_message_join (message_id INTEGER,chat_id INTEGER);')
        db.execute('INSERT INTO handle VALUES (?)', ('+12025550123',))
        db.execute('INSERT INTO chat VALUES (?)', ('desktop-thread',))
        for identifier, body, kind, when in messages:
            apple = int((when / 1000 - 978307200) * 1000000000) if when else 0
            db.execute('INSERT INTO message VALUES (?,?,?,?,1)', (identifier, apple, body, int(kind == 'outgoing')))
            rowid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute('INSERT INTO chat_message_join VALUES (?,1)', (rowid,))
    else:
        db.executescript('CREATE TABLE messages (id TEXT,conversationId TEXT,sent_at INTEGER,received_at INTEGER,type TEXT,body TEXT,json TEXT); CREATE TABLE conversations (id TEXT,e164 TEXT,type TEXT,json TEXT);')
        db.execute('INSERT INTO conversations VALUES (?,?,?,?)', ('desktop-thread', '+12025550123', 'group' if group else 'private', '{}'))
        for identifier, body, kind, when in messages:
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)', (identifier, 'desktop-thread', when, when, kind, body, '{}'))
    db.commit()
    raw = db.serialize()
    db.close()
    return raw


def payload(source='imessage', raw=None, account='desktop-account'):
    return {'source': source, 'source_account_id': account, 'content_base64': base64.b64encode(raw or snapshot(source)).decode()}


def person(store):
    return store.save({'name': 'Desktop Friend', 'identities': [{'kind': 'phone', 'value': '+1 (202) 555-0123'}]})


def reviewed(store, data):
    preview = desktop.preview(store, data)
    return {**data, 'source_digest': preview['source_digest'], 'review_token': preview['review_token']}


@pytest.mark.parametrize('source', ['imessage', 'signal'])
def test_real_sqlite_preview_preserves_source_and_matches_people(tmp_path, source):
    store = PeopleStore(tmp_path)
    friend = person(store)
    data = payload(source)
    preview = desktop.preview(store, data)
    assert preview['source'] == source
    assert preview['source_account_id'] == 'desktop-account'
    assert preview['source_digest'] == hashlib.sha256(base64.b64decode(data['content_base64'])).hexdigest()
    assert len(preview['review_token']) == 64
    assert preview['coverage'] == 'snapshot_only'
    assert preview['qualification'] == 'uploaded_snapshot'
    assert preview['rows'][0]['person_id'] == friend['id']
    assert preview['rows'][0]['eligible'] is True
    assert preview['rows'][0]['direction'] == 'inbound'
    assert preview['rows'][0]['body'] == 'Imported desktop hello'
    assert preview['rows'][0]['occurred_at'] == '2023-11-14T22:13:20+00:00'
    assert preview['rows'][0]['identity'] == {'kind': 'phone', 'value': '+12025550123'}
    assert desktop.imports(store) == []
    assert desktop.history(store, source, 'desktop-account') == []
    assert store.touchpoints(friend['id']) == []


@pytest.mark.parametrize('source', ['imessage', 'signal'])
def test_atomic_commit_original_blob_restart_and_exact_retry(tmp_path, source):
    store = PeopleStore(tmp_path)
    friend = person(store)
    data = payload(source)
    request = reviewed(store, data)
    receipt, created = desktop.commit(store, request)
    assert created is True
    assert receipt['inserted'] == 1
    assert receipt['linked'] == 1
    assert receipt['messages'] == 1
    assert receipt['coverage'] == 'snapshot_only'
    assert desktop.imports(PeopleStore(tmp_path)) == [receipt]
    assert desktop.commit(PeopleStore(tmp_path), request) == (receipt, False)
    with store.connect() as db:
        raw = db.execute('SELECT raw FROM desktop_imports').fetchone()[0]
        assert raw == base64.b64decode(data['content_base64'])
        assert db.execute('SELECT COUNT(*) FROM relationship_messages').fetchone()[0] == 1
    rows = desktop.history(store, source, 'desktop-account')
    assert len(rows) == 1
    assert rows[0]['external_id'] == 'message-1'
    assert rows[0]['rowid'] == 1
    care = report(store)
    assert care['threads'][0]['state'] == 'unknown'
    assert care['threads'][0]['qualification'] == 'recorded_evidence_only'
    assert care['people'][0]['person']['id'] == friend['id']
    assert care['people'][0]['care']['state'] != 'missing'
    assert store.touchpoints(friend['id']) == []


def test_replay_after_contact_edit_remains_idempotent(tmp_path):
    store = PeopleStore(tmp_path)
    friend = person(store)
    request = reviewed(store, payload())
    receipt, _ = desktop.commit(store, request)
    store.save({'name': 'Renamed', 'identities': [], 'revision': 1}, friend['id'])
    assert desktop.commit(store, request) == (receipt, False)
    with pytest.raises(PeopleError) as error:
        desktop.commit(store, {**request, 'review_token': 'different'})
    assert error.value.status == 409
    assert desktop.imports(store) == [receipt]


def test_stale_review_contact_mapping_rolls_back(tmp_path):
    store = PeopleStore(tmp_path)
    request = reviewed(store, payload())
    person(store)
    with pytest.raises(PeopleError) as error:
        desktop.commit(store, request)
    assert error.value.status == 409
    assert desktop.imports(store) == []
    assert desktop.history(store, 'imessage', 'desktop-account') == []
    assert report(store)['threads'] == []
    fresh = reviewed(store, payload())
    receipt, _ = desktop.commit(store, fresh)
    assert receipt['linked'] == 1


def test_unmatched_history_is_preserved_without_invented_people(tmp_path):
    store = PeopleStore(tmp_path)
    data = payload('signal')
    preview = desktop.preview(store, data)
    assert preview['rows'][0]['person_id'] is None
    assert preview['rows'][0]['eligible'] is False
    receipt, _ = desktop.commit(store, reviewed(store, data))
    assert receipt['inserted'] == 1
    assert receipt['linked'] == 0
    assert store.people() == []
    assert report(store)['threads'] == []
    assert desktop.history(store, 'signal', 'desktop-account')[0]['body'] == 'Imported desktop hello'


def test_changed_source_message_conflict_is_atomic(tmp_path):
    store = PeopleStore(tmp_path)
    person(store)
    original = reviewed(store, payload('signal'))
    receipt, _ = desktop.commit(store, original)
    raw = snapshot('signal', [('message-1', 'Conflicting text', 'incoming', 1700000000000), ('message-2', 'New message', 'outgoing', 1700000001000)])
    changed = reviewed(store, payload('signal', raw))
    with pytest.raises(PeopleError) as error:
        desktop.commit(store, changed)
    assert error.value.status == 409
    assert desktop.imports(store) == [receipt]
    assert len(desktop.history(store, 'signal', 'desktop-account')) == 1
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM relationship_messages').fetchone()[0] == 1


def test_incremental_snapshot_has_stable_message_ids(tmp_path):
    store = PeopleStore(tmp_path)
    person(store)
    desktop.commit(store, reviewed(store, payload('signal')))
    raw = snapshot('signal', [('message-1', 'Imported desktop hello', 'incoming', 1700000000000), ('message-2', 'Actual reply', 'outgoing', 1700000001000)])
    receipt, _ = desktop.commit(store, reviewed(store, payload('signal', raw)))
    assert receipt['inserted'] == 1
    assert receipt['linked'] == 1
    assert len(desktop.history(store, 'signal', 'desktop-account')) == 2
    assert len(desktop.imports(store)) == 2
    assert report(store)['threads'][0]['state'] == 'unknown'
    assert report(store)['threads'][0]['latest']['direction'] == 'outbound'


def test_message_tombstone_removes_evidence_and_survives_restart_replay(tmp_path):
    store = PeopleStore(tmp_path)
    person(store)
    desktop.commit(store, reviewed(store, payload('signal')))
    exclusion, removed, created = desktop.exclude(store, {'source': 'signal', 'source_account_id': 'desktop-account',
                                                           'external_id': 'message-1', 'scope': 'message'})
    assert created is True
    assert removed == 1
    assert exclusion['scope'] == 'message'
    assert desktop.history(store, 'signal', 'desktop-account') == []
    assert report(store)['threads'] == []
    assert desktop.exclude(PeopleStore(tmp_path), {'source': 'signal', 'source_account_id': 'desktop-account',
                                                   'external_id': 'message-1', 'scope': 'message'}) == (exclusion, 0, False)
    replay = snapshot('signal', [('message-1', 'Imported desktop hello', 'incoming', 1700000000000),
                                 ('message-2', 'Allowed reply', 'outgoing', 1700000001000)])
    receipt, _ = desktop.commit(PeopleStore(tmp_path), reviewed(store, payload('signal', replay)))
    assert receipt['excluded'] == 1
    assert receipt['inserted'] == 1
    assert [row['external_id'] for row in desktop.history(store, 'signal', 'desktop-account')] == ['message-2']
    assert desktop.exclusions(PeopleStore(tmp_path), 'signal', 'desktop-account') == [exclusion]


def test_identity_block_removes_all_matching_history_and_is_source_scoped(tmp_path):
    store = PeopleStore(tmp_path)
    person(store)
    raw = snapshot('imessage', [('one', 'First', 'incoming', 1700000000000),
                                ('two', 'Second', 'outgoing', 1700000001000)])
    desktop.commit(store, reviewed(store, payload('imessage', raw)))
    exclusion, removed, created = desktop.exclude(store, {'source': 'imessage', 'source_account_id': 'desktop-account',
                                                           'external_id': 'one', 'scope': 'identity'})
    assert (removed, created) == (2, True)
    assert exclusion['identity'] == {'kind': 'phone', 'value': '+12025550123'}
    assert desktop.history(store, 'imessage', 'desktop-account') == []
    assert report(store)['threads'] == []
    other = payload('imessage', account='other-account')
    other_receipt, _ = desktop.commit(store, reviewed(store, other))
    assert other_receipt['excluded'] == 0
    assert len(desktop.history(store, 'imessage', 'other-account')) == 1
    expanded = snapshot('imessage', [('one', 'First', 'incoming', 1700000000000),
                                     ('three', 'Third', 'incoming', 1700000002000)])
    replay, _ = desktop.commit(store, reviewed(store, payload('imessage', expanded)))
    assert replay['excluded'] == 2
    assert replay['inserted'] == 0
    assert desktop.history(store, 'imessage', 'desktop-account') == []


def test_exclusion_validation_does_not_create_arbitrary_blocks(tmp_path):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError) as error:
        desktop.exclude(store, {'source': 'signal', 'source_account_id': 'desktop-account',
                                'external_id': 'missing', 'scope': 'message'})
    assert error.value.status == 404
    with pytest.raises(PeopleError):
        desktop.exclude(store, {'source': 'signal', 'source_account_id': 'desktop-account',
                                'external_id': 'missing', 'scope': 'conversation'})
    assert desktop.exclusions(store, 'signal', 'desktop-account') == []


@pytest.mark.parametrize('raw', [b'encrypted SQLCipher bytes', b'', b'SQLite format 3\x00' + b'bad' * 500])
def test_encrypted_or_damaged_snapshot_rejected(tmp_path, raw):
    store = PeopleStore(tmp_path)
    data = {'source': 'signal', 'source_account_id': 'account', 'content_base64': base64.b64encode(raw).decode()}
    with pytest.raises(PeopleError):
        desktop.preview(store, data)
    assert desktop.imports(store) == []


@pytest.mark.parametrize('extra', [{'source': 'telegram'}, {'home': '/outside'}, {'content_base64': '%%%bad'}, {'source_account_id': ''}, {'content_base64': 'A' * 11184813}])
def test_strict_snapshot_inputs(tmp_path, extra):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError):
        desktop.preview(store, {**payload(), **extra})
    assert desktop.imports(store) == []


def test_unexpected_and_view_schemas_rejected(tmp_path):
    store = PeopleStore(tmp_path)
    db = sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE unrelated (id TEXT); CREATE VIEW messages AS SELECT id FROM unrelated;')
    raw = db.serialize()
    db.close()
    with pytest.raises(PeopleError, match='ordinary table'):
        desktop.preview(store, payload('signal', raw))
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE messages (id TEXT)')
    raw = db.serialize()
    db.close()
    with pytest.raises(PeopleError, match='missing columns'):
        desktop.preview(store, payload('signal', raw))
    assert desktop.imports(store) == []


def test_signal_group_system_event_and_missing_text_limitations(tmp_path):
    store = PeopleStore(tmp_path)
    person(store)
    raw = snapshot('signal', [('event-1', None, 'group-v2-change', 0)], group=True)
    row = desktop.preview(store, payload('signal', raw))['rows'][0]
    assert row['identity'] is None
    assert row['person_id'] is None
    assert row['eligible'] is False
    assert row['direction'] is None
    assert row['occurred_at'] is None
    assert 'text_or_attachment_body_unavailable' in row['limitations']
    assert 'unsupported_event_or_timestamp' in row['limitations']
    receipt, _ = desktop.commit(store, reviewed(store, payload('signal', raw)))
    assert receipt['linked'] == 0
    assert receipt['inserted'] == 1


def test_oversized_message_count_and_duplicate_ids_rejected(tmp_path):
    store = PeopleStore(tmp_path)
    many = [('id-' + str(i), 'text', 'incoming', 1700000000000) for i in range(1001)]
    with pytest.raises(PeopleError, match='1000'):
        desktop.preview(store, payload('signal', snapshot('signal', many)))
    duplicate = [('same', 'text', 'incoming', 1700000000000)] * 2
    with pytest.raises(PeopleError, match='duplicate'):
        desktop.preview(store, payload('signal', snapshot('signal', duplicate)))
    assert desktop.imports(store) == []


def test_timestamp_versions_and_invalid_values():
    assert desktop.timestamp(721692800, 'imessage') == '2023-11-14T22:13:20+00:00'
    assert desktop.timestamp(721692800000000000, 'imessage') == '2023-11-14T22:13:20+00:00'
    assert desktop.timestamp(1700000000000, 'signal') == '2023-11-14T22:13:20+00:00'
    assert desktop.timestamp(None, 'signal') is None
    assert desktop.timestamp(-1, 'imessage') is None
    assert desktop.timestamp('invalid', 'signal') is None
    assert desktop.timestamp(10 ** 1000, 'signal') is None


def test_source_and_runtime_isolation(tmp_path):
    first = PeopleStore(tmp_path / 'first')
    second = PeopleStore(tmp_path / 'second')
    desktop.commit(first, reviewed(first, payload(account='one')))
    assert desktop.history(first, 'imessage', 'two') == []
    assert desktop.history(first, 'signal', 'one') == []
    assert desktop.imports(second) == []
    assert desktop.history(second, 'imessage', 'one') == []
    receipt, created = desktop.commit(first, reviewed(first, payload(account='two')))
    assert created is True
    assert receipt['inserted'] == 1
    assert len(desktop.imports(first)) == 2


def test_native_snapshot_tools_real_home(tmp_path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(tmp_path)
    async def scenario():
        provider = create_provider()
        data = payload('signal')
        result = await provider.invoke('people_desktop_preview', data)
        assert result.success, result.error
        preview = json.loads(result.output)
        request = {**data, 'source_digest': preview['source_digest'], 'review_token': preview['review_token']}
        result = await provider.invoke('people_desktop_commit', request)
        assert result.success, result.error
        assert json.loads(result.output)['created'] is True
        result = await provider.invoke('people_desktop_commit', request)
        assert json.loads(result.output)['created'] is False
        result = await provider.invoke('people_desktop_history', {'source': 'signal', 'source_account_id': 'desktop-account'})
        assert json.loads(result.output)['messages'][0]['external_id'] == 'message-1'
        result = await provider.invoke('people_desktop_imports', {})
        assert len(json.loads(result.output)['imports']) == 1
        result = await provider.invoke('people_desktop_exclude', {'source': 'signal', 'source_account_id': 'desktop-account', 'external_id': 'message-1', 'scope': 'message'})
        assert result.success, result.error
        assert json.loads(result.output)['removed'] == 1
        result = await provider.invoke('people_desktop_exclusions', {'source': 'signal', 'source_account_id': 'desktop-account'})
        assert len(json.loads(result.output)['exclusions']) == 1
        result = await provider.invoke('people_desktop_preview', {**data, 'root': '/outside'})
        assert not result.success
        assert result.metadata['status'] == 400
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions['people_desktop_commit'].requires_approval is True
        assert definitions['people_desktop_exclude'].requires_approval is True
        assert definitions['people_desktop_preview'].requires_approval is False
    try:
        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous


def test_http_snapshot_preview_commit_history(tmp_path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(tmp_path)
    async def scenario():
        app = web.Application(client_max_size=12 * 1024 * 1024)
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/desktop'
        try:
            async with ClientSession() as client:
                data = payload()
                async with client.post(base + '/preview', json=data) as response:
                    assert response.status == 200
                    preview = await response.json()
                request = {**data, 'source_digest': preview['source_digest'], 'review_token': preview['review_token']}
                async with client.post(base + '/commit', json=request) as response:
                    assert response.status == 201
                    receipt = (await response.json())['receipt']
                async with client.post(base + '/commit', json=request) as response:
                    assert response.status == 200
                    assert (await response.json())['created'] is False
                async with client.get(base + '/imports') as response:
                    assert (await response.json())['imports'] == [receipt]
                async with client.get(base + '/history', params={'source': 'imessage', 'source_account_id': 'desktop-account'}) as response:
                    assert len((await response.json())['messages']) == 1
                async with client.post(base + '/exclusions', json={'source': 'imessage', 'source_account_id': 'desktop-account', 'external_id': 'message-1', 'scope': 'message'}) as response:
                    assert response.status == 201
                    assert (await response.json())['removed'] == 1
                async with client.get(base + '/exclusions', params={'source': 'imessage', 'source_account_id': 'desktop-account'}) as response:
                    assert len((await response.json())['exclusions']) == 1
                async with client.get(base + '/history', params={'source': 'imessage', 'source_account_id': 'desktop-account'}) as response:
                    assert (await response.json())['messages'] == []
                async with client.post(base + '/commit', json={**request, 'source_digest': 'wrong'}) as response:
                    assert response.status == 409
                async with client.post(base + '/preview', json={**data, 'home': '/outside'}) as response:
                    assert response.status == 400
        finally:
            await runner.cleanup()
    try:
        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous


def test_repacked_snapshot_rowids_do_not_change_message_identity(tmp_path):
    store = PeopleStore(tmp_path)
    person(store)
    desktop.commit(store, reviewed(store, payload('signal')))
    reordered = snapshot('signal', [('new-first', 'New row before original', 'incoming', 1700000001000), ('message-1', 'Imported desktop hello', 'incoming', 1700000000000)])
    receipt, created = desktop.commit(store, reviewed(store, payload('signal', reordered)))
    assert created is True
    assert receipt['inserted'] == 1
    assert receipt['linked'] == 1
    rows = desktop.history(store, 'signal', 'desktop-account')
    assert len(rows) == 2
    assert next(row for row in rows if row['external_id'] == 'message-1')['rowid'] == 1
    assert len(desktop.imports(store)) == 2
