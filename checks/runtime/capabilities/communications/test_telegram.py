import asyncio
import importlib
import json
import os
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from gideon.core.config.credentials import save_credential
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.integrations.notification_providers.base import NotificationDeliveryProvider
from gideon.integrations.notification_providers.registry import register_provider, route, unregister_provider
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications import telegram
from gideon.workspace.capabilities.communications.tools import create_provider


@pytest.fixture
def home(tmp_path):
    keys = ['GIDEON_HOME', 'GIDEON_CREDENTIAL_BACKEND', 'TG_TEST_WEBHOOK_78DA', 'TG_TEST_BOT_78DA']
    previous = {key: os.environ.get(key) for key in keys}
    os.environ['GIDEON_HOME'] = str(tmp_path)
    os.environ['GIDEON_CREDENTIAL_BACKEND'] = 'dotenv'
    save_credential('TG_TEST_WEBHOOK_78DA', 'local-test-secret')
    try:
        yield tmp_path
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def config(store, enabled=True, automatic=False):
    return telegram.configure(store, {'enabled': enabled, 'automatic_replies': automatic, 'bot_credential_ref': 'TG_TEST_BOT_78DA', 'webhook_credential_ref': 'TG_TEST_WEBHOOK_78DA', 'allowed_chat_ids': [12345, -999], 'allowed_user_ids': [12345], 'revision': telegram.config(store)['revision']})


def update(identifier=1, command='/care', chat=12345, user=12345):
    return {'update_id': identifier, 'message': {'message_id': identifier, 'chat': {'id': chat, 'type': 'private'}, 'from': {'id': user, 'is_bot': False}, 'text': command}}


def queue(store, key='notice-one', chat=12345, text='Notification body'):
    return telegram.queue(store, {'request_key': key, 'chat_id': chat, 'text': text})


def test_default_and_real_configuration_restart(home):
    store = PeopleStore()
    initial = telegram.config(store)
    assert initial['enabled'] is False
    assert initial['automatic_replies'] is False
    assert initial['revision'] == 0
    assert telegram.deliveries(store) == []
    saved = config(store)
    assert saved['revision'] == 1
    assert saved['allowed_chat_ids'] == [-999, 12345]
    assert saved['allowed_user_ids'] == [12345]
    assert 'local-test-secret' not in json.dumps(saved)
    assert telegram.config(PeopleStore()) == saved
    with pytest.raises(PeopleError) as error:
        telegram.configure(store, {**saved, 'revision': 0})
    assert error.value.status == 409
    assert telegram.config(store) == saved
    disabled = config(store, False)
    assert disabled['revision'] == 2
    assert disabled['enabled'] is False


@pytest.mark.parametrize('change', [{'home': '/outside'}, {'enabled': 'yes'}, {'automatic_replies': 1}, {'revision': True}, {'allowed_chat_ids': []}, {'allowed_user_ids': [True]}, {'allowed_chat_ids': [0]}, {'allowed_chat_ids': [2 ** 53]}, {'allowed_user_ids': list(range(1, 52))}, {'bot_credential_ref': 'raw-token:value'}, {'webhook_credential_ref': ''}])
def test_settings_validation(home, change):
    store = PeopleStore()
    valid = {'enabled': True, 'automatic_replies': False, 'bot_credential_ref': 'TG_TEST_BOT_78DA', 'webhook_credential_ref': 'TG_TEST_WEBHOOK_78DA', 'allowed_chat_ids': [12345], 'allowed_user_ids': [12345], 'revision': 0}
    with pytest.raises(PeopleError):
        telegram.configure(store, {**valid, **change})
    assert telegram.config(store)['revision'] == 0


def test_actual_command_projection_uses_people_and_care(home):
    store = PeopleStore()
    assert telegram.command(store, '/people')['text'] == 'No people recorded.'
    assert telegram.command(store, '/care')['text'] == 'No overdue or missing relationship touchpoints.'
    person = store.save({'name': 'Telegram Friend', 'ring': 'core'})
    result = telegram.command(store, '/people')
    assert result['text'] == 'Telegram Friend (core)'
    assert result['qualification'] == 'local_runtime_projection'
    care = telegram.command(store, '/care')
    assert care['text'] == 'Telegram Friend: missing'
    assert telegram.command(store, '/care@my_bot') == care
    status = telegram.command(store, '/status')
    assert 'People: 1.' in status['text']
    assert 'not live account coverage' in status['text']
    assert status['timezone']
    assert store.get(person['id'])['name'] == 'Telegram Friend'
    assert telegram.deliveries(store) == []
    for invalid in ['/delete', '/care extra', 'arbitrary instructions', '', None]:
        with pytest.raises(PeopleError):
            telegram.command(store, invalid)


