import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import ClientSession, web
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications import beeper
from gideon.workspace.capabilities.communications.tools import create_provider


def configure(store):
    return beeper.configure(store, {'base_url': 'http://127.0.0.1:23373', 'credential_ref': 'GIDEON_ABSENT_BEEPER_TEST_9BC2', 'revision': beeper.settings(store)['revision']})


def draft(store, key='request-one', body='A reviewed text', chat='chat-one'):
    return beeper.draft(store, {'request_key': key, 'chat_id': chat, 'text': body})[0]


def chats():
    return {'items': [{'id': 'chat-one', 'title': 'Source conversation', 'accountID': 'network-account'}], 'hasMore': True, 'oldestCursor': 'next-page'}


def messages():
    return {'items': [{'id': 'message-one', 'chatID': 'chat-one', 'accountID': 'network-account', 'text': 'Captured source text', 'attachments': [{'id': 'mxc://example.com/asset-one', 'fileName': 'attachment.txt', 'fileSize': 12}]}], 'hasMore': False}


def test_settings_revision_reference_and_restart(tmp_path):
    store = PeopleStore(tmp_path)
    initial = beeper.settings(store)
    assert initial['revision'] == 0
    assert initial['credential_ref'] == ''
    value = configure(store)
    assert value['revision'] == 1
    assert value['credential_ref'] == 'GIDEON_ABSENT_BEEPER_TEST_9BC2'
    assert 'token' not in value
    assert beeper.settings(PeopleStore(tmp_path)) == value
    with pytest.raises(PeopleError) as error:
        beeper.configure(store, {**value, 'revision': 0})
    assert error.value.status == 409
    assert beeper.settings(store) == value
    assert configure(store)['revision'] == 2


@pytest.mark.parametrize('url', ['https://example.com', 'http://example.com', 'file:///etc/passwd', 'http://user:password@localhost:23373', 'http://localhost:23373/path', 'http://localhost:23373?key=value', 'http://localhost:23373#fragment', 'http://localhost:99999', 'http://localhost:abc', 'http://127.0.0.2:23373'])
def test_oss_endpoint_boundary(url):
    with pytest.raises(PeopleError):
        beeper.endpoint(url)


@pytest.mark.parametrize('url', ['http://localhost:23373', 'http://127.0.0.1:23373/', 'http://[::1]:23373'])
def test_loopback_desktop_origins_supported(url):
    assert beeper.endpoint(url) == url.rstrip('/')


@pytest.mark.parametrize('change', [{'home': '/outside'}, {'credential_ref': 'token value'}, {'credential_ref': ''}, {'revision': True}, {'revision': -1}])
def test_invalid_configuration_never_persists(tmp_path, change):
    store = PeopleStore(tmp_path)
    data = {'base_url': 'http://127.0.0.1:23373', 'credential_ref': 'TOKEN_REF', 'revision': 0, **change}
    with pytest.raises(PeopleError):
        beeper.configure(store, data)
    assert beeper.settings(store)['revision'] == 0


def test_captured_page_normalization_and_persistence(tmp_path):
    store = PeopleStore(tmp_path)
    assert beeper.stored_page(store)['coverage'] == 'unknown'
    page = beeper.persist_page(store, chats(), provenance='imported_capture')
    assert page['coverage'] == 'available_page_only'
    assert page['provenance'] == 'imported_capture'
    assert page['hasMore'] is True
    assert page['oldestCursor'] == 'next-page'
    assert page['items'][0]['title'] == 'Source conversation'
    assert datetime.fromisoformat(page['observed_at']).tzinfo is not None
    assert beeper.stored_page(PeopleStore(tmp_path)) == page
    message_page = beeper.persist_page(store, messages(), 'chat-one', 'imported_capture')
    assert message_page['items'][0]['attachments'][0]['fileName'] == 'attachment.txt'
    assert beeper.stored_page(store, 'chat-two')['items'] == []
    assert beeper.stored_page(store, 'chat-one') == message_page
    assert beeper.stored_page(store) == page


@pytest.mark.parametrize('page', [{}, {'items': 'invalid'}, {'items': [1]}, {'items': [{}]}, {'items': [{'id': 'same'}, {'id': 'same'}]}, {'items': [{'id': str(i)} for i in range(501)]}])
def test_invalid_provider_pages_do_not_replace_cache(tmp_path, page):
    store = PeopleStore(tmp_path)
    existing = beeper.persist_page(store, chats(), provenance='imported_capture')
    with pytest.raises(PeopleError):
        beeper.persist_page(store, page)
    assert beeper.stored_page(store) == existing


