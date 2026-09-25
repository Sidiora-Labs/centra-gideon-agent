import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.store import IngredientStore, CatalogError
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.creative.editorial import EditorialStore
from gideon.workspace.capabilities.creative.prose_checks import scan, SUPPORTED, CATALOG
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider


@pytest.mark.parametrize('check,source,quotes', [
    ('prose.filter-words', 'She saw the door and began to run.', ['saw', 'began to']),
    ('prose.filter-words', 'He began to move.', ['began to']),
    ('prose.filter-words', 'The sawmill was loud.', []),
    ('prose.filter-words', 'She could see the door; he found himself running.', ['could see', 'found himself']),
    ('prose.hedge-words', 'It was as if he knew. Part of him almost felt it. I kind of think so, no doubt.', ['as if', 'Part of him', 'almost', 'kind of', 'no doubt']),
    ('prose.hedge-words', 'The almshouse stood empty.', []),
    ('prose.crutch-words', 'It was just really very that thing.', ['just', 'really', 'very']),
    ('prose.crutch-words', 'He left in order to escape.', ['in order to']),
    ('prose.adverbs', 'She quickly entered the family home only briefly.', ['quickly', 'briefly']),
    ('prose.adverbs', 'She walked slowly.', ['slowly']),
    ('prose.cliches', 'And then TIME STOOD STILL as the door opened.', ['TIME STOOD STILL']),
    ('prose.cliches', 'the silence was a\n  deafening   silence indeed', ['deafening   silence']),
    ('prose.cliches', 'he scolded sweater makers', []),
    ('prose.cliches', 'little did they know. Later, all hell broke loose, and little did they know again.', ['little did they know', 'all hell broke loose']),
    ('prose.modifier-stacking', 'It was a big red shiny new car.', ['big red shiny new']),
    ('prose.modifier-stacking', 'a big red car', []),
    ('prose.word-echoes', 'The obsidian blade gleamed. She raised the obsidian high.', ['obsidian']),
    ('prose.word-echoes', 'The cat sat on the mat and the dog ran.', []),
    ('prose.word-echoes', 'crimson crimson crimson crimson', ['crimson']),
    ('prose.italic-thoughts', 'She froze. *He knows I lied to him.* Then she ran.', ['*He knows I lied to him.*']),
    ('prose.italic-thoughts', '_This cannot be happening again._', ['_This cannot be happening again._']),
    ('prose.italic-thoughts', 'She would *never* do that. He told her to _run_.', []),
    ('prose.italic-thoughts', '**This is bold and not a thought.**', []),
    ('prose.italic-thoughts', '***This is bold italic emphasis here.***', []),
    ('prose.italic-thoughts', 'The file my_long_variable_name was edited.', []),
    ('prose.italic-thoughts', '*one line\nsecond line and more words here*', []),
    ('prose.italic-thoughts', '*I have to get out.* ... later ... *I have to get out.*', ['*I have to get out.*']),
    ('dialogue.said-bookisms', '"I disagree," expostulated Marlon.', ['expostulated']),
    ('dialogue.said-bookisms', '"Indeed," she opined, leaning back.', ['opined']),
    ('dialogue.said-bookisms', 'Marlon interjected, "Wait."', ['interjected']),
    ('dialogue.said-bookisms', '"Of course," she smiled.', ['smiled']),
    ('dialogue.said-bookisms', 'The engine growled as it turned over.', []),
    ('dialogue.said-bookisms', '"Run!" Thunder rolled overhead.', []),
    ('dialogue.said-bookisms', '"Of course." She smiled.', []),
    ('dialogue.said-bookisms', '"Ha," he quipped.', ['quipped']),
])
def test_preserved_prose_vectors_and_exact_anchors(check, source, quotes):
    hits = scan(check, source)
    assert [hit['quote'] for hit in hits] == quotes
    for hit in hits:
        assert source[hit['start']:hit['end']] == hit['quote']
        assert hit['end'] > hit['start']
        assert hit['problem']
        assert hit['suggestion']