def test_real_webhook_secret_allowlists_receipt_and_retry(home):
    store = PeopleStore()
    config(store)
    store.save({'name': 'Inbound Friend'})
    payload = update()
    receipt, created = telegram.receive(store, payload, 'local-test-secret')
    assert created is True
    assert receipt['command'] == '/care'
    assert receipt['chat_id'] == 12345
    assert receipt['automatic_replies'] is False
    assert receipt['result']['text'] == 'Inbound Friend: missing'
    deliveries = telegram.deliveries(store)
    assert len(deliveries) == 1
    assert deliveries[0]['state'] == 'queued'
    assert deliveries[0]['id'] == receipt['delivery_id']
    assert deliveries[0]['text'] == receipt['result']['text']
    assert deliveries[0]['message_id'] is None
    assert telegram.receive(PeopleStore(), payload, 'local-test-secret') == (receipt, False)
    assert telegram.deliveries(store) == deliveries
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM telegram_updates').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM telegram_deliveries').fetchone()[0] == 1


def test_replayed_command_keeps_original_result_after_people_change(home):
    store = PeopleStore()
    config(store)
    receipt, _ = telegram.receive(store, update(command='/people'), 'local-test-secret')
    assert receipt['result']['text'] == 'No people recorded.'
    store.save({'name': 'Added later'})
    assert telegram.receive(store, update(command='/people'), 'local-test-secret') == (receipt, False)
    fresh, created = telegram.receive(store, update(2, '/people'), 'local-test-secret')
    assert created is True
    assert fresh['result']['text'] == 'Added later (tribe)'
    assert len(telegram.deliveries(store)) == 2


@pytest.mark.parametrize('secret', [None, '', 'wrong-secret', 123])
def test_bad_webhook_secret_has_no_side_effect(home, secret):
    store = PeopleStore()
    config(store)
    with pytest.raises(PeopleError) as error:
        telegram.receive(store, update(), secret)
    assert error.value.status == 403
    assert telegram.deliveries(store) == []


@pytest.mark.parametrize('payload', [update(chat=444), update(user=444), {'update_id': 1, 'message': {'chat': {'id': 12345}, 'from': {'id': 12345, 'is_bot': True}, 'text': '/care'}}])
def test_untrusted_chat_sender_bot_rejected(home, payload):
    store = PeopleStore()
    config(store)
    with pytest.raises(PeopleError) as error:
        telegram.receive(store, payload, 'local-test-secret')
    assert error.value.status == 403
    assert telegram.deliveries(store) == []


@pytest.mark.parametrize('payload', [{'update_id': True}, {'update_id': -1}, {'update_id': 1, 'edited_message': {}}, update(command='/unsupported')])
def test_unsupported_update_is_not_a_success(home, payload):
    store = PeopleStore()
    config(store)
    with pytest.raises(PeopleError):
        telegram.receive(store, payload, 'local-test-secret')
    assert telegram.deliveries(store) == []


def test_changed_update_identity_conflicts_without_new_delivery(home):
    store = PeopleStore()
    config(store)
    receipt, _ = telegram.receive(store, update(), 'local-test-secret')
    with pytest.raises(PeopleError) as error:
        telegram.receive(store, update(command='/people'), 'local-test-secret')
    assert error.value.status == 409
    assert len(telegram.deliveries(store)) == 1
    assert telegram.deliveries(store)[0]['id'] == receipt['delivery_id']


def test_queue_idempotency_conflict_and_no_silent_send(home):
    store = PeopleStore()
    config(store)
    row, created = queue(store)
    assert created is True
    assert row['state'] == 'queued'
    assert row['config_revision'] == 1
    assert row['message_id'] is None
    assert queue(PeopleStore()) == (row, False)
    with pytest.raises(PeopleError) as error:
        queue(store, text='Changed payload')
    assert error.value.status == 409
    assert telegram.deliveries(store) == [row]
    with pytest.raises(PeopleError) as error:
        queue(store, key='other', chat=999)
    assert error.value.status == 403
    assert telegram.deliveries(store) == [row]


def test_unavailable_credentials_preserve_queued_attempt(home):
    store = PeopleStore()
    config(store)
    row, _ = queue(store)
    with pytest.raises(PeopleError) as error:
        telegram.deliver(store, row['id'])
    assert error.value.status == 503
    assert 'unavailable' in str(error.value)
    assert telegram.deliveries(store) == [row]
    save_credential('TG_TEST_BOT_78DA', 'invalid-format-not-a-token')
    with pytest.raises(PeopleError, match='invalid format'):
        telegram.deliver(store, row['id'])
    assert telegram.deliveries(store) == [row]
    config(store, False)
    with pytest.raises(PeopleError) as error:
        telegram.deliver(store, row['id'])
    assert error.value.status == 409
    assert telegram.deliveries(store)[0]['state'] == 'queued'