def test_chat_scope_and_attachment_shape_validation(tmp_path):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError, match='another chat'):
        beeper.persist_page(store, messages(), 'chat-two')
    wrong = messages()
    wrong['items'][0]['attachments'] = 'not a list'
    with pytest.raises(PeopleError, match='attachment'):
        beeper.persist_page(store, wrong, 'chat-one')
    assert beeper.stored_page(store, 'chat-one')['items'] == []
    with pytest.raises(PeopleError, match='2 MiB'):
        beeper.persist_page(store, {'items': [{'id': 'large', 'text': 'x' * (2 * 1024 * 1024)}]})


def test_connection_change_invalidates_cached_pages_and_stale_fetch(tmp_path):
    store = PeopleStore(tmp_path)
    configured = configure(store)
    beeper.persist_page(store, chats(), expected_revision=configured['revision'])
    beeper.persist_page(store, messages(), 'chat-one', expected_revision=configured['revision'])
    queued = draft(store)
    configure(store)
    assert beeper.stored_page(store)['items'] == []
    assert beeper.stored_page(store, 'chat-one')['coverage'] == 'unknown'
    assert beeper.get_outbox(store, queued['id']) == queued
    with pytest.raises(PeopleError) as error:
        beeper.persist_page(store, chats(), expected_revision=configured['revision'])
    assert error.value.status == 409
    assert beeper.stored_page(store)['items'] == []
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.send(store, queued['id'], {'revision': 1, 'confirm_send': True}))
    assert error.value.status == 409
    assert beeper.get_outbox(store, queued['id'])['state'] == 'draft'


def test_disconnect_clears_provider_cache_and_invalidates_preserved_drafts(tmp_path):
    store = PeopleStore(tmp_path)
    configured = configure(store)
    beeper.persist_page(store, chats(), expected_revision=configured['revision'])
    beeper.persist_page(store, messages(), 'chat-one', expected_revision=configured['revision'])
    with store.connect() as db, db:
        beeper.schema(db)
        db.execute('INSERT INTO beeper_assets VALUES (?,?,?)', ('mxc://example.com/asset-one', b'cached bytes', 'digest'))
    queued = draft(store)
    disconnected = beeper.disconnect(store, {'revision': configured['revision']})
    assert disconnected == {'base_url': 'http://127.0.0.1:23373', 'credential_ref': '', 'revision': 2,
                            'connected': False, 'transport_mode': 'manual_refresh_only'}
    assert beeper.settings(PeopleStore(tmp_path)) == disconnected
    assert beeper.stored_page(store)['coverage'] == 'unknown'
    assert beeper.stored_page(store, 'chat-one')['items'] == []
    with pytest.raises(PeopleError) as error:
        beeper.asset(store, 'mxc://example.com/asset-one')
    assert error.value.status == 404
    assert beeper.get_outbox(store, queued['id']) == queued
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.send(store, queued['id'], {'revision': queued['revision'], 'confirm_send': True}))
    assert error.value.status == 409
    with pytest.raises(PeopleError) as error:
        beeper.disconnect(store, {'revision': configured['revision']})
    assert error.value.status == 409
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.refresh(store))
    assert error.value.status == 503


def test_draft_persists_idempotently_without_external_effect(tmp_path):
    store = PeopleStore(tmp_path)
    row = draft(store)
    assert row['state'] == 'draft'
    assert row['delivery'] == 'not_sent'
    assert row['pending_message_id'] is None
    assert row['revision'] == 1
    assert row['connection_revision'] == 0
    assert beeper.outbox(PeopleStore(tmp_path)) == [row]
    retry, created = beeper.draft(store, {'request_key': 'request-one', 'chat_id': 'chat-one', 'text': 'A reviewed text'})
    assert retry == row
    assert created is False
    with pytest.raises(PeopleError) as error:
        draft(store, body='Different text')
    assert error.value.status == 409
    assert beeper.outbox(store) == [row]


@pytest.mark.parametrize('data', [{}, {'request_key': 'key', 'chat_id': '', 'text': 'x'}, {'request_key': 'key', 'chat_id': 'chat', 'text': ' '}, {'request_key': 'key', 'chat_id': 'chat', 'text': 'x', 'send': True}])
def test_draft_validation(data, tmp_path):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError):
        beeper.draft(store, data)
    assert beeper.outbox(store) == []


def test_discard_uses_revision_and_does_not_retract(tmp_path):
    store = PeopleStore(tmp_path)
    row = draft(store)
    result = beeper.discard(store, row['id'], 1)
    assert result['state'] == 'discarded'
    assert result['delivery'] == 'not_retracted'
    assert result['revision'] == 2
    assert result['text'] == row['text']
    assert beeper.get_outbox(PeopleStore(tmp_path), row['id']) == result
    with pytest.raises(PeopleError) as error:
        beeper.discard(store, row['id'], 1)
    assert error.value.status == 409
    with pytest.raises(PeopleError):
        beeper.transition(store, row['id'], 2, {'draft'}, 'sending')


