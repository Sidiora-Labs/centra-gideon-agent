import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.core.sqlite_compat import sqlite3
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.stories import StoryStore
from gideon.workspace.capabilities.creative.store import IngredientStore, CatalogError
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider


def create(store, **changes):
    return store.create({'request_id': str(uuid4()), 'title': 'Night train', **changes})


def ready(store, **changes):
    first = create(store, premise='A traveler misses the final train.', **changes)
    development = store.update(first['id'], {'revision': 1, 'stage': 'development', 'protagonist_goal': 'Get home', 'conflict': 'No trains', 'stakes': 'Family waiting', 'ending': 'Walks home'})
    outline = store.update(first['id'], {'revision': 2, 'stage': 'outline', 'beats': [{'id': 'arrival', 'title': 'Arrival', 'summary': 'Arrives too late.'}, {'id': 'walk', 'title': 'Walk', 'summary': 'Finds another way.'}]})
    return store.update(first['id'], {'revision': 3, 'stage': 'ready'})


def test_guided_readiness_stage_order_and_restore(tmp_path):
    store = StoryStore(tmp_path)
    first = create(store)
    detail = store.get(first['id'])
    assert detail['next_stage'] == 'development'
    assert detail['missing_for_next_stage'] == ['premise']
    with pytest.raises(CatalogError):
        store.update(first['id'], {'revision': 1, 'stage': 'development'})
    with pytest.raises(CatalogError):
        store.update(first['id'], {'revision': 1, 'stage': 'ready'})
    development = store.update(first['id'], {'revision': 1, 'premise': 'Missing train', 'stage': 'development'})
    assert development['stage'] == 'development'
    assert store.get(first['id'])['missing_for_next_stage'] == ['protagonist_goal', 'conflict', 'stakes', 'ending']
    with pytest.raises(CatalogError):
        store.update(first['id'], {'revision': 2, 'stage': 'outline'})
    outline = store.update(first['id'], {'revision': 2, 'stage': 'outline', 'protagonist_goal': 'Home', 'conflict': 'Distance', 'stakes': 'Family', 'ending': 'Returns'})
    assert store.get(first['id'])['missing_for_next_stage'] == ['beats']
    with pytest.raises(CatalogError):
        store.update(first['id'], {'revision': 3, 'stage': 'ready', 'beats': [{'id': 'one', 'title': 'Empty summary'}]})
    completed = store.update(first['id'], {'revision': 3, 'stage': 'ready', 'beats': [{'id': 'one', 'title': 'Walk', 'summary': 'Walks home'}]})
    assert completed['stage'] == 'ready'
    assert store.get(first['id'])['missing_for_next_stage'] == []
    restored = store.restore(first['id'], {'revision': 4, 'target_revision': 1})
    assert restored['stage'] == 'premise'
    assert restored['premise'] == ''
    restored_ready = store.restore(first['id'], {'revision': 5, 'target_revision': 4})
    assert restored_ready['beats'] == completed['beats']
    assert restored_ready['stage'] == 'ready'
    assert StoryStore(tmp_path).export(first['id']) == restored_ready
    assert len(store.revisions(first['id'])) == 6


def test_authored_guidance_review_adoption_and_conflicts(tmp_path):
    store = StoryStore(tmp_path)
    story = create(store)
    payload = {'request_id': 'review', 'revision': 1, 'mode': 'authored', 'patch': {'premise': 'A traveler must walk home.', 'genre': 'Literary'}}
    proposal = asyncio.run(store.suggest(story['id'], payload))
    assert proposal['mode'] == 'authored'
    assert proposal['patch'] == payload['patch']
    assert store.export(story['id']) == story
    assert store.suggestions(story['id'])['items'] == [proposal]
    assert asyncio.run(store.suggest(story['id'], payload)) == proposal
    with pytest.raises(CatalogError) as replay:
        asyncio.run(store.suggest(story['id'], {**payload, 'patch': {'premise': 'Other'}}))
    assert replay.value.status == 409
    adopted = store.adopt(story['id'], {'revision': 1, 'suggestion_id': proposal['id']})
    assert adopted['premise'] == payload['patch']['premise']
    assert adopted['genre'] == 'Literary'
    assert adopted['revision'] == 2
    assert adopted['stage'] == 'premise'
    with pytest.raises(CatalogError) as stale:
        store.adopt(story['id'], {'revision': 2, 'suggestion_id': proposal['id']})
    assert stale.value.status == 409
    assert store.export(story['id'], 1) == story
    with pytest.raises(CatalogError):
        asyncio.run(store.suggest(story['id'], {'request_id': 'badstage', 'revision': 2, 'mode': 'authored', 'patch': {'ending': 'Wrong stage'}}))
    assert len(store.suggestions(story['id'])['items']) == 1


