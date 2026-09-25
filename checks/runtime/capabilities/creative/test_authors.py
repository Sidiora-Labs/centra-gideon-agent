import asyncio
import json
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.creative.authors import AuthorStore
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider


def payload(**changes):
    return {'request_id': str(uuid4()), 'title': 'Mira', 'biography': 'Writes about cities.',
            'voice': {'perspective': 'third', 'tense': 'past', 'tone': 'Reflective', 'diction': 'Concrete', 'rhythm': 'Varied', 'avoid': 'Cliches'}, **changes}


def sample(home, text='The night train crossed the city.'):
    provider = NativeArtifactProvider(home / 'artifacts')
    artifact = provider.create(name='Writing sample', kind='markdown', content=text)
    return provider, artifact


def test_author_lifecycle_revisions_restore_and_deterministic_brief(tmp_path):
    artifacts, artifact = sample(tmp_path)
    store = AuthorStore(tmp_path)
    ref = {'artifact_id': artifact.slug, 'artifact_version': 1}
    first = store.create(payload(sample_refs=[ref]))
    assert first['revision'] == 1
    assert first['voice']['tone'] == 'Reflective'
    assert first['sample_refs'] == [ref]
    second = store.update(first['id'], {'revision': 1, 'voice': {**first['voice'], 'tone': 'Urgent'}})
    assert second['voice']['tone'] == 'Urgent'
    assert second['created_at'] == first['created_at']
    assert second['revision'] == 2
    assert store.export(first['id'], 1) == first
    brief = store.brief(first['id'], 1)
    assert brief['kind'] == 'configured_voice_brief'
    assert brief['voice'] == first['voice']
    assert brief['biography'] == first['biography']
    assert brief['samples'][0]['content'] == 'The night train crossed the city.'
    assert brief['samples'][0]['missing'] is False
    assert brief['samples'][0]['truncated'] is False
    assert brief == store.brief(first['id'], 1)
    restored = store.restore(first['id'], {'revision': 2, 'target_revision': 1})
    assert restored['voice'] == first['voice']
    assert restored['revision'] == 3
    reopened = AuthorStore(tmp_path)
    assert reopened.revisions(first['id']) == [restored, second, first]
    assert reopened.export(first['id']) == restored
    assert reopened.get(first['id'])['sample_status'] == [{**ref, 'missing': False, 'title': artifact.name}]


def test_pinned_samples_use_actual_version_and_bounded_excerpt(tmp_path):
    text = 'é' * 5000
    artifacts, artifact = sample(tmp_path, text)
    store = AuthorStore(tmp_path)
    author = store.create(payload(sample_refs=[{'artifact_id': artifact.slug, 'artifact_version': 1}]))
    artifacts.update(artifact.slug, content='New sample version', snapshot=True)
    brief = store.brief(author['id'])
    assert brief['samples'][0]['content'] == 'é' * 4000
    assert brief['samples'][0]['original_characters'] == 5000
    assert brief['samples'][0]['truncated'] is True
    assert brief['samples'][0]['artifact_version'] == 1
    assert store.sources()['items'][0]['version'] == 2
    assert store.sources(q='writing')['items'][0]['id'] == artifact.slug
    assert store.sources(q='absent')['items'] == []
    artifacts.delete(artifact.slug)
    assert store.get(author['id'])['sample_status'][0]['missing'] is True
    missing = store.brief(author['id'])['samples'][0]
    assert missing['missing'] is True
    assert missing['content'] == ''
    assert missing['original_characters'] == 0
    assert store.export(author['id']) == author
    updated = store.update(author['id'], {'revision': 1, 'sample_refs': []})
    restored = store.restore(author['id'], {'revision': updated['revision'], 'target_revision': 1})
    assert restored['sample_refs'] == author['sample_refs']
    with pytest.raises(CatalogError):
        store.create(payload(sample_refs=author['sample_refs']))


@pytest.mark.parametrize('changes', [
    {'voice': {'perspective': 'second'}}, {'voice': {'tense': 'future'}},
    {'voice': {'tone': ['invalid']}}, {'voice': {'model': 'override'}},
    {'voice': {'tone': 'x' * 4001}}, {'biography': 'x' * 20001}, {'title': ''},
    {'sample_refs': [{}] * 9}, {'sample_refs': 'invalid'},
    {'sample_refs': [{'artifact_id': '../escape', 'artifact_version': 1}]},
    {'sample_refs': [{'artifact_id': 'missing', 'artifact_version': True}]},
    {'sample_refs': [{'artifact_id': 'missing', 'artifact_version': 1, 'home': 'override'}]},
    {'provider': 'override'}, {'model': 'override'}, {'home': '/tmp/other'}, {'session_id': 'private'},
])
def test_strict_validation_is_atomic(tmp_path, changes):
    store = AuthorStore(tmp_path)
    with pytest.raises(CatalogError):
        store.create(payload(**changes))
    assert store.list()['total'] == 0
    first = store.create(payload())
    with pytest.raises(CatalogError):
        store.update(first['id'], {'revision': 1, **changes})
    assert store.export(first['id']) == first
    assert store.revisions(first['id']) == [first]