def test_actual_notification_sdk_factory_and_registry_uses_allowlist(home):
    store = PeopleStore()
    config(store)
    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / 'runtime/gideon/extensions/apps/native/gideon-telegram-ops/app.json').read_text())
    assert manifest['provider']['type'] == 'notification'
    module, factory = manifest['provider']['implementation'].split(':')
    provider = getattr(importlib.import_module(module), factory)({'home': '/ignored'})
    assert isinstance(provider, NotificationDeliveryProvider)
    assert provider.delivery_name == 'gideon-telegram-ops'
    assert provider.can_reach('telegram:12345') is True
    assert provider.can_reach('telegram:-999') is True
    assert provider.can_reach('telegram:444') is False
    assert provider.can_reach('email:someone') is False
    assert provider.can_reach('telegram:12345/other') is False
    assert provider.can_reach(None) is False
    register_provider(provider)
    try:
        notification = {'id': 'source-notice', 'addressee': 'telegram:12345', 'title': 'Actual notification', 'body': 'Queued from SDK'}
        assert route(notification) == ''
        rows = telegram.deliveries(store)
        assert len(rows) == 1
        assert rows[0]['state'] == 'queued'
        assert rows[0]['text'] == 'Actual notification\nQueued from SDK'
        assert route(notification) == ''
        assert telegram.deliveries(store) == rows
        assert route({**notification, 'addressee': 'telegram:555'}) == ''
        assert telegram.deliveries(store) == rows
    finally:
        unregister_provider(provider.delivery_name)
    config(store, False)
    assert provider.can_reach('telegram:12345') is False


def test_native_telegram_tools_use_real_store_and_approval_metadata(home):
    store = PeopleStore()
    settings = config(store)
    store.save({'name': 'Native Telegram Friend'})
    async def scenario():
        provider = create_provider()
        result = await provider.invoke('people_telegram_config', {})
        assert json.loads(result.output)['config'] == settings
        result = await provider.invoke('people_telegram_command', {'command': '/care'})
        assert result.success
        assert json.loads(result.output)['text'] == 'Native Telegram Friend: missing'
        result = await provider.invoke('people_telegram_queue', {'request_key': 'native', 'chat_id': 12345, 'text': 'Native notification'})
        assert result.success
        row = json.loads(result.output)['delivery']
        result = await provider.invoke('people_telegram_send', {'delivery_id': row['id'], 'confirm_send': True})
        assert not result.success
        assert result.metadata['status'] == 503
        result = await provider.invoke('people_telegram_deliveries', {})
        assert json.loads(result.output)['deliveries'][0]['state'] == 'queued'
        result = await provider.invoke('people_telegram_send', {'delivery_id': row['id'], 'confirm_send': False})
        assert not result.success
        result = await provider.invoke('people_telegram_configure', {**settings, 'enabled': False})
        assert json.loads(result.output)['config']['revision'] == 2
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions['people_telegram_send'].requires_approval is True
        assert definitions['people_telegram_configure'].risk_level.value == 'destructive'
        assert definitions['people_telegram_command'].requires_approval is False
    asyncio.run(scenario())


def test_http_authorized_webhook_and_operational_routes(home):
    store = PeopleStore()
    async def scenario():
        app = web.Application()
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/telegram'
        try:
            async with ClientSession() as client:
                async with client.get(base + '/config') as response:
                    assert (await response.json())['config']['enabled'] is False
                settings = config(store)
                async with client.put(base + '/config', json=settings) as response:
                    assert response.status == 200
                async with client.post(base + '/command', json={'command': '/people'}) as response:
                    assert (await response.json())['text'] == 'No people recorded.'
                async with client.post(base + '/webhook', json=update()) as response:
                    assert response.status == 403
                headers = {'X-Telegram-Bot-Api-Secret-Token': 'local-test-secret'}
                async with client.post(base + '/webhook', json=update(), headers=headers) as response:
                    assert response.status == 200
                    result = await response.json()
                    assert result['created'] is True
                    identity = result['receipt']['delivery_id']
                async with client.post(base + '/webhook', json=update(), headers=headers) as response:
                    assert (await response.json())['created'] is False
                async with client.get(base + '/deliveries') as response:
                    assert len((await response.json())['deliveries']) == 1
                async with client.post(base + '/deliveries/' + identity + '/send', json={'confirm_send': False}) as response:
                    assert response.status == 400
                async with client.post(base + '/deliveries/' + identity + '/send', json={'confirm_send': True}) as response:
                    assert response.status == 503
                async with client.post(base + '/deliveries', json={'request_key': 'http-notice', 'chat_id': 12345, 'text': 'HTTP notification'}) as response:
                    assert response.status == 201
        finally:
            await runner.cleanup()
    asyncio.run(scenario())