def test_ready_story_creates_one_real_work_atomically_and_preserves_pins(tmp_path):
    store = StoryStore(tmp_path)
    author = store.works.authors.create({'request_id': 'author', 'title': 'Mira'})
    universe = store.works.universes.create({'request_id': 'universe', 'title': 'Night city'})
    story = ready(store, author_ref={'id': author['id'], 'revision': 1}, universe_ref={'id': universe['id'], 'revision': 1})
    payload = {'request_id': 'create-work', 'revision': 4}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.create_work(story['id'], payload), range(2)))
    work = results[0]
    assert results[1] == work
    assert work['author_ref'] == story['author_ref']
    assert work['universe_ref'] == story['universe_ref']
    assert 'Goal: Get home' in work['prompt']
    assert 'Arrival: Arrives too late.' in work['prompt']
    assert store.works.list()['total'] == 1
    assert store.get(story['id'])['work_links'] == [{'work_id': work['id'], 'story_revision': 4, 'missing': False}]
    assert store.export(story['id']) == story
    store.works.update(work['id'], {'revision': 1, 'title': 'Renamed work'})
    assert store.create_work(story['id'], payload)['title'] == 'Renamed work'
    with pytest.raises(CatalogError):
        store.create_work(story['id'], {**payload, 'revision': 3})
    with store.connection() as db:
        db.execute('DELETE FROM works WHERE id=?', (work['id'],))
    assert store.get(story['id'])['work_links'][0]['missing'] is True


