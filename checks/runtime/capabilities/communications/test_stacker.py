import asyncio
import json
import os
from contextlib import contextmanager
from gideon.integrations.tool_providers.base import RiskLevel

import pytest
from aiohttp import ClientSession, web
from gideon.core.config.credentials import save_credential
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications import social, stacker
from gideon.workspace.capabilities.communications.tools import create_provider



@contextmanager
def runtime_home(path):
    previous = os.environ.get('GIDEON_HOME')
    previous_backend = os.environ.get('GIDEON_CREDENTIAL_BACKEND')
    os.environ['GIDEON_HOME'] = str(path)
    os.environ['GIDEON_CREDENTIAL_BACKEND'] = 'dotenv'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous
        if previous_backend is None:
            os.environ.pop('GIDEON_CREDENTIAL_BACKEND', None)
        else:
            os.environ['GIDEON_CREDENTIAL_BACKEND'] = previous_backend


def registration(store, **extra):
    return social.save(store, {'platform': 'stackernews', 'handle': 'alice', 'request_key': 'alice', **extra})[0]


def prepare(store, row, **extra):
    return stacker.save(store, row['id'], {'kind': 'discussion', 'territory': 'bitcoin', 'title': 'A discussion', 'text': 'A useful question', 'request_key': 'discussion1', **extra})


def reviewed(store, row, **extra):
    draft = prepare(store, row, **extra)
    return stacker.review(store, row['id'], draft['id'], {'revision': 1, 'confirm_review': True})


