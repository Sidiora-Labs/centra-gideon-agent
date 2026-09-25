import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleStore, mirrors
from gideon.workspace.capabilities.communications.outbound_email_tools import create_provider


def account_data():
    return {'name': 'Customer mail', 'kind': 'imap', 'owner_email': 'owner@example.com', 'alias': 'custom',
        'host': 'smtp.customer.example', 'username': 'owner@example.com', 'credential_ref': 'CUSTOMER_MAIL',
        'auth_mode': 'password', 'inbox_folder': 'INBOX', 'sent_folder': 'Sent'}


def draft_data(account_id, request_key='privacy-shared'):
    return {'request_key': request_key, 'account_id': account_id, 'to': ['privacyrequest@whitepages.com'],
        'subject': 'Privacy request', 'body': 'Remove the exact listed profile.'}


def test_central_route_and_native_provider_share_canonical_outbound_records(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    account = mirrors.save_account(PeopleStore(), account_data())

    async def exercise():
        app = web.Application(); register(app)
        client = TestClient(TestServer(app)); await client.start_server()
        try:
            response = await client.post('/api/capabilities/communications/outbound-email/drafts', json=draft_data(account['id']))
            assert response.status == 201
            created = (await response.json())['draft']
            response = await client.get('/api/capabilities/communications/outbound-email/drafts')
            assert (await response.json())['drafts'][0]['id'] == created['id']
        finally:
            await client.close()

        provider = create_provider()
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions['outbound_email_approve'].requires_approval is True
        assert definitions['outbound_email_send'].requires_approval is True
        result = await provider.invoke('outbound_email_draft', draft_data(account['id'], 'native-privacy'))
        assert result.success is True
        native = json.loads(result.output)['draft']
        listing = json.loads((await provider.invoke('outbound_email_list', {})).output)['drafts']
        assert native['sender'] == account['owner_email'] and {row['id'] for row in listing} == {created['id'], native['id']}

    asyncio.run(exercise())