def test_real_sqlite_failure_rolls_back_work_and_link(tmp_path):
    store = StoryStore(tmp_path)
    story = ready(store)
    with store.connection() as db:
        db.execute("CREATE TRIGGER reject_story_link BEFORE INSERT ON story_work_links BEGIN SELECT RAISE(ABORT, 'link unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_work(story['id'], {'request_id': 'retry', 'revision': 4})
    assert store.works.list()['total'] == 0
    assert store.get(story['id'])['work_links'] == []
    with store.connection() as db:
        db.execute('DROP TRIGGER reject_story_link')
    work = store.create_work(story['id'], {'request_id': 'retry', 'revision': 4})
    assert store.works.get(work['id'])['title'] == story['title']


@pytest.mark.parametrize('changes', [
    {'stage': 'ready'}, {'stage': 'unknown'}, {'premise': 'x' * 4001}, {'beats': [{}] * 51},
    {'beats': [{'id': 'same', 'title': 'One'}, {'id': 'same', 'title': 'Two'}]},
    {'beats': [{'id': '../bad', 'title': 'Bad'}]}, {'home': 'other'}, {'provider': 'override'},
    {'author_ref': {'id': 'foreign', 'revision': 1}}, {'universe_ref': {'id': 'foreign', 'revision': True}},
])
def test_invalid_story_is_atomic(tmp_path, changes):
    store = StoryStore(tmp_path)
    with pytest.raises(CatalogError):
        create(store, **changes)
    assert store.list()['total'] == 0
    first = create(store)
    with pytest.raises(CatalogError):
        store.update(first['id'], {'revision': 1, **changes})
    assert store.export(first['id']) == first


def test_missing_context_and_foreign_story_suggestion_access(tmp_path):
    one = StoryStore(tmp_path / 'one')
    author = one.works.authors.create({'request_id': 'author', 'title': 'Writer'})
    story = create(one, author_ref={'id': author['id'], 'revision': 1})
    assert one.get(story['id'])['source_status'][0]['title'] == 'Writer'
    with one.connection() as db:
        db.execute('DELETE FROM author_revisions WHERE id=?', (author['id'],))
    assert one.get(story['id'])['source_status'][0]['missing']
    two = StoryStore(tmp_path / 'two')
    for action in (lambda: two.get(story['id']), lambda: two.suggestions(story['id']), lambda: two.create_work(story['id'], {'request_id': 'cross', 'revision': 1})):
        with pytest.raises(CatalogError):
            action()
    assert two.works.list()['total'] == 0
    for index in range(26):
        create(two, title=f'Paged {index}')
    assert len(two.list()['items']) == 25
    assert len(two.list(offset=25)['items']) == 1
    assert two.list(q='missing')['total'] == 0


def test_actual_http_and_native_guided_story_workflow(tmp_path):
    async def run():
        app = web.Application(); app[STORE] = IngredientStore(tmp_path); register(app)
        base = '/api/capabilities/creative/stories'
        async with TestClient(TestServer(app)) as client:
            response = await client.post(base, json={'request_id': 'http', 'title': 'HTTP story'})
            assert response.status == 201
            story = await response.json()
            path = base + '/' + story['id']
            suggestion = await client.post(path + '/suggestions', json={'request_id': 'authored', 'revision': 1, 'mode': 'authored', 'patch': {'premise': 'Night train'}})
            assert suggestion.status == 200
            proposal = await suggestion.json()
            adopted = await client.post(path + '/adopt', json={'revision': 1, 'suggestion_id': proposal['id']})
            assert adopted.status == 200
            assert (await adopted.json())['premise'] == 'Night train'
            premature = await client.post(path + '/work', json={'request_id': 'early', 'revision': 2})
            assert premature.status == 409
            exported = await client.get(path + '/export?revision=1')
            assert await exported.json() == story
        provider = CreativeToolProvider(tmp_path)
        async def call(action, args):
            result = await provider.invoke('creative_story_' + action, args)
            assert result.success, result.error
            return json.loads(result.output)
        item = await call('create', {'payload': {'request_id': 'native', 'title': 'Native story'}})
        proposal = await call('suggest', {'id': item['id'], 'payload': {'request_id': 'native-review', 'revision': 1, 'mode': 'authored', 'patch': {'premise': 'A walk'}}})
        assert (await call('suggestions', {'id': item['id']}))['items'] == [proposal]
        adopted = await call('adopt', {'id': item['id'], 'payload': {'revision': 1, 'suggestion_id': proposal['id']}})
        assert (await call('get', {'id': item['id']}))['premise'] == 'A walk'
        assert (await call('list', {'q': 'Native'}))['items'] == [adopted]
        assert (await call('revisions', {'id': item['id']}))['total'] == 2
        assert await call('export', {'id': item['id'], 'revision': 1}) == item
        await call('restore', {'id': item['id'], 'payload': {'revision': 2, 'target_revision': 1}})
        completed = ready(StoryStore(tmp_path))
        work = await call('create_work', {'id': completed['id'], 'payload': {'request_id': 'native-work', 'revision': 4}})
        assert work['title'] == completed['title']
    asyncio.run(run())


def test_actual_model_guidance_outcome_is_recorded_separately(tmp_path):
    store = StoryStore(tmp_path)
    story = create(store)
    report = {'task': 'creative.08', 'actual_provider_attempt': True, 'inference_qualified': False}
    try:
        proposal = asyncio.run(store.suggest(story['id'], {'request_id': 'model', 'revision': 1, 'instruction': 'Create a one-sentence premise about a train journey.'}))
    except CatalogError as error:
        assert error.status == 503
        assert store.export(story['id']) == story
        assert store.suggestions(story['id'])['items'] == []
        report['error'] = str(error)
    else:
        assert proposal['mode'] == 'model'
        assert proposal['patch']
        adopted = store.adopt(story['id'], {'revision': 1, 'suggestion_id': proposal['id']})
        assert adopted['revision'] == 2
        report.update(inference_qualified=True, suggestion_id=proposal['id'])
    Path('/tmp/gideon-creative-08-inference-oss.json').write_text(json.dumps(report, indent=2) + '\n')


def test_stage_specific_authored_outline_review_preserves_order_and_refuses_cross_stage(tmp_path):
    store = StoryStore(tmp_path)
    story = create(store, premise='A night train')
    story = store.update(story['id'], {'revision': 1, 'stage': 'development'})
    proposal = asyncio.run(store.suggest(story['id'], {'request_id': 'develop', 'revision': 2, 'mode': 'authored', 'patch': {
        'protagonist_goal': 'Home', 'conflict': 'No transport', 'stakes': 'A promise', 'ending': 'Returns'}}))
    assert proposal['stage'] == 'development'
    assert store.export(story['id'])['protagonist_goal'] == ''
    story = store.adopt(story['id'], {'revision': 2, 'suggestion_id': proposal['id']})
    assert story['protagonist_goal'] == 'Home'
    story = store.update(story['id'], {'revision': 3, 'stage': 'outline'})
    beats = [{'id': 'one', 'title': 'Arrival', 'summary': 'Late'}, {'id': 'two', 'title': 'Choice', 'summary': 'Walks'}]
    outline = asyncio.run(store.suggest(story['id'], {'request_id': 'outline', 'revision': 4, 'mode': 'authored', 'patch': {'beats': beats}}))
    assert outline['patch']['beats'] == beats
    assert store.export(story['id'])['beats'] == []
    adopted = store.adopt(story['id'], {'revision': 4, 'suggestion_id': outline['id']})
    assert adopted['beats'] == beats
    assert [entry['id'] for entry in adopted['beats']] == ['one', 'two']
    for patch in ({'title': 'Forbidden'}, {'stage': 'ready'}, {'author_ref': None}, {}):
        with pytest.raises(CatalogError):
            asyncio.run(store.suggest(story['id'], {'request_id': str(uuid4()), 'revision': 5, 'mode': 'authored', 'patch': patch}))
    with pytest.raises(CatalogError):
        asyncio.run(store.suggest(story['id'], {'request_id': 'injected', 'revision': 5, 'mode': 'model', 'patch': {'beats': beats}}))
    assert len(store.suggestions(story['id'])['items']) == 2
    assert store.export(story['id']) == adopted


def test_oversized_ready_outline_refuses_work_without_truncation(tmp_path):
    store = StoryStore(tmp_path)
    story = ready(store)
    beats = [{'id': f'beat-{index}', 'title': f'Beat {index}', 'summary': 'x' * 2000} for index in range(11)]
    story = store.update(story['id'], {'revision': 4, 'beats': beats})
    with pytest.raises(CatalogError, match='prompt limit'):
        store.create_work(story['id'], {'request_id': 'too-long', 'revision': 5})
    assert store.works.list()['total'] == 0
    assert store.get(story['id'])['work_links'] == []
    assert store.export(story['id'])['beats'] == beats
    assert len(store.revisions(story['id'])) == 5
