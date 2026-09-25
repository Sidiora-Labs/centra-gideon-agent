import json
import math
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.voice import VoiceStore, fingerprint, METRICS
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider

SHORT = 'The key is red. ' * 15
LONG = 'The traveler carried a heavy iron key along the narrow road beside the silent river. ' * 5
EXEMPLAR = '"Walk now." ' * 30


def fixture(home, texts=(SHORT, LONG), sample=EXEMPLAR):
    series = SeriesStore(home)
    artifact = series.works.artifacts.create(name='Chosen voice', kind='markdown', content=sample)
    author = series.works.authors.create({'request_id': 'author', 'title': 'Literary author', 'sample_refs': [{'artifact_id': artifact.slug, 'artifact_version': 1}]})
    record = series.create({'request_id': 'series', 'title': 'Voice series', 'author_ref': {'id': author['id'], 'revision': 1},
                            'volumes': [{'id': 'volume', 'title': 'Volume', 'chapters': [{'id': f'chapter{i}', 'title': f'Chapter {i}', 'prompt': 'Observe voice'} for i in range(len(texts))]}]})
    works = []
    for i, source in enumerate(texts):
        work = series.prepare(record['id'], f'chapter{i}', {'revision': 1})
        if source:
            work = series.works.draft(work['id'], {'request_id': f'draft{i}', 'revision': 1, 'text': source})['work']
        works.append(work)
    return series, VoiceStore(series), record, works, artifact


def test_exact_sentence_paragraph_rhythm_fixture():
    result = fingerprint('One two.\n\nOne two three four five six.')
    assert result['words'] == 8
    assert result['sentences'] == 2
    assert result['paragraphs'] == 2
    metrics = result['metrics']
    assert metrics['sentence_mean'] == 4
    assert metrics['sentence_std'] == 2
    assert metrics['sentence_cv'] == 0.5
    assert metrics['paragraph_mean'] == 4
    assert metrics['paragraph_std'] == 2
    assert metrics['fragment_pct'] == 50
    assert metrics['long_pct'] == 0
    assert metrics['opener_pct'] == 100
    assert metrics['dialogue_pct'] == metrics['emdash_rate'] == metrics['abstract_rate'] == metrics['simile_rate'] == 0
    assert set(metrics) == set(METRICS)


def test_unicode_apostrophes_quoted_dialogue_and_wells_are_exact():
    result = fingerprint('😀 نور said “Don’t run.”\n\nنور—stay!', {'names': ['نور'], 'verbs': ['don’t', 'stay']})
    assert result['words'] == 6
    assert result['sentences'] == 2
    assert result['paragraphs'] == 2
    assert result['metrics']['sentence_mean'] == 3
    assert result['metrics']['sentence_std'] == 1
    assert result['metrics']['sentence_cv'] == 0.3333
    assert result['metrics']['dialogue_pct'] == 33.3333
    assert result['metrics']['emdash_rate'] == 166.6667
    assert result['metrics']['well:names'] == 333.3333
    assert result['metrics']['well:verbs'] == 333.3333
    assert result['metrics']['opener_pct'] == 100


def test_english_heuristics_long_sentence_and_empty_unknown_source_distinction():
    source = 'Realization like a bird as cold as stone ship nation.'
    result = fingerprint(source)
    assert result['words'] == 10
    assert result['metrics']['abstract_rate'] == 200
    assert result['metrics']['simile_rate'] == 200
    assert result['metrics']['long_pct'] == 0
    long = fingerprint(' '.join(['word'] * 31) + '.')
    assert long['metrics']['long_pct'] == 100
    assert long['metrics']['fragment_pct'] == 0
    empty = fingerprint('😀 ... !!!')
    assert empty['words'] == empty['sentences'] == empty['paragraphs'] == 0
    assert all(v == 0 for v in empty['metrics'].values())
    assert all(math.isfinite(v) for v in empty['metrics'].values())