def test_parallel_claims_only_one_mutates_real_outbox(tmp_path):
    store = PeopleStore(tmp_path)
    row = draft(store)
    def claim():
        try:
            return beeper.transition(PeopleStore(tmp_path), row['id'], 1, {'draft'}, 'sending', delivery='unknown')['state']
        except PeopleError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert results.count('sending') == 1
    assert results.count(409) == 1
    saved = beeper.get_outbox(store, row['id'])
    assert saved['revision'] == 2
    assert saved['delivery'] == 'unknown'
    with pytest.raises(PeopleError):
        beeper.transition(store, row['id'], 2, {'draft'}, 'sending')


def test_interrupted_send_recovery_never_creates_retry(tmp_path):
    store = PeopleStore(tmp_path)
    row = draft(store)
    old = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    claimed = beeper.transition(store, row['id'], 1, {'draft'}, 'sending', delivery='unknown', started_at=old)
    recovered = beeper.recover(PeopleStore(tmp_path), row['id'], claimed['revision'])
    assert recovered['state'] == 'unknown'
    assert recovered['pending_message_id'] is None
    assert recovered['delivery'] == 'unknown'
    assert len(beeper.outbox(store)) == 1
    with pytest.raises(PeopleError):
        asyncio.run(beeper.reconcile(store, row['id'], recovered['revision']))
    assert beeper.discard(store, row['id'], recovered['revision'])['state'] == 'discarded'


def test_active_send_cannot_be_recovered_early(tmp_path):
    store = PeopleStore(tmp_path)
    row = draft(store)
    claimed = beeper.transition(store, row['id'], 1, {'draft'}, 'sending', started_at=datetime.now(timezone.utc).isoformat())
    with pytest.raises(PeopleError) as error:
        beeper.recover(store, row['id'], claimed['revision'])
    assert error.value.status == 409
    assert beeper.get_outbox(store, row['id']) == claimed


def test_real_client_unavailable_secret_does_not_claim_send(tmp_path):
    store = PeopleStore(tmp_path)
    configured = configure(store)
    assert configured['credential_ref'] not in os.environ
    row = draft(store)
    with pytest.raises(PeopleError, match='confirmation'):
        asyncio.run(beeper.send(store, row['id'], {'revision': 1, 'confirm_send': False}))
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.send(store, row['id'], {'revision': 1, 'confirm_send': True}))
    assert error.value.status == 503
    assert beeper.get_outbox(store, row['id']) == row
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.refresh(store))
    assert error.value.status == 503
    assert beeper.stored_page(store)['coverage'] == 'unknown'
    assert beeper.outbox(store) == [row]


def test_attachment_provenance_and_unavailable_connection(tmp_path):
    store = PeopleStore(tmp_path)
    configure(store)
    beeper.persist_page(store, messages(), 'chat-one', 'imported_capture')
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.fetch_asset(store, 'other-chat', 'mxc://example.com/asset-one'))
    assert error.value.status == 404
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.fetch_asset(store, 'chat-one', 'mxc://example.com/asset-one'))
    assert error.value.status == 503
    with pytest.raises(PeopleError) as error:
        beeper.asset(store, 'mxc://example.com/asset-one')
    assert error.value.status == 404
    page = messages()
    page['items'][0]['attachments'][0]['id'] = 'file:///etc/passwd'
    beeper.persist_page(store, page, 'chat-one', 'imported_capture')
    with pytest.raises(PeopleError, match='media identities'):
        asyncio.run(beeper.fetch_asset(store, 'chat-one', 'file:///etc/passwd'))


def test_runtime_isolation_for_settings_pages_outbox(tmp_path):
    first = PeopleStore(tmp_path / 'one')
    second = PeopleStore(tmp_path / 'two')
    configure(first)
    row = draft(first)
    beeper.persist_page(first, chats(), provenance='imported_capture')
    assert beeper.settings(second)['revision'] == 0
    assert beeper.outbox(second) == []
    assert beeper.stored_page(second)['coverage'] == 'unknown'
    with pytest.raises(PeopleError) as error:
        beeper.get_outbox(second, row['id'])
    assert error.value.status == 404
    assert beeper.get_outbox(first, row['id']) == row