def test_adverb_dialogue_manner_emotion_and_passive_intent():
    emotional = scan('prose.adverbs', '"Fine," she said angrily.')[0]
    assert emotional['dialogue_tag']
    assert emotional['tag_kind'] == 'emotion'
    reporting = scan('prose.adverbs', '"Fine," she said quietly.')[0]
    assert reporting['dialogue_tag']
    assert reporting['tag_kind'] == 'reporting'
    assert scan('prose.adverbs', 'She walked slowly.')[0]['tag_kind'] is None
    assert scan('prose.passive-voice', 'The letter was written by Mara.')[0]['quote'] == 'was written'
    assert scan('prose.passive-voice', 'She was exhausted.') == []
    assert scan('prose.passive-voice', 'The sky was streaked with red.') == []
    assert scan('prose.passive-voice', 'The cake was eaten.')[0]['quote'] == 'was eaten'


def test_rhythm_burstiness_gestures_and_stock_patterns():
    uniform = 'one two three four five. ' * 10
    assert scan('prose.sentence-rhythm', uniform)[0]['coefficient'] == 0
    assert scan('prose.sentence-rhythm', 'One. Two.') == []
    assert scan('prose.repeated-gestures', 'She nodded. He nodded. They nodded.')[0]['occurrences'] == 3
    assert scan('prose.ai-tells', "She couldn't help but smile.")[0]['quote'] == "couldn't help but"
    assert scan('prose.ai-tells', 'Her eyes widened.')[0]['quote'] == 'eyes widened'
    assert scan('prose.structural-tics', 'Go. Run. Stop.')[0]['quote'] == 'Go. Run. Stop.'
    assert scan('prose.structural-tics', 'It was not just rain but a storm.')[0]['quote'] == 'not just rain but a storm.'
    assert scan('prose.burstiness', 'One—two—three.')[0]['quote'] == '—'
    paragraphs = '\n\n'.join(['one two three four five.'] * 4)
    assert any(h['problem'] == 'Uniform paragraph lengths' for h in scan('prose.burstiness', paragraphs))


def test_dialogue_attribution_and_tag_inventory():
    bare = '\n'.join(['"You came back."', '"I had to."', '"After everything?"', '"Especially after everything."', '"And now?"', '"Now we finish it."'])
    assert scan('dialogue.attribution-clarity', bare)[0]['quote'] == '"You came back."'
    assert scan('dialogue.attribution-clarity', '"Hi."\n"Hello."\n"Bye."') == []
    tagged = bare.replace('"I had to."', '"I had to," she said.')
    assert scan('dialogue.attribution-clarity', tagged) == []
    repetitive = '"No," she said. ' * 5
    assert scan('dialogue.tag-variety', repetitive)[0]['total_tags'] == 5
    assert scan('dialogue.tag-variety', repetitive)[0]['quote'] == 'said'
    assert scan('dialogue.tag-variety', '"No," she said.') == []


def fixture(home, source='😀 Time stood still. She saw the obsidian door. The obsidian door opened.'):
    works = WorkStore(home)
    work = works.create({'request_id': 'work', 'title': 'Editorial manuscript'})
    work = works.draft(work['id'], {'request_id': 'draft', 'revision': 1, 'text': source})['work']
    return works, EditorialStore(works), work, source


def payload(source, **changes):
    return {'request_id': str(uuid4()), 'work_revision': 2, 'start': 0, 'end': len(source), 'check_ids': ['prose.cliches', 'prose.filter-words', 'prose.word-echoes'], **changes}