def test_persistent_action_request_identity_and_restart(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = prepare(store, row)
    assert saved['state'] == 'draft'
    assert saved['external_execution'] == 'not_attempted'
    assert saved['account_revision'] == 1
    assert saved['revision'] == 1
    assert saved['created_at']
    assert stacker.actions(PeopleStore(tmp_path), row['id'])[0]['id'] == saved['id']
    assert prepare(store, row) == saved
    with pytest.raises(PeopleError) as error:
        prepare(store, row, text='Changed request')
    assert error.value.status == 409
    assert len(stacker.actions(store, row['id'])) == 1
    assert stacker.actions(store, row['id'])[0]['text'] == 'A useful question'


def test_explicit_review_contains_exact_payload_and_fee_warning(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = prepare(store, row)
    with pytest.raises(PeopleError):
        stacker.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': False})
    result = stacker.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': True})
    assert result['revision'] == 2
    assert result['state'] == 'reviewed'
    assert result['text'] == saved['text']
    assert result['title'] == saved['title']
    assert result['handoff_url'] == 'https://stacker.news/~bitcoin'
    assert 'charge provider-determined fees' in result['execution_warning']
    assert result['external_execution'] == 'not_attempted'
    with pytest.raises(PeopleError) as error:
        stacker.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': True})
    assert error.value.status == 409


def test_comment_and_zap_review_destinations_and_auth_boundary(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    comment = reviewed(store, row, kind='comment', item_id='123', request_key='comment')
    assert comment['handoff_url'] == 'https://stacker.news/items/123'
    query, variables = stacker.mutation(comment)
    assert 'upsertComment' in query
    assert 'parentId:$parent' in query
    assert variables == {'text': 'A useful question', 'parent': '123'}
    zap = reviewed(store, row, kind='zap', item_id='123', sats=21, text='', request_key='zap')
    assert zap['sats'] == 21
    assert zap['handoff_url'] == comment['handoff_url']
    with pytest.raises(PeopleError) as error:
        stacker.mutation(zap)
    assert error.value.status == 409
    assert 'prohibits API-key zaps' in str(error.value)
    with pytest.raises(PeopleError):
        asyncio.run(stacker.submit(store, row['id'], zap['id'], {'revision': 2, 'confirm_execute': True}))
    assert stacker.action(store, row['id'], zap['id'])['external_execution'] == 'not_attempted'


def test_actual_documented_discussion_mutation_variables_are_separate(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = prepare(store, row, title='Quotes " and braces }', text='mutation { not executable }')
    query, variables = stacker.mutation(saved)
    assert 'upsertDiscussion' in query
    assert 'subNames:$subs' in query
    assert variables['subs'] == ['bitcoin']
    assert variables['title'] == saved['title']
    assert saved['text'] not in query
    assert variables['text'] == saved['text']
    assert 'payInState' in query
    assert 'mcost' in query
    assert 'payerPrivates' in query


@pytest.mark.parametrize('change', [
    {'kind': 'unknown'}, {'territory': 'bad/name'}, {'territory': ''}, {'title': ''}, {'text': ''},
    {'kind': 'comment', 'item_id': ''}, {'kind': 'comment', 'item_id': '../abc'},
    {'kind': 'zap', 'item_id': '123', 'sats': 0}, {'sats': 1}, {'sats': True},
    {'kind': 'zap', 'item_id': '123', 'sats': 1000001}, {'home': '/other'},
])
def test_invalid_actions_never_persist(tmp_path, change):
    store = PeopleStore(tmp_path)
    row = registration(store)
    with pytest.raises(PeopleError):
        prepare(store, row, **change)
    assert stacker.actions(store, row['id']) == []


def test_identity_changes_disable_prior_review_and_account_isolation(tmp_path):
    first = PeopleStore(tmp_path / 'first')
    second = PeopleStore(tmp_path / 'second')
    row = registration(first)
    saved = reviewed(first, row)
    values = {key: row[key] for key in social.FIELDS}
    social.save(first, {**values, 'revision': 1, 'handle': 'bob'}, row['id'])
    current = stacker.actions(first, row['id'])[0]
    assert current['registration_changed'] is True
    assert 'handoff_url' not in current
    with pytest.raises(PeopleError) as error:
        stacker.transition(first, row['id'], saved['id'], 2, {'reviewed'}, {'state': 'submitting'})
    assert error.value.status == 409
    other = registration(second)
    assert stacker.actions(second, other['id']) == []
    with pytest.raises(PeopleError):
        stacker.action(second, other['id'], saved['id'])
    assert stacker.actions(first, row['id'])[0]['state'] == 'reviewed'


def test_missing_real_credentials_never_claim_submission(tmp_path):
    with runtime_home(tmp_path):
        store = PeopleStore()
        row = registration(store, credential_ref='ABSENT_STACKER_302D')
        saved = reviewed(store, row)
        with pytest.raises(PeopleError) as error:
            asyncio.run(stacker.submit(store, row['id'], saved['id'], {'revision': 2, 'confirm_execute': True}))
        assert error.value.status == 409
        assert 'credential is unavailable' in str(error.value)
        current = stacker.action(store, row['id'], saved['id'])
        assert current['state'] == 'reviewed'
        assert current['revision'] == 2
        assert current['external_execution'] == 'not_attempted'
        with pytest.raises(PeopleError):
            asyncio.run(stacker.submit(store, row['id'], saved['id'], {'revision': 2, 'confirm_execute': False}))
        with pytest.raises(PeopleError):
            asyncio.run(stacker.reconcile(store, row['id'], saved['id'], {}))
        assert 'payin_id' not in stacker.action(store, row['id'], saved['id'])


def test_real_transition_claim_excludes_duplicate_submission_and_records_unknown(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = reviewed(store, row)
    claim = stacker.transition(store, row['id'], saved['id'], 2, {'reviewed'}, {'state': 'submitting', 'external_execution': 'unknown'})
    assert claim['revision'] == 3
    with pytest.raises(PeopleError) as error:
        stacker.transition(PeopleStore(tmp_path), row['id'], saved['id'], 2, {'reviewed'}, {'state': 'submitting'})
    assert error.value.status == 409
    current = stacker.finish(store, claim, {'state': 'unknown', 'external_execution': 'unknown'})
    assert current['revision'] == 4
    assert current['state'] == 'unknown'
    with pytest.raises(PeopleError):
        stacker.finish(store, claim, {'state': 'provider_receipt'})
    with pytest.raises(PeopleError):
        stacker.transition(store, row['id'], saved['id'], 4, {'reviewed'}, {'state': 'submitting'})
    assert stacker.actions(store, row['id'])[0]['state'] == 'unknown'


@pytest.mark.parametrize('provider_state', ['PENDING', 'PENDING_INVOICE_CREATION', 'HELD', 'PAID', 'FAILED', 'CANCELLED', 'FORWARDED'])
def test_payin_normalizer_preserves_provider_state_without_claiming_execution(provider_state):
    result = stacker.payin({'id': 42, 'payInState': provider_state, 'mcost': '21000', 'item': {'id': '123'}, 'payerPrivates': {'payInFailureReason': None}})
    assert result['payin_id'] == 42
    assert result['provider_state'] == provider_state
    assert result['cost_msats'] == '21000'
    assert result['external_item_id'] == '123'
    assert result['external_execution'] == 'provider_reported'
    assert result['state'] == 'provider_receipt'
    assert 'paid' not in result
    assert 'posted' not in result


@pytest.mark.parametrize('data', [None, {}, {'id': True, 'payInState': 'PAID', 'mcost': 1}, {'id': 1, 'payInState': None, 'mcost': 1}, {'id': 1, 'payInState': 'PENDING', 'mcost': -1}, {'id': 1, 'payInState': 'PENDING', 'mcost': 1, 'item': []}])
def test_invalid_payin_receipts_do_not_imply_success(data):
    with pytest.raises(PeopleError):
        stacker.payin(data)


def test_actual_territory_schema_normalization_fees_cursor_and_page_scope():
    response = {'sub': {'name': 'bitcoin', 'desc': 'Bitcoin discussion', 'status': 'ACTIVE', 'baseCost': 100, 'replyCost': 1}, 'items': {'cursor': 'next-page', 'items': [{'id': '123', 'title': 'A post', 'text': 'Body'}]}}
    result = stacker.normalize_territory(response)
    assert result['name'] == 'bitcoin'
    assert result['base_cost_sats'] == 100
    assert result['reply_cost_sats'] == 1
    assert result['cursor'] == 'next-page'
    assert result['coverage'] == 'available_page_only'
    assert result['posts'][0]['url'] == 'https://stacker.news/items/123'
    assert result['posts'][0]['text'] == 'Body'
    empty = stacker.normalize_territory({**response, 'items': {'items': []}})
    assert empty['posts'] == []
    assert empty['coverage'] == 'available_page_only'
    assert empty['cursor'] is None
    with pytest.raises(PeopleError):
        stacker.normalize_territory({**response, 'sub': {**response['sub'], 'baseCost': -1}})
    with pytest.raises(PeopleError):
        stacker.normalize_territory({**response, 'items': {'items': [{}]}})
    with pytest.raises(PeopleError):
        stacker.normalize_territory({**response, 'items': {'items': [], 'cursor': 4}})


def test_loopback_graphql_protocol_covers_authenticated_submit_and_reconcile_without_external_action(tmp_path, monkeypatch):
    with runtime_home(tmp_path):
        save_credential('STACKER_LOOPBACK_77F2', 'loopback-api-key')
        store = PeopleStore()
        account = registration(store, credential_ref='STACKER_LOOPBACK_77F2')
        saved = reviewed(store, account)
        requests = []

        async def provider(request):
            payload = await request.json()
            requests.append(payload)
            query = payload['query']
            if ' me ' in query:
                assert request.headers.get('X-API-Key') == 'loopback-api-key'
                data = {'me': {'id': '7', 'name': 'alice'}}
            elif 'upsertDiscussion' in query:
                assert request.headers.get('X-API-Key') == 'loopback-api-key'
                assert payload['variables'] == {'title': 'A discussion', 'text': 'A useful question', 'subs': ['bitcoin']}
                data = {'result': {'id': 81, 'payInState': 'PENDING', 'mcost': '21000', 'item': {'id': '456'}, 'payerPrivates': {'payInFailureReason': None}}}
            elif 'payIn(id:$id)' in query:
                assert request.headers.get('X-API-Key') == 'loopback-api-key'
                assert payload['variables'] == {'id': 81}
                data = {'payIn': {'id': 81, 'payInState': 'PAID', 'mcost': '21000', 'item': {'id': '456'}, 'payerPrivates': {'payInFailureReason': None}}}
            else:
                assert request.headers.get('X-API-Key') is None
                assert payload['variables'] == {'name': 'bitcoin', 'cursor': None}
                data = {'sub': {'name': 'bitcoin', 'desc': 'Loopback territory', 'status': 'ACTIVE', 'baseCost': 100, 'replyCost': 1}, 'items': {'cursor': None, 'items': [{'id': '456', 'title': 'Local post', 'text': 'Protocol fixture'}]}}
            return web.json_response({'data': data})

        async def scenario():
            app = web.Application()
            app.router.add_post('/api/graphql', provider)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            monkeypatch.setattr(stacker, 'GRAPHQL_URL', f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/graphql')
            try:
                territory = await stacker.read_territory(store, {'name': 'bitcoin'})
                assert territory['posts'][0]['url'] == 'https://stacker.news/items/456'
                submitted = await stacker.submit(store, account['id'], saved['id'], {'revision': 2, 'confirm_execute': True})
                assert submitted['provider_state'] == 'PENDING'
                assert submitted['payin_id'] == 81
                reconciled = await stacker.reconcile(store, account['id'], saved['id'], {})
                assert reconciled['provider_state'] == 'PAID'
                assert reconciled['external_item_id'] == '456'
            finally:
                await runner.cleanup()

        asyncio.run(scenario())
        assert len(requests) == 4


def test_native_stacker_real_prepare_review_and_missing_credential(tmp_path):
    with runtime_home(tmp_path):
        store = PeopleStore()
        row = registration(store)
        async def scenario():
            provider = create_provider()
            result = await provider.invoke('people_stacker_prepare', {'account_id': row['id'], 'action': {'kind': 'discussion', 'territory': 'bitcoin', 'title': 'Native post', 'text': 'Native body', 'request_key': 'native'}})
            assert result.success, result.error
            saved = json.loads(result.output)['action']
            result = await provider.invoke('people_stacker_review', {'account_id': row['id'], 'action_id': saved['id'], 'revision': 1, 'confirm_review': True})
            assert json.loads(result.output)['action']['state'] == 'reviewed'
            result = await provider.invoke('people_stacker_submit', {'account_id': row['id'], 'action_id': saved['id'], 'revision': 2, 'confirm_execute': True})
            assert not result.success
            result = await provider.invoke('people_stacker_reconcile', {'account_id': row['id'], 'action_id': saved['id']})
            assert not result.success
            result = await provider.invoke('people_stacker_actions', {'account_id': row['id']})
            assert json.loads(result.output)['actions'][0]['external_execution'] == 'not_attempted'
            result = await provider.invoke('people_stacker_territories', {})
            assert json.loads(result.output)['territories'] == []
            result = await provider.invoke('people_stacker_read', {'name': '../invalid'})
            assert not result.success
            definitions = {tool.name: tool for tool in await provider.list_tools()}
            assert definitions['people_stacker_submit'].requires_approval
            assert definitions['people_stacker_submit'].risk_level == RiskLevel.DESTRUCTIVE
            assert definitions['people_stacker_territories'].requires_approval is False
        asyncio.run(scenario())


def test_actual_http_action_review_and_credentials_failure(tmp_path):
    with runtime_home(tmp_path):
        row = registration(PeopleStore())
        async def scenario():
            app = web.Application()
            register(app)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/stacker'
            try:
                async with ClientSession() as client:
                    async with client.get(base + '/territories') as response:
                        assert (await response.json())['territories'] == []
                    async with client.post(base + '/territories', json={'name': '../bad'}) as response:
                        assert response.status == 400
                    path = base + '/accounts/' + row['id'] + '/actions'
                    data = {'kind': 'comment', 'territory': 'bitcoin', 'item_id': '123', 'text': 'HTTP comment', 'request_key': 'http'}
                    async with client.post(path, json=data) as response:
                        assert response.status == 200
                        saved = (await response.json())['action']
                    action_path = path + '/' + saved['id']
                    async with client.post(action_path + '/review', json={'revision': 1, 'confirm_review': True}) as response:
                        assert (await response.json())['action']['state'] == 'reviewed'
                    async with client.post(action_path + '/submit', json={'revision': 2, 'confirm_execute': True}) as response:
                        assert response.status == 409
                    async with client.get(path) as response:
                        assert (await response.json())['actions'][0]['external_execution'] == 'not_attempted'
                    async with client.post(action_path + '/reconcile', json={}) as response:
                        assert response.status == 409
            finally:
                await runner.cleanup()
        asyncio.run(scenario())