def test_actual_canonical_sources_drafted_matrix_and_config_history(tmp_path):
    series, voice, record, works, sample = fixture(tmp_path)
    report = voice.report(record['id'])
    assert report['formula_version'] == 'unicode-prose-v1'
    assert report['config']['revision'] == 0
    assert report['gate'] is None
    assert report['rows'][0]['fingerprint'] == fingerprint(SHORT)
    assert report['rows'][1]['fingerprint'] == fingerprint(LONG)
    assert report['rows'][0]['draft_id'] == works[0]['active_draft_id']
    assert report['rows'][0]['artifact_version'] == 1
    assert report['rows'][0]['characters'] == len(SHORT)
    assert report['samples'] == [{'artifact_id': sample.slug, 'artifact_version': 1, 'missing': False, 'title': 'Chosen voice'}]
    assert report['baseline']['sentence_mean'] == {'drafted_mean': 9.5, 'center': 9.5, 'std': 5.5}
    assert report['findings'] == []
    config = voice.configure(record['id'], {'revision': 0, 'series_revision': 1, 'z_threshold': 0.5, 'wells': {'Metal': ['KEY', 'iron', 'key']}})
    assert config['wells'] == {'metal': ['iron', 'key']}
    assert config['revision'] == 1
    updated = voice.report(record['id'])
    assert updated['config_history'] == [config]
    assert updated['rows'][0]['fingerprint']['metrics']['well:metal'] == 250
    rhythm = [f for f in updated['findings'] if f['metric'] == 'sentence_mean']
    assert [f['direction'] for f in rhythm] == ['lower', 'higher']
    assert [f['z'] for f in rhythm] == [-1, 1]
    assert VoiceStore(SeriesStore(tmp_path)).export(record['id']) == updated
    assert series.works.get(works[0]['id'])['text'] == SHORT


def test_exemplar_and_blended_centers_keep_population_draft_spread(tmp_path):
    series, voice, record, works, sample = fixture(tmp_path)
    voice.configure(record['id'], {'revision': 0, 'series_revision': 1, 'baseline': 'exemplars'})
    exemplar = voice.report(record['id'])
    assert exemplar['baseline']['sentence_mean'] == {'drafted_mean': 9.5, 'center': 2, 'std': 5.5}
    assert exemplar['exemplar']['words'] == 60
    assert exemplar['gate'] is None
    assert any(f['kind'] == 'uniform_corpus_difference' and f['metric'] == 'dialogue_pct' for f in exemplar['findings'])
    voice.configure(record['id'], {'revision': 1, 'series_revision': 1, 'baseline': 'blended'})
    blended = voice.report(record['id'])
    assert blended['baseline']['sentence_mean'] == {'drafted_mean': 9.5, 'center': 5.75, 'std': 5.5}
    assert len(blended['config_history']) == 2
    assert blended['config_history'][0]['baseline'] == 'exemplars'
    assert blended['config_history'][1]['baseline'] == 'blended'
    assert series.works.export(works[0]['id'])['revision'] == 2


def test_pin_exemplar_version_after_source_update_and_author_profile_change(tmp_path):
    series, voice, record, works, sample = fixture(tmp_path)
    voice.configure(record['id'], {'revision': 0, 'series_revision': 1, 'baseline': 'exemplars'})
    initial = voice.report(record['id'])
    series.works.artifacts.update(sample.slug, content=LONG, snapshot=True)
    author = series.works.authors.get(record['author_ref']['id'])
    series.works.authors.update(author['id'], {'revision': 1, 'sample_refs': [{'artifact_id': sample.slug, 'artifact_version': 2}]})
    unchanged = voice.report(record['id'])
    assert unchanged['exemplar'] == initial['exemplar']
    assert unchanged['samples'][0]['artifact_version'] == 1
    assert unchanged['author_ref']['revision'] == 1
    assert unchanged['baseline'] == initial['baseline']
    assert series.works.artifacts.get(sample.slug).content == LONG


def test_missing_and_thin_sources_are_visible_and_never_silent_fallback(tmp_path):
    series, voice, record, works, sample = fixture(tmp_path, texts=(SHORT, 'Small.', None), sample='Tiny sample.')
    voice.configure(record['id'], {'revision': 0, 'series_revision': 1, 'baseline': 'exemplars'})
    thin = voice.report(record['id'])
    assert thin['gate'] == 'exemplar_missing_or_small'
    assert thin['findings'] == []
    assert [r['gate'] for r in thin['rows']] == [None, 'below_min_words', 'not_drafted']
    assert thin['rows'][1]['fingerprint']['words'] == 1
    assert thin['rows'][2]['fingerprint'] is None
    assert thin['baseline']['sentence_mean']['center'] is None
    assert thin['baseline']['sentence_mean']['drafted_mean'] == 4
    series.works.artifacts.delete(sample.slug)
    missing = voice.report(record['id'])
    assert missing['samples'][0]['missing']
    assert missing['exemplar'] is None
    assert missing['gate'] == 'exemplar_missing_or_small'
    draft = series.works.read_draft(works[0]['id'], works[0]['active_draft_id'])
    series.works.artifacts.delete(draft['artifact_id'])
    empty = voice.report(record['id'])
    assert empty['rows'][0]['gate'] == 'source_missing'
    assert empty['baseline']['sentence_mean'] == {'drafted_mean': None, 'center': None, 'std': None}