def test_native_beeper_operations_and_send_policy(tmp_path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(tmp_path)
    async def scenario():
        provider = create_provider()
        config = {'base_url': 'http://127.0.0.1:23373', 'credential_ref': 'GIDEON_ABSENT_BEEPER_TEST_9BC2', 'revision': 0}
        result = await provider.invoke('people_beeper_configure', config)
        assert result.success, result.error
        assert json.loads(result.output)['settings']['revision'] == 1
        result = await provider.invoke('people_beeper_settings', {})
        assert json.loads(result.output)['settings']['credential_ref'] == config['credential_ref']
        result = await provider.invoke('people_beeper_draft', {'request_key': 'native-one', 'chat_id': 'native-chat', 'text': 'Native reviewed text'})
        assert result.success, result.error
        row = json.loads(result.output)['item']
        result = await provider.invoke('people_beeper_send', {'outbox_id': row['id'], 'revision': 1, 'confirm_send': True})
        assert not result.success
        assert result.metadata['status'] == 503
        result = await provider.invoke('people_beeper_outbox', {})
        assert json.loads(result.output)['outbox'][0]['state'] == 'draft'
        result = await provider.invoke('people_beeper_page', {})
        assert json.loads(result.output)['coverage'] == 'unknown'
        result = await provider.invoke('people_beeper_refresh', {})
        assert not result.success
        assert result.metadata['status'] == 503
        result = await provider.invoke('people_beeper_asset', {'asset_id': 'missing'})
        assert not result.success
        assert result.metadata['status'] == 404
        result = await provider.invoke('people_beeper_discard', {'outbox_id': row['id'], 'revision': 1})
        assert json.loads(result.output)['item']['state'] == 'discarded'
        result = await provider.invoke('people_beeper_draft', {'request_key': 'x', 'chat_id': 'x', 'text': 'x', 'home': '/outside'})
        assert not result.success
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions['people_beeper_send'].risk_level.value == 'destructive'
        assert definitions['people_beeper_send'].requires_approval is True
        assert definitions['people_beeper_draft'].requires_approval is True
        assert definitions['people_beeper_disconnect'].requires_approval is True
        assert definitions['people_beeper_page'].requires_approval is False
        result = await provider.invoke('people_beeper_disconnect', {'revision': 1})
        assert result.success, result.error
        assert json.loads(result.output)['settings']['connected'] is False
    try:
        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous


def test_real_http_beeper_draft_configuration_and_errors(tmp_path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(tmp_path)
    async def scenario():
        app = web.Application()
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/beeper'
        try:
            async with ClientSession() as client:
                async with client.get(base + '/settings') as response:
                    assert (await response.json())['settings']['revision'] == 0
                config = {'base_url': 'http://127.0.0.1:23373', 'credential_ref': 'GIDEON_ABSENT_BEEPER_TEST_9BC2', 'revision': 0}
                async with client.put(base + '/settings', json=config) as response:
                    assert response.status == 200
                async with client.put(base + '/settings', json=config) as response:
                    assert response.status == 409
                data = {'request_key': 'http-one', 'chat_id': 'chat-one', 'text': 'HTTP queued text'}
                async with client.post(base + '/outbox', json=data) as response:
                    assert response.status == 201
                    row = (await response.json())['item']
                async with client.post(base + '/outbox', json=data) as response:
                    assert response.status == 200
                    assert (await response.json())['created'] is False
                path = base + '/outbox/' + row['id']
                async with client.post(path + '/send', json={'revision': 1, 'confirm_send': True}) as response:
                    assert response.status == 503
                async with client.post(base + '/refresh', json={}) as response:
                    assert response.status == 503
                async with client.get(base + '/chats') as response:
                    assert (await response.json())['coverage'] == 'unknown'
                async with client.get(base + '/messages', params={'chat_id': 'chat-one'}) as response:
                    assert (await response.json())['items'] == []
                async with client.get(base + '/assets', params={'id': 'missing'}) as response:
                    assert response.status == 404
                async with client.post(path + '/discard', json={'revision': 1}) as response:
                    assert (await response.json())['item']['state'] == 'discarded'
                async with client.get(base + '/outbox') as response:
                    assert len((await response.json())['outbox']) == 1
                async with client.post(base + '/disconnect', json={'revision': 1}) as response:
                    assert response.status == 200
                    disconnected = (await response.json())['settings']
                    assert disconnected['connected'] is False
                    assert disconnected['credential_ref'] == ''
                async with client.get(base + '/settings') as response:
                    assert (await response.json())['settings'] == disconnected
        finally:
            await runner.cleanup()
    try:
        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous


def test_pending_reconciliation_cannot_switch_connections(tmp_path):
    store = PeopleStore(tmp_path)
    configure(store)
    row = draft(store)
    pending = beeper.transition(store, row['id'], 1, {'draft'}, 'pending', pending_message_id='recorded-pending-id', delivery='not_confirmed')
    configure(store)
    with pytest.raises(PeopleError) as error:
        asyncio.run(beeper.reconcile(store, row['id'], pending['revision']))
    assert error.value.status == 409
    assert beeper.get_outbox(store, row['id']) == pending
    discarded = beeper.discard(store, row['id'], pending['revision'])
    assert discarded['delivery'] == 'not_retracted'
    assert discarded['pending_message_id'] == 'recorded-pending-id'
