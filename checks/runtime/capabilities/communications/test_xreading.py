import asyncio
import json
import os
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import ClientSession, web
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications import social, xreading
from gideon.workspace.capabilities.communications.tools import create_provider



@contextmanager
def runtime_home(path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous


def registration(store, **extra):
    return social.save(store, {'platform': 'x', 'handle': 'alice', 'request_key': 'alice', **extra})[0]


def draft(store, row, **extra):
    return xreading.save_draft(store, row['id'], {'text': 'A considered update & question?', 'request_key': 'draft1', **extra})


def test_real_draft_persistence_idempotency_and_content_identity(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = draft(store, row)
    assert saved['account_revision'] == 1
    assert saved['revision'] == 1
    assert saved['external_posted'] is False
    assert saved['state'] == 'draft'
    assert 'handoff_url' not in saved
    assert xreading.drafts(PeopleStore(tmp_path), row['id']) == [saved]
    assert draft(store, row) == saved
    with pytest.raises(PeopleError) as error:
        draft(store, row, text='Different')
    assert error.value.status == 409
    assert len(xreading.drafts(store, row['id'])) == 1


def test_review_handoff_encoding_and_explicit_confirmation(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = draft(store, row)
    with pytest.raises(PeopleError):
        xreading.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': False})
    reviewed = xreading.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': True})
    assert reviewed['state'] == 'reviewed_handoff'
    assert reviewed['external_posted'] is False
    url = urlsplit(reviewed['handoff_url'])
    assert url.scheme == 'https'
    assert url.hostname == 'twitter.com'
    assert url.path == '/intent/tweet'
    assert parse_qs(url.query)['text'] == [saved['text']]
    assert 'signed-in X account' in reviewed['browser_account_warning']
    assert reviewed['reviewed_at']
    assert xreading.drafts(PeopleStore(tmp_path), row['id'])[0] == reviewed


def test_edit_invalidates_review_and_stale_edit_cannot_overwrite(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = draft(store, row)
    xreading.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': True})
    edited = xreading.save_draft(store, row['id'], {'text': 'Revised text', 'revision': 1}, saved['id'])
    assert edited['revision'] == 2
    assert edited['state'] == 'draft'
    assert 'handoff_url' not in edited
    with pytest.raises(PeopleError) as error:
        xreading.save_draft(store, row['id'], {'text': 'Stale', 'revision': 1}, saved['id'])
    assert error.value.status == 409
    with pytest.raises(PeopleError):
        xreading.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': True})
    assert xreading.drafts(store, row['id'])[0]['text'] == 'Revised text'
    reviewed = xreading.review(store, row['id'], saved['id'], {'revision': 2, 'confirm_review': True})
    assert parse_qs(urlsplit(reviewed['handoff_url']).query)['text'] == ['Revised text']


def test_registration_revision_changes_invalidate_handoff_until_new_review(tmp_path):
    store = PeopleStore(tmp_path)
    row = registration(store)
    saved = draft(store, row)
    xreading.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': True})
    values = {key: row[key] for key in social.FIELDS}
    social.save(store, {**values, 'handle': 'bob', 'revision': 1}, row['id'])
    read = xreading.drafts(store, row['id'])[0]
    assert read['state'] == 'registration_changed'
    assert 'handoff_url' not in read
    with pytest.raises(PeopleError) as error:
        xreading.review(store, row['id'], saved['id'], {'revision': 1, 'confirm_review': True})
    assert error.value.status == 409
    updated = xreading.save_draft(store, row['id'], {'text': saved['text'], 'revision': 1}, saved['id'])
    assert updated['handle'] == 'bob'
    assert updated['account_revision'] == 2
    assert updated['revision'] == 2
    assert xreading.review(store, row['id'], saved['id'], {'revision': 2, 'confirm_review': True})['state'] == 'reviewed_handoff'


@pytest.mark.parametrize('data', [{'text': ''}, {'text': 'x' * 281}, {'home': 'elsewhere'}, {'text': None}, {'text': 4}])
def test_invalid_drafts_do_not_persist(tmp_path, data):
    store = PeopleStore(tmp_path)
    row = registration(store)
    with pytest.raises(PeopleError):
        draft(store, row, **data)
    assert xreading.drafts(store, row['id']) == []


@pytest.mark.parametrize('change', [{'platform': 'github'}, {'status': 'paused'}, {'handle': 'with.dots'}, {'handle': 'x' * 16}])
def test_only_active_valid_x_registration_can_read_or_compose(tmp_path, change):
    store = PeopleStore(tmp_path)
    row = registration(store, **change)
    with pytest.raises(PeopleError):
        draft(store, row)
    with pytest.raises(PeopleError):
        xreading.snapshot(store, row['id'])


def test_real_provider_page_normalization_and_explicit_partial_coverage():
    user = {'data': {'id': '123', 'username': 'Alice'}}
    page = {'data': [{'id': '456', 'text': 'Official post', 'created_at': '2026-09-25T09:00:00Z'}], 'meta': {'next_token': 'opaque-next'}}
    result = xreading.normalize_page(user, page)
    assert result['user_id'] == '123'
    assert result['username'] == 'Alice'
    assert result['next_token'] == 'opaque-next'
    assert result['coverage'] == 'available_page_only'
    assert result['posts'][0]['url'] == 'https://x.com/i/status/456'
    assert result['posts'][0]['text'] == 'Official post'
    assert xreading.normalize_page(user, {'meta': {'result_count': 0}})['posts'] == []
    partial = xreading.normalize_page(user, {**page, 'errors': [{'title': 'Partial data'}]})
    assert partial['coverage'] == 'partial'


@pytest.mark.parametrize('user,page', [(None, {}), ({'data': []}, {}), ({'data': {'id': 'bad', 'username': 'a'}}, {}), ({'data': {'id': '1', 'username': 'a'}}, {'data': {}}), ({'data': {'id': '1', 'username': 'a'}}, {'meta': {'next_token': 2}}), ({'data': {'id': '1', 'username': 'a'}}, {'data': [{'id': '1', 'text': 'a'}, {'id': '1', 'text': 'b'}]}), ({'data': {'id': '1', 'username': 'a'}}, {'data': [None]})])
def test_malformed_provider_pages_rejected(user, page):
    with pytest.raises(PeopleError):
        xreading.normalize_page(user, page)


def test_missing_real_credential_persists_unknown_failure_and_revision_binding(tmp_path):
    with runtime_home(tmp_path):
        store = PeopleStore()
        row = registration(store, credential_ref='ABSENT_X_READ_952D')
        assert xreading.snapshot(store, row['id'])['state'] == 'not_synced'
        failed = asyncio.run(xreading.sync(store, row['id'], {}))
        assert failed['state'] == 'failed'
        assert failed['coverage'] == 'unknown'
        assert failed['posts'] == []
        assert 'credential is unavailable' in failed['error']
        assert failed['captured_at']
        assert xreading.snapshot(PeopleStore(), row['id']) == failed
        values = {key: row[key] for key in social.FIELDS}
        social.save(store, {**values, 'revision': 1, 'handle': 'bob'}, row['id'])
        assert xreading.snapshot(store, row['id'])['state'] == 'registration_changed'
        assert xreading.snapshot(store, row['id'])['posts'] == []


def test_actual_native_x_draft_review_and_missing_read(tmp_path):
    with runtime_home(tmp_path):
        row = registration(PeopleStore())
        async def scenario():
            provider = create_provider()
            result = await provider.invoke('people_x_draft_create', {'account_id': row['id'], 'text': 'Native draft', 'request_key': 'native'})
            assert result.success, result.error
            saved = json.loads(result.output)['draft']
            result = await provider.invoke('people_x_review', {'account_id': row['id'], 'draft_id': saved['id'], 'revision': 1, 'confirm_review': True})
            assert json.loads(result.output)['draft']['external_posted'] is False
            result = await provider.invoke('people_x_draft_update', {'account_id': row['id'], 'draft_id': saved['id'], 'text': 'Edited native', 'revision': 1})
            assert json.loads(result.output)['draft']['state'] == 'draft'
            result = await provider.invoke('people_x_drafts', {'account_id': row['id']})
            assert len(json.loads(result.output)['drafts']) == 1
            result = await provider.invoke('people_x_sync', {'account_id': row['id']})
            assert json.loads(result.output)['snapshot']['state'] == 'failed'
            result = await provider.invoke('people_x_snapshot', {'account_id': row['id']})
            assert json.loads(result.output)['snapshot']['coverage'] == 'unknown'
            definitions = {tool.name: tool for tool in await provider.list_tools()}
            assert definitions['people_x_snapshot'].requires_approval is False
            assert definitions['people_x_review'].requires_approval is True
            assert definitions['people_x_sync'].requires_approval is True
        asyncio.run(scenario())


def test_actual_http_account_bound_draft_and_review(tmp_path):
    with runtime_home(tmp_path):
        row = registration(PeopleStore())
        async def scenario():
            app = web.Application()
            register(app)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/x/accounts/{row["id"]}'
            try:
                async with ClientSession() as client:
                    async with client.get(base + '/snapshot') as response:
                        assert (await response.json())['snapshot']['coverage'] == 'unknown'
                    async with client.post(base + '/sync', json={}) as response:
                        assert (await response.json())['snapshot']['state'] == 'failed'
                    async with client.post(base + '/drafts', json={'text': 'HTTP draft', 'request_key': 'http'}) as response:
                        assert response.status == 200
                        saved = (await response.json())['draft']
                    path = base + '/drafts/' + saved['id']
                    async with client.post(path + '/review', json={'revision': 1, 'confirm_review': False}) as response:
                        assert response.status == 400
                    async with client.post(path + '/review', json={'revision': 1, 'confirm_review': True}) as response:
                        reviewed = (await response.json())['draft']
                        assert reviewed['state'] == 'reviewed_handoff'
                        assert reviewed['external_posted'] is False
                    async with client.put(path, json={'text': 'Edited HTTP', 'revision': 1}) as response:
                        assert (await response.json())['draft']['revision'] == 2
                    async with client.post(path + '/review', json={'revision': 1, 'confirm_review': True}) as response:
                        assert response.status == 409
                    async with client.get(base + '/drafts') as response:
                        assert (await response.json())['drafts'][0]['state'] == 'draft'
            finally:
                await runner.cleanup()
        asyncio.run(scenario())