def test_updated_current_draft_changes_metrics_and_previous_draft_stays_immutable(tmp_path):
    series, voice, record, works, sample = fixture(tmp_path)
    before = voice.report(record['id'])
    newer = series.works.draft(works[0]['id'], {'request_id': 'rewrite', 'revision': 2, 'text': LONG})['work']
    after = voice.report(record['id'])
    assert after['rows'][0]['draft_id'] == newer['active_draft_id']
    assert after['rows'][0]['fingerprint']['metrics']['sentence_mean'] == 15
    assert before['rows'][0]['fingerprint']['metrics']['sentence_mean'] == 4
    assert after['baseline']['sentence_mean']['std'] == 0
    assert after['findings'] == []
    assert series.works.read_draft(works[0]['id'], works[0]['active_draft_id'])['text'] == SHORT


@pytest.mark.parametrize('changes', [
    {'revision': 1}, {'series_revision': 2}, {'min_words': 0}, {'min_chapters': 1},
    {'z_threshold': float('nan')}, {'z_threshold': True}, {'z_threshold': 11},
    {'baseline': 'automatic'}, {'wells': {'x': ['two words']}}, {'wells': {'x': []}},
    {'wells': {'x': ['123']}}, {'home': 'other'}, {'provider': 'override'},
])
def test_invalid_config_preserves_default_and_sources(tmp_path, changes):
    series, voice, record, works, sample = fixture(tmp_path)
    with pytest.raises(CatalogError):
        voice.configure(record['id'], {'revision': 0, 'series_revision': 1, **changes})
    assert voice.report(record['id'])['config']['revision'] == 0
    assert voice.report(record['id'])['config_history'] == []
    assert series.works.get(works[0]['id'])['text'] == SHORT


def test_real_concurrent_config_writes_require_current_revision(tmp_path):
    series, voice, record, works, sample = fixture(tmp_path)
    def update(threshold):
        try:
            return VoiceStore(SeriesStore(tmp_path)).configure(record['id'], {'revision': 0, 'series_revision': 1, 'z_threshold': threshold})
        except CatalogError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [1, 3]))
    assert len([r for r in results if isinstance(r, dict)]) == 1
    assert 409 in results
    assert len(voice.report(record['id'])['config_history']) == 1


async def test_actual_http_and_native_share_config_and_private_source_report(tmp_path):
    series, voice, record, works, sample = fixture(tmp_path)
    app = web.Application()
    app[STORE] = IngredientStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        root = f'/api/capabilities/creative/series/{record["id"]}/voice'
        response = await client.get(root)
        assert response.status == 200
        assert (await response.json())['rows'][0]['fingerprint'] == fingerprint(SHORT)
        response = await client.patch(root, json={'revision': 0, 'series_revision': 1, 'baseline': 'blended'})
        assert response.status == 200
        assert (await response.json())['revision'] == 1
        response = await client.get(root + '/export')
        exported = await response.json()
        assert exported['config']['baseline'] == 'blended'
        assert exported['baseline']['sentence_mean']['center'] == 5.75
        assert (await client.get(root + '?home=private')).status == 400
        assert (await client.patch(root, json={'revision': 1, 'series_revision': 1, 'provider': 'foreign'})).status == 400
        assert (await client.get('/api/capabilities/creative/series/missing/voice')).status == 404
    provider = CreativeToolProvider(tmp_path)
    result = await provider.invoke('creative_series_voice_report', {'id': record['id']})
    assert result.success
    assert json.loads(result.output) == exported
    configured = await provider.invoke('creative_series_voice_configure', {'id': record['id'], 'payload': {'revision': 1, 'series_revision': 1, 'baseline': 'drafted'}})
    assert configured.success
    assert json.loads(configured.output)['revision'] == 2
    exported_tool = await provider.invoke('creative_series_voice_export', {'id': record['id']})
    assert exported_tool.success
    assert json.loads(exported_tool.output)['config']['baseline'] == 'drafted'
    foreign = CreativeToolProvider(tmp_path / 'other')
    denied = await foreign.invoke('creative_series_voice_report', {'id': record['id']})
    assert not denied.success
    assert denied.metadata['status'] == 404