def test_sample_rejection_two_homes_and_nontext_sources(tmp_path):
    artifacts, artifact = sample(tmp_path / 'one')
    one = AuthorStore(tmp_path / 'one')
    two = AuthorStore(tmp_path / 'two')
    author = one.create(payload(sample_refs=[{'artifact_id': artifact.slug, 'artifact_version': 1}]))
    with pytest.raises(CatalogError):
        two.create(payload(sample_refs=author['sample_refs']))
    with pytest.raises(CatalogError):
        two.brief(author['id'])
    assert two.sources()['items'] == []
    assert two.list()['total'] == 0
    binary = artifacts.create_binary(name='Binary image', data=b'\x89PNG\r\n\x1a\n', mime='image/png')
    with pytest.raises(CatalogError):
        one.create(payload(sample_refs=[{'artifact_id': binary.slug, 'artifact_version': 1}]))
    assert [entry['id'] for entry in one.sources()['items']] == [artifact.slug]
    with pytest.raises(CatalogError):
        one.create(payload(sample_refs=[{'artifact_id': artifact.slug, 'artifact_version': 999}]))


def test_request_replay_conflicts_and_pagination(tmp_path):
    store = AuthorStore(tmp_path)
    request = payload()
    first = store.create(request)
    assert store.create(request) == first
    with pytest.raises(CatalogError) as reused:
        store.create({**request, 'title': 'Different'})
    assert reused.value.status == 409
    second = store.update(first['id'], {'revision': 1, 'title': 'New name'})
    with pytest.raises(CatalogError) as stale:
        store.update(first['id'], {'revision': 1, 'title': 'Stale'})
    assert stale.value.status == 409
    assert store.export(first['id']) == second
    for index in range(26):
        store.create(payload(title=f'Author {index}'))
    assert store.list()['total'] == 27
    assert len(store.list()['items']) == 25
    assert len(store.list(offset=25)['items']) == 2
    assert store.list(q='New name')['items'] == [second]
    assert store.list(q='nothing')['total'] == 0


def test_actual_http_author_workflow_and_native_brief(tmp_path):
    async def run():
        artifacts, artifact = sample(tmp_path)
        app = web.Application(); app[STORE] = IngredientStore(tmp_path); register(app)
        base = '/api/capabilities/creative/authors'
        async with TestClient(TestServer(app)) as client:
            sources = await client.get(base + '/sources?q=writing')
            assert sources.status == 200
            assert (await sources.json())['items'][0]['id'] == artifact.slug
            created = await client.post(base, json=payload(sample_refs=[{'artifact_id': artifact.slug, 'artifact_version': 1}]))
            assert created.status == 201
            author = await created.json()
            path = base + '/' + author['id']
            edited = await client.patch(path, json={'revision': 1, 'title': 'HTTP author'})
            assert edited.status == 200
            brief = await client.get(path + '/brief?revision=1')
            assert brief.status == 200
            assert (await brief.json())['title'] == 'Mira'
            exported = await client.get(path + '/export?revision=1')
            assert 'attachment' in exported.headers['Content-Disposition']
            assert await exported.json() == author
            restore = await client.post(path + '/restore', json={'revision': 2, 'target_revision': 1})
            assert restore.status == 200
            history = await client.get(path + '/revisions')
            assert len((await history.json())['items']) == 3
            detail = await client.get(path)
            assert (await detail.json())['sample_status'][0]['missing'] is False
            bad = await client.get(base + '?provider=other')
            assert bad.status == 400
            missing = await client.get(base + '/missing/brief')
            assert missing.status == 404
        provider = CreativeToolProvider(tmp_path)
        async def call(action, args):
            result = await provider.invoke('creative_author_' + action, args)
            assert result.success, result.error
            return json.loads(result.output)
        native = await call('create', {'payload': payload(title='Native')})
        assert (await call('get', {'id': native['id']}))['title'] == 'Native'
        assert (await call('sources', {'q': 'writing'}))['items'][0]['id'] == artifact.slug
        edited = await call('update', {'id': native['id'], 'payload': {'revision': 1, 'title': 'Native updated'}})
        assert (await call('list', {'q': 'Native updated'}))['items'] == [edited]
        assert (await call('revisions', {'id': native['id']}))['total'] == 2
        assert await call('export', {'id': native['id'], 'revision': 1}) == native
        restored = await call('restore', {'id': native['id'], 'payload': {'revision': 2, 'target_revision': 1}})
        assert (await call('brief', {'id': native['id']}))['revision'] == restored['revision']
    asyncio.run(run())
