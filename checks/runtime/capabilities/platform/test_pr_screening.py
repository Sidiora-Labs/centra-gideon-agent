import copy
import json
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.core.config.loader import config_dir
from gideon.integrations.llm.credentials import CredentialStore
from gideon.interfaces.dashboard.handlers.capabilities_pr_screening import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_ephemeral_secret
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.platform import pr_screening as p
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/pr-screening'
REQUEST = {'repo': 'example/project', 'number': 7, 'credential': 'github-review', 'screen_provider': 'missing-screen', 'review_provider': 'missing-review'}


def source_documents():
    return ({'state': 'open', 'draft': False, 'head': {'sha': 'a' * 40}, 'base': {'sha': 'b' * 40}, 'title': 'Improve parser', 'body': 'Review the changed parser.', 'changed_files': 1, 'commits': 1}, [{'filename': 'parser.py', 'patch': '-old\n+new', 'status': 'modified'}], [{'sha': 'a' * 40, 'commit': {'message': 'Improve parser'}}])


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    CredentialStore(tmp_path).save({'github-review': {'type': 'static_token', 'value_env': 'GIDEON_TEST_PR_UNSET'}})
    monkeypatch.delenv('GIDEON_TEST_PR_UNSET', raising=False)
    return tmp_path


def captured():
    return p.record(REQUEST, p.snapshot(*source_documents()), 'user:owner')


def proposed(row, event='COMMENT'):
    row.update(status='awaiting_authorization', screen=p.parse_decision('{"safe":true,"reason":"No instruction attack identified"}', screening=True), proposal=p.parse_decision(json.dumps({'eligible': True, 'event': event, 'body': 'Please add a regression case.', 'reason': 'Parser behavior changed.'}), screening=False))
    return p.save(row)


def test_complete_snapshot_fingerprint_pins_body_patch_messages_and_head(home):
    documents = source_documents()
    first = p.snapshot(*documents)
    assert first['head_sha'] == 'a' * 40
    assert first['base_sha'] == 'b' * 40
    assert first['files'][0]['patch'] == '-old\n+new'
    assert first['commits'][0]['commit']['message'] == 'Improve parser'
    assert len(first['fingerprint']) == 64
    assert p.snapshot(*source_documents()) == first
    changes = [(0, 'body', 'Edited PR body'), (0, 'title', 'Edited title')]
    for index, key, value in changes:
        documents = copy.deepcopy(source_documents())
        documents[index][key] = value
        assert p.snapshot(*documents)['fingerprint'] != first['fingerprint']
    documents = copy.deepcopy(source_documents())
    documents[1][0]['patch'] = '-old\n+different'
    assert p.snapshot(*documents)['fingerprint'] != first['fingerprint']
    documents = copy.deepcopy(source_documents())
    documents[2][0]['commit']['message'] = 'Altered commit message'
    assert p.snapshot(*documents)['fingerprint'] != first['fingerprint']
    documents = copy.deepcopy(source_documents())
    documents[0]['head']['sha'] = 'c' * 40
    assert p.snapshot(*documents)['fingerprint'] != first['fingerprint']
    assert first['head_sha'] == 'a' * 40


@pytest.mark.parametrize('change', ['draft', 'closed', 'sha', 'files', 'commits', 'binary', 'oversize'])
def test_incomplete_or_unreviewable_content_rejected(home, change):
    pr, files, commits = source_documents()
    if change == 'draft':
        pr['draft'] = True
    elif change == 'closed':
        pr['state'] = 'closed'
    elif change == 'sha':
        pr['head']['sha'] = 'not-a-sha'
    elif change == 'files':
        pr['changed_files'] = 2
    elif change == 'commits':
        pr['commits'] = 2
    elif change == 'binary':
        files[0].pop('patch')
    else:
        files[0]['patch'] = 'x' * 500001
    with pytest.raises(ValueError):
        p.snapshot(pr, files, commits)
    assert p.view()['records'] == []


def test_persisted_capture_deduplication_and_changed_content_new_identity(home):
    row = captured()
    repeated = captured()
    assert repeated['id'] == row['id']
    assert repeated['revision'] == 1
    assert row['status'] == 'captured'
    assert row['screen'] is None
    assert row['proposal'] is None
    assert row['review_id'] is None
    assert json.loads(p._path(row['id']).read_text()) == row
    assert p.get(row['id']) == row
    documents = source_documents()
    documents[0]['body'] = 'An independently changed body'
    changed = p.record(REQUEST, p.snapshot(*documents), 'user:owner')
    assert changed['id'] != row['id']
    assert len(p.view()['records']) == 2
    assert p.view()['credentials'] == ['github-review']
    assert 'secret' not in json.dumps(p.view())
    with pytest.raises(ValueError):
        p.record(REQUEST, p.snapshot(*source_documents()), '')
    with pytest.raises(ValueError):
        p.get('../../credentials')


@pytest.mark.parametrize('text,screening', [('{"safe":"true","reason":"x"}', True), ('{"safe":true,"reason":"x","command":"run"}', True), ('not JSON', True), ('{"eligible":true,"event":"APPROVE","body":"ok","reason":"x"}', False), ('{"eligible":true,"event":"MERGE","body":"ok","reason":"x"}', False), ('{"eligible":true,"event":"COMMENT","body":"","reason":"x"}', False)])
def test_model_decisions_are_strict_and_cannot_authorize_other_actions(home, text, screening):
    with pytest.raises(ValueError):
        p.parse_decision(text, screening=screening)
    assert p.view()['records'] == []


