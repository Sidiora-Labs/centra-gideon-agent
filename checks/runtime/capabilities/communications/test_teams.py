import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications import teams


def source(store, **changes):
    body = {'name': 'Work Teams', 'owner_email': 'owner@example.com', 'credential_ref': 'MICROSOFT_GRAPH_WORK'}
    body.update(changes)
    return teams.save_source(store, body)


def person(store, graph_id='friend-1'):
    return store.save({'name': 'Friend', 'identities': [
        {'kind': 'email', 'value': 'friend@example.com'},
        {'kind': 'handle', 'value': 'microsoft:' + graph_id},
    ]})


def stamp(minutes=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class GraphService:
    def __init__(self):
        self.calls = []
        self.revoked = False

    async def handle(self, request):
        self.calls.append((request.path_qs, request.headers.get('Authorization')))
        if self.revoked:
            return web.json_response({'error': {'code': 'InvalidAuthenticationToken'}}, status=401)
        path = request.path
        page = request.query.get('page')
        origin = f'{request.scheme}://{request.host}'
        if path.endswith('/me'):
            return web.json_response({'id': 'owner-1', 'mail': 'owner@example.com', 'displayName': 'Owner'})
        if path.endswith('/me/chats') and not page:
            return web.json_response({'value': [{'id': 'chat-a'}], '@odata.nextLink': origin + '/v1.0/me/chats?page=2'})
        if path.endswith('/me/chats'):
            return web.json_response({'value': [{'id': 'chat-b'}]})
        if path.endswith('/me/joinedTeams'):
            return web.json_response({'value': [{'id': 'team-a', 'displayName': 'Engineering'}]})
        if path.endswith('/teams/team-a/channels'):
            return web.json_response({'value': [{'id': 'channel-a', 'displayName': 'General'}]})
        if path.endswith('/chats/chat-a/messages') and not page:
            return web.json_response({'value': [self.message('same-id', 'friend-1', '<p>Hello <b>owner</b></p>')],
                                      '@odata.nextLink': origin + '/v1.0/chats/chat-a/messages?page=2'})
        if path.endswith('/chats/chat-a/messages'):
            edited = self.message('edited', 'owner-1', 'Draft one')
            edited['lastModifiedDateTime'] = stamp(1)
            return web.json_response({'value': [edited]})
        if path.endswith('/chats/chat-b/messages'):
            return web.json_response({'value': [self.message('same-id', 'owner-1', 'Same ID, different chat')]})
        if path.endswith('/teams/team-a/channels/channel-a/messages'):
            root = self.message('root', 'friend-1', 'Channel root')
            root['attachments'] = [{'id': 'file-1', 'name': 'plan.pdf', 'contentType': 'reference'}]
            root['replies'] = [self.message('reply', 'owner-1', 'Channel reply')]
            return web.json_response({'value': [root]})
        return web.json_response({'error': {'path': path}}, status=404)

    @staticmethod
    def message(identifier, sender, body):
        return {'id': identifier, 'etag': identifier + '-v1', 'createdDateTime': stamp(5),
                'lastModifiedDateTime': stamp(4), 'deletedDateTime': None,
                'from': {'user': {'id': sender, 'displayName': 'Sender ' + sender}},
                'body': {'contentType': 'html', 'content': body}, 'attachments': []}


@pytest.fixture
async def graph_server(unused_tcp_port):
    service = GraphService()
    app = web.Application()
    app.router.add_get('/{tail:.*}', service.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', unused_tcp_port)
    await site.start()
    try:
        yield service, f'http://127.0.0.1:{unused_tcp_port}/v1.0/'
    finally:
        await runner.cleanup()


def test_source_validation_revision_and_secret_free_persistence(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    assert row['revision'] == 1
    assert row['sync'] == {'state': 'not_synced', 'coverage': 'unknown'}
    assert teams.sources(store) == [row]
    assert 'token' not in json.dumps(row).casefold()
    changed = teams.save_source(store, {**{key: row[key] for key in ('name', 'owner_email', 'credential_ref')},
                                       'name': 'Renamed', 'revision': 1}, row['id'])
    assert changed['revision'] == 2
    assert changed['name'] == 'Renamed'
    with pytest.raises(PeopleError, match='changed; reload'):
        teams.save_source(store, {'name': 'Stale', 'owner_email': row['owner_email'],
                                  'credential_ref': row['credential_ref'], 'revision': 1}, row['id'])
    with pytest.raises(PeopleError, match='immutable'):
        teams.save_source(store, {'name': 'Wrong', 'owner_email': 'else@example.com',
                                  'credential_ref': row['credential_ref'], 'revision': 2}, row['id'])
    for bad in ('', 'space key', '../secret', 'A-B'):
        with pytest.raises(PeopleError):
            source(store, credential_ref=bad)


@pytest.mark.asyncio
async def test_real_http_graph_sync_paginates_deduplicates_and_links_people(tmp_path, graph_server):
    service, base_url = graph_server
    store = PeopleStore(tmp_path)
    friend = person(store)
    configured = source(store)
    async with ClientSession() as session:
        client = teams.GraphClient('actual-local-bearer', base_url, session)
        result = await teams.sync(store, configured['id'], client)
    assert result['state'] == 'synced'
    assert result['coverage'] == 'complete_discovered_history'
    assert result['conversations_seen'] == 3
    assert result['messages_seen'] == 5
    rows = teams.messages(store, configured['id'])
    assert len(rows) == 5
    assert len({row['provenance_key'] for row in rows}) == 5
    assert len([row for row in rows if row['message_id'] == 'same-id']) == 2
    inbound = next(row for row in rows if row['message_id'] == 'same-id' and row['conversation_id'] == 'chat-a')
    assert inbound['body'] == 'Hello owner'
    assert inbound['person_id'] == friend['id']
    assert inbound['direction'] == 'inbound'
    own = next(row for row in rows if row['message_id'] == 'edited')
    assert own['direction'] == 'outbound'
    reply = next(row for row in rows if row['message_id'] == 'reply')
    assert reply['reply_to_id'] == 'root'
    channel = next(row for row in rows if row['message_id'] == 'root')
    assert channel['source_kind'] == 'channel'
    assert channel['team_id'] == 'team-a'
    assert channel['channel_id'] == 'channel-a'
    assert channel['attachments'] == [{'id': 'file-1', 'name': 'plan.pdf', 'content_type': 'reference'}]
    assert all(auth == 'Bearer actual-local-bearer' for _, auth in service.calls)
    assert any('page=2' in path for path, _ in service.calls)
    persisted = teams.get_source(store, configured['id'])
    assert persisted['sync']['owner_graph_id'] == 'owner-1'
    assert 'actual-local-bearer' not in store.path.read_bytes().decode('latin1')


@pytest.mark.asyncio
async def test_repeat_sync_updates_message_without_duplicate(tmp_path, graph_server):
    service, base_url = graph_server
    store = PeopleStore(tmp_path)
    person(store)
    configured = source(store)
    async with ClientSession() as session:
        client = teams.GraphClient('token', base_url, session)
        await teams.sync(store, configured['id'], client)
        original = teams.messages(store, configured['id'])
        original_count = len(original)
        old_message = GraphService.message

        def revised(identifier, sender, body):
            row = old_message(identifier, sender, 'Edited in Teams' if identifier == 'edited' else body)
            if identifier == 'edited':
                row['etag'] = 'edited-v2'
            return row

        service.message = revised
        await teams.sync(store, configured['id'], client)
    current = teams.messages(store, configured['id'])
    assert len(current) == original_count
    edited = next(row for row in current if row['message_id'] == 'edited')
    assert edited['body'] == 'Edited in Teams'
    assert edited['etag'] == 'edited-v2'


@pytest.mark.asyncio
async def test_verified_customer_identity_is_required_before_history(tmp_path, graph_server):
    service, base_url = graph_server
    store = PeopleStore(tmp_path)
    configured = source(store, owner_email='different@example.com')
    async with ClientSession() as session:
        with pytest.raises(PeopleError, match='does not match') as error:
            await teams.sync(store, configured['id'], teams.GraphClient('token', base_url, session))
    assert error.value.status == 409
    assert [path for path, _ in service.calls] == ['/v1.0/me?$select=id,mail,userPrincipalName,displayName']
    assert teams.messages(store, configured['id']) == []
    assert teams.get_source(store, configured['id'])['sync']['state'] == 'failed'


@pytest.mark.asyncio
async def test_revocation_and_missing_credentials_are_durable_truthful_states(tmp_path, graph_server, monkeypatch):
    service, base_url = graph_server
    store = PeopleStore(tmp_path)
    configured = source(store)
    service.revoked = True
    async with ClientSession() as session:
        with pytest.raises(PeopleError) as revoked:
            await teams.sync(store, configured['id'], teams.GraphClient('expired', base_url, session))
    assert revoked.value.status == 401
    state = teams.get_source(store, configured['id'])['sync']
    assert state['state'] == 'revoked'
    assert state['coverage'] == 'unknown'
    assert 'expired' not in json.dumps(state)
    monkeypatch.setattr(teams, 'get_credential', lambda _: '')
    with pytest.raises(PeopleError, match='credential is unavailable') as missing:
        await teams.sync(store, configured['id'])
    assert missing.value.status == 503
    assert teams.get_source(store, configured['id'])['sync']['state'] == 'failed'


@pytest.mark.asyncio
async def test_continuation_cannot_leave_verified_graph_origin(tmp_path, unused_tcp_port):
    async def first(request):
        if request.path.endswith('/me'):
            return web.json_response({'id': 'owner-1', 'mail': 'owner@example.com'})
        if request.path.endswith('/me/chats'):
            return web.json_response({'value': [], '@odata.nextLink': 'https://attacker.invalid/stolen'})
        return web.json_response({'value': []})
    app = web.Application()
    app.router.add_get('/{tail:.*}', first)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', unused_tcp_port).start()
    try:
        store = PeopleStore(tmp_path)
        configured = source(store)
        async with ClientSession() as session:
            client = teams.GraphClient('token', f'http://127.0.0.1:{unused_tcp_port}/v1.0/', session)
            with pytest.raises(PeopleError, match='changed origin'):
                await teams.sync(store, configured['id'], client)
    finally:
        await runner.cleanup()
    assert teams.get_source(store, configured['id'])['sync']['state'] == 'failed'


@pytest.mark.asyncio
async def test_dashboard_source_messages_and_sync_failure_journey(tmp_path, monkeypatch, unused_tcp_port):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.setattr(teams, 'get_credential', lambda _: '')
    app = web.Application()
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', unused_tcp_port).start()
    root = f'http://127.0.0.1:{unused_tcp_port}/api/capabilities/communications/teams/sources'
    try:
        async with ClientSession() as client:
            response = await client.post(root, json={'name': 'HTTP Teams', 'owner_email': 'owner@example.com', 'credential_ref': 'GRAPH_HTTP'})
            assert response.status == 201
            configured = (await response.json())['source']
            listed = await (await client.get(root)).json()
            assert listed['sources'] == [configured]
            detail = await (await client.get(root + '/' + configured['id'] + '/messages')).json()
            assert detail['messages'] == []
            failed = await client.post(root + '/' + configured['id'] + '/sync', json={})
            assert failed.status == 503
            body = await failed.json()
            assert 'credential is unavailable' in body['error']
            refreshed = await (await client.get(root + '/' + configured['id'])).json()
            assert refreshed['source']['sync']['state'] == 'failed'
    finally:
        await runner.cleanup()


def test_message_parser_rejects_invalid_timestamps_and_bounds_metadata(tmp_path):
    store = PeopleStore(tmp_path)
    people = [person(store)]
    raw = GraphService.message('bad', 'friend-1', 'body')
    raw['createdDateTime'] = 'not-a-timestamp'
    with pytest.raises(PeopleError, match='ISO timestamp'):
        teams._normalize(raw, source_kind='chat', conversation_id='chat-a', owner_id='owner-1', people=people)
    raw = GraphService.message('ok', 'friend-1', 'body')
    raw['attachments'] = [{'id': str(index), 'name': 'file', 'contentType': 'reference'} for index in range(150)]
    row = teams._normalize(raw, source_kind='chat', conversation_id='chat-a', owner_id='owner-1', people=people)
    assert len(row['attachments']) == 100
    assert row['provider'] == 'microsoft_graph'
    assert row['person_id'] == people[0]['id']