def test_registry_all_eighty_and_missing_context_are_explicit(tmp_path):
    works, editorial, work, source = fixture(tmp_path)
    catalog = editorial.catalog()
    assert len(catalog) == 80
    assert len({c['id'] for c in catalog}) == 80
    assert {c['kind'] for c in catalog} == {'deterministic', 'llm'}
    assert next(c for c in catalog if c['id'] == 'chekhov.setups-payoffs')['label'] != 'Max findings per run'
    assert next(c for c in catalog if c['id'] == 'prose.cliches')['availability'] == 'available'
    assert next(c for c in catalog if c['id'] == 'visual.eyeline-match')['availability'] == 'requires_context'
    assert next(c for c in catalog if c['id'] == 'visual.eyeline-match')['context_family'] == 'scene'
    run = editorial.run(work['id'], payload(source, check_ids=['prose.cliches', 'visual.eyeline-match']))
    assert run['kind'] == 'mixed'
    assert run['results'][0]['status'] == 'completed'
    assert run['results'][1] == {'check_id': 'visual.eyeline-match', 'status': 'skipped', 'reason': 'scene_context_missing'}
    assert run['findings'][0]['quote'] == 'Time stood still'
    assert run['findings'][0]['start'] == 2
    assert run['readiness'] == 'review_required'
    assert works.get(work['id'])['text'] == source
    assert len(works.artifacts.list()) == 1
    assert len(works.drafts(work['id'])['items']) == 1
    assert EditorialStore(WorkStore(tmp_path)).get(work['id']) == editorial.get(work['id'])


def test_run_replay_partial_coverage_empty_clear_and_caps(tmp_path):
    works, editorial, work, source = fixture(tmp_path)
    data = payload(source)
    run = editorial.run(work['id'], data)
    assert editorial.run(work['id'], data) == run
    with pytest.raises(CatalogError) as conflict:
        editorial.run(work['id'], {**data, 'end': 10})
    assert conflict.value.status == 409
    partial = editorial.run(work['id'], payload(source, start=20, end=25, check_ids=['prose.cliches']))
    assert partial['coverage'] == {'start': 20, 'end': 25, 'total_characters': len(source)}
    assert partial['findings'] == []
    assert partial['readiness'] == 'incomplete'
    clean = editorial.run(work['id'], payload(source, check_ids=['prose.italic-thoughts']))
    assert clean['readiness'] == 'selected_checks_clear'
    missing_context = editorial.run(work['id'], payload(source, check_ids=['pov.head-hopping']))
    assert missing_context['readiness'] == 'incomplete'
    assert missing_context['findings'] == []
    many = works.draft(work['id'], {'request_id': 'many', 'revision': 2, 'text': 'really ' * 150})['work']
    capped = editorial.run(work['id'], payload('really ' * 150, work_revision=many['revision'], check_ids=['prose.crutch-words']))
    assert capped['results'][0]['finding_count'] == 150
    assert capped['results'][0]['truncated']
    assert len(capped['findings']) == 100
    assert len(editorial.get(work['id'])['runs']) == 5


async def test_exact_repair_review_promote_restore_and_replay(tmp_path):
    works, editorial, work, source = fixture(tmp_path)
    run = await editorial.run_async(work['id'], payload(source))
    finding = run['findings'][0]
    data = {'request_id': 'repair', 'work_revision': 2, 'replacement': 'The clock stopped'}
    receipt = await editorial.repair(work['id'], run['id'], finding['id'], data)
    proposal = receipt['proposal']
    assert proposal['mode'] == 'authored'
    assert works.get(work['id'])['text'] == source
    review = editorial.polishing.get(work['id'], proposal['id'])
    assert review['original'] == 'Time stood still'
    assert review['replacement'] == 'The clock stopped'
    assert '-😀 Time stood still.' in review['diff']
    promoted = editorial.polishing.promote(work['id'], proposal['id'], {'revision': 2})
    assert works.get(work['id'])['text'] == source.replace('Time stood still', 'The clock stopped')
    assert works.read_draft(work['id'], work['active_draft_id'])['text'] == source
    assert await editorial.repair(work['id'], run['id'], finding['id'], data) == receipt
    with pytest.raises(CatalogError) as replay:
        await editorial.repair(work['id'], run['id'], finding['id'], {**data, 'replacement': 'Other'})
    assert replay.value.status == 409
    assert editorial.get(work['id'])['runs'][0]['stale']
    restored = works.restore(work['id'], {'revision': promoted['work']['revision'], 'target_revision': 2})
    assert restored['active_draft_id'] == work['active_draft_id']
    assert works.get(work['id'])['text'] == source
    assert editorial.get(work['id'])['runs'][0]['stale']