def test_authorization_exact_actor_revision_state_and_payload(home):
    row = proposed(captured(), 'REQUEST_CHANGES')
    assert p.authorized(row['id'], 1, 'user:owner', {'awaiting_authorization'}) == row
    for actor, revision, states in [('user:other', 1, {'awaiting_authorization'}), ('user:owner', 0, {'awaiting_authorization'}), ('user:owner', True, {'awaiting_authorization'}), ('user:owner', 1, {'captured'}), ('', 1, {'awaiting_authorization'})]:
        with pytest.raises(ValueError):
            p.authorized(row['id'], revision, actor, states)
    payload = p.review_payload(row)
    assert payload['commit_id'] == 'a' * 40
    assert payload['event'] == 'REQUEST_CHANGES'
    assert payload['body'].startswith('Please add a regression case.')
    assert payload['body'].endswith('<!-- gideon-review:' + row['id'] + ' -->')
    assert 'reason' not in payload
    assert 'screen' not in payload
    assert 'command' not in payload
    assert p.get(row['id'])['status'] == 'awaiting_authorization'


def test_exact_review_reconciliation_rejects_wrong_actor_sha_body_and_duplicates(home):
    row = proposed(captured())
    row.update(status='submitting', submitter='reviewer', submission=p.review_payload(row))
    review = {'id': 19, 'body': row['submission']['body'], 'state': 'COMMENTED', 'commit_id': 'a' * 40, 'user': {'login': 'reviewer'}}
    wrong = []
    for key, value in [('body', 'same proposal without marker'), ('commit_id', 'c' * 40), ('state', 'APPROVED'), ('user', {'login': 'impostor'})]:
        wrong.append({**review, key: value})
    uncertain = p.reconcile(row, wrong)
    assert uncertain['status'] == 'uncertain'
    assert uncertain['review_id'] is None
    assert 'no automatic retry' in uncertain['error']
    resolved = p.reconcile(uncertain, wrong + [review])
    assert resolved['status'] == 'submitted'
    assert resolved['review_id'] == 19
    assert resolved['error'] is None
    assert p.get(row['id']) == resolved
    row['review_id'] = None
    duplicate = p.reconcile(row, [review, {**review, 'id': 20}])
    assert duplicate['status'] == 'uncertain'
    assert duplicate['review_id'] is None


@pytest.mark.asyncio
async def test_missing_actual_credential_and_model_fail_closed_without_forge_write(home):
    with pytest.raises(ValueError, match='credential'):
        await p.capture(REQUEST, 'user:owner')
    assert p.view()['records'] == []
    row = captured()
    result = await p.screen(row['id'], row['revision'], 'user:owner')
    assert result['status'] == 'blocked'
    assert result['proposal'] is None
    assert result['review_id'] is None
    assert result['revision'] == 2
    assert 'unavailable' in result['error']
    with pytest.raises(ValueError):
        await p.submit(row['id'], 2, 'user:owner')
    assert p.get(row['id']) == result
    for path in ['/other', '/repos/../credentials']:
        with pytest.raises(ValueError):
            await p.github('github-review', path)


@pytest.mark.asyncio
async def test_signed_http_source_read_and_real_failure_mutations(home):
    row = captured()
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        response = await client.get(PREFIX, params={'token': generate_token('owner')})
        assert response.status == 200
        assert (await response.json())['records'][0]['source']['head_sha'] == 'a' * 40
        response = await client.post(PREFIX, json=REQUEST)
        assert response.status == 409
        assert 'credential' in (await response.json())['error']
        response = await client.post(PREFIX + '/' + row['id'], json={'action': 'screen', 'revision': 1})
        assert response.status == 200
        value = await response.json()
        assert value['status'] == 'blocked'
        assert value['revision'] == 2
        assert p.get(row['id'])['status'] == 'blocked'
        for payload in [{'action': 'authorize', 'revision': 2}, {'action': 'screen', 'revision': 1}, {'action': 'merge', 'revision': 2}, {'action': 'screen', 'revision': 2, 'actor': 'other'}]:
            response = await client.post(PREFIX + '/' + row['id'], json=payload)
            assert response.status == 409
        assert p.get(row['id'])['review_id'] is None
        assert len(p.view()['records']) == 1


@pytest.mark.asyncio
async def test_native_tools_read_actual_snapshot_and_unavailable_credential(home):
    row = captured()
    provider = create_provider()
    result = await provider.invoke('platform_pr_screening', {})
    assert result.success
    assert json.loads(result.output)['records'][0]['id'] == row['id']
    token = set_current_session_key('agent:reviewer')
    try:
        result = await provider.invoke('platform_pr_capture', REQUEST)
        assert not result.success
    finally:
        reset_current_session_key(token)
    token = set_current_session_key('')
    try:
        result = await provider.invoke('platform_pr_capture', REQUEST)
        assert not result.success
    finally:
        reset_current_session_key(token)
    tools = await provider.list_tools()
    capture = next(item for item in tools if item.name == 'platform_pr_capture')
    assert capture.requires_approval
    assert not any(item.name == 'platform_pr_submit' for item in tools)
    manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
    assert capture.name in manifest['provider']['capabilities']
    assert 'platform_pr_screening' in manifest['provider']['capabilities']
    assert p.get(row['id'])['status'] == 'captured'