@pytest.mark.parametrize('changes', [{'start': -1}, {'end': 0}, {'work_revision': 1}, {'check_ids': []}, {'check_ids': ['unknown']}, {'check_ids': ['prose.cliches'] * 2}, {'provider': 'other'}, {'home': 'other'}])
def test_invalid_run_no_writes(tmp_path, changes):
    works, editorial, work, source = fixture(tmp_path)
    with pytest.raises(CatalogError):
        editorial.run(work['id'], payload(source, **changes))
    assert editorial.get(work['id'])['runs'] == []
    assert works.get(work['id'])['revision'] == 2


async def test_missing_source_and_foreign_finding_cannot_create_repair(tmp_path):
    works, editorial, work, source = fixture(tmp_path)
    run = await editorial.run_async(work['id'], payload(source))
    data = {'request_id': 'repair', 'work_revision': 2, 'replacement': 'Changed'}
    with pytest.raises(CatalogError) as unknown:
        await editorial.repair(work['id'], run['id'], 'missing', data)
    assert unknown.value.status == 404
    works.artifacts.delete(run['artifact_id'])
    assert editorial.get(work['id'])['runs'][0]['missing']
    with pytest.raises(CatalogError) as missing:
        await editorial.repair(work['id'], run['id'], run['findings'][0]['id'], data)
    assert missing.value.status == 404
    assert editorial.polishing.list(work['id'])['items'] == []


def test_concurrent_run_replay_retains_one_immutable_record(tmp_path):
    works, editorial, work, source = fixture(tmp_path)
    data = payload(source)
    def execute(_):
        return EditorialStore(WorkStore(tmp_path)).run(work['id'], data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert results[0] == results[1]
    assert len(editorial.get(work['id'])['runs']) == 1


async def test_actual_http_native_registry_run_repair_and_home_isolation(tmp_path):
    works, editorial, work, source = fixture(tmp_path)
    app = web.Application()
    app[STORE] = IngredientStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        root = f'/api/capabilities/creative/works/{work["id"]}/editorial'
        response = await client.get(root)
        assert response.status == 200
        assert len((await response.json())['catalog']) == 80
        response = await client.post(root + '/runs', json=payload(source))
        assert response.status == 200
        run = await response.json()
        response = await client.post(root + '/runs/' + run['id'] + '/findings/' + run['findings'][0]['id'] + '/repair', json={'request_id': 'http-repair', 'work_revision': 2, 'replacement': 'The clock stopped'})
        assert response.status == 200
        receipt = await response.json()
        assert receipt['proposal']['mode'] == 'authored'
        assert (await client.get(root + '?home=other')).status == 400
        assert (await client.get('/api/capabilities/creative/works/missing/editorial')).status == 404
    provider = CreativeToolProvider(tmp_path)
    result = await provider.invoke('creative_work_editorial_get', {'id': work['id']})
    assert result.success
    assert json.loads(result.output)['runs'][0]['id'] == run['id']
    invoked = await provider.invoke('creative_work_editorial_run', {'id': work['id'], 'payload': payload(source)})
    assert invoked.success
    native_run = json.loads(invoked.output)
    repaired = await provider.invoke('creative_work_editorial_repair', {'id': work['id'], 'run_id': native_run['id'], 'finding_id': native_run['findings'][0]['id'], 'payload': {'request_id': 'native', 'work_revision': 2, 'replacement': 'The clock stopped'}})
    assert repaired.success
    assert json.loads(repaired.output)['proposal']['base_revision'] == 2
    foreign = CreativeToolProvider(tmp_path / 'foreign')
    denied = await foreign.invoke('creative_work_editorial_get', {'id': work['id']})
    assert not denied.success and denied.metadata['status'] == 404
