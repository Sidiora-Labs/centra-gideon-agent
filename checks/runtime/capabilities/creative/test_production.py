import asyncio
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative_production import PRODUCTION, register
from gideon.workspace.capabilities.creative.production import SeriesProductionStore
from gideon.workspace.capabilities.creative.production_tools import CreativeProductionToolProvider, SCHEMAS
from gideon.workspace.capabilities.creative.store import CatalogError


CLEAN = 'Mara entered the station and checked the brass key before dawn.'
FINDING = 'Time stood still in the station. Mara checked the brass key before dawn.'


def create_series(store, prepared=True, chapters=1):
    plans = [{'id': f'chapter-{index}', 'title': f'Chapter {index}', 'prompt': f'Write chapter {index}.'}
             for index in range(1, chapters + 1)]
    record = store.series.create({
        'request_id': f'series-{chapters}-{prepared}', 'title': 'Production series', 'synopsis': 'Mara follows a key.',
        'volumes': [{'id': 'volume', 'title': 'Volume', 'chapters': plans}],
        'arcs': [{'id': 'arc', 'title': 'Key arc', 'summary': 'Follow the key.',
                  'chapter_ids': [item['id'] for item in plans]}],
    })
    works = []
    if prepared:
        for chapter in plans:
            works.append(store.series.prepare(record['id'], chapter['id'], {'revision': record['revision']}))
    return record, works


def start(store, series, request='run', mode='authored', attempts=2):
    return store.start(series['id'], {'request_id': request, 'series_revision': series['revision'],
                                      'mode': mode, 'max_attempts': attempts})


def prepare_candidate(store, series, run, work, body=CLEAN, request='candidate'):
    paused = asyncio.run(store.advance(series['id'], run['id']))
    assert paused['status'] == 'paused'
    assert paused['pause_reason'] == 'authored_candidate_required'
    candidate = store.submit(series['id'], run['id'], {'request_id': request, 'work_revision': work['revision'],
                                                        'text': body, 'note': 'Authored source'})
    assert candidate['status'] == 'awaiting_approval'
    assert candidate['approval'] == {'kind': 'draft', 'chapter_id': 'chapter-1'}
    return candidate


def test_start_is_durable_idempotent_and_pins_canonical_identity(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series, request='same')
    replay = start(store, series, request='same')
    assert replay == run
    assert run['series_id'] == series['id']
    assert run['series_revision'] == series['revision']
    assert run['canon_refs'] == {'author_ref': None, 'universe_ref': None}
    assert run['source_pins'] == []
    assert run['editorial_runs'] == []
    assert run['status'] == 'running'
    assert run['max_attempts'] == 2
    assert run['events'] == []
    state = store.get(series['id'], run['id'])
    assert state['chapter_status'][0]['work_id'] == works[0]['id']
    assert state['chapter_status'][0]['work_revision'] == works[0]['revision']
    assert state['stale'] is False
    with pytest.raises(CatalogError) as conflict:
        start(store, series, request='same', attempts=3)
    assert conflict.value.status == 409


@pytest.mark.parametrize(('changes', 'message'), [
    ({'mode': 'fake'}, 'Choose'),
    ({'max_attempts': 0}, 'integer'),
    ({'max_attempts': 4}, 'integer'),
    ({'series_revision': 2}, 'changed'),
    ({'provider': 'override'}, 'Unexpected'),
    ({'model': 'override'}, 'Unexpected'),
    ({'home': '/tmp/other'}, 'Unexpected'),
])
def test_start_rejects_unbounded_or_routing_inputs(tmp_path, changes, message):
    store = SeriesProductionStore(tmp_path)
    series, _ = create_series(store)
    payload = {'request_id': 'invalid', 'series_revision': series['revision'], 'mode': 'authored', 'max_attempts': 2}
    payload.update(changes)
    with pytest.raises(CatalogError, match=message):
        store.start(series['id'], payload)
    assert store.list(series['id']) == {'items': []}


def test_unprepared_chapters_pause_with_exact_residual(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, _ = create_series(store, prepared=False, chapters=2)
    run = start(store, series)
    paused = asyncio.run(store.advance(series['id'], run['id']))
    assert paused['status'] == 'paused'
    assert paused['pause_reason'] == 'chapters_not_prepared'
    assert paused['residual'] == ['chapter-1', 'chapter-2']
    assert paused['cursor'] == 0
    assert paused['candidate'] is None


def test_series_revision_change_pauses_without_generation(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, _ = create_series(store)
    run = start(store, series)
    updated = store.series.update(series['id'], {'revision': series['revision'], 'synopsis': 'Changed synopsis.'})
    paused = asyncio.run(store.advance(series['id'], run['id']))
    assert paused['status'] == 'paused'
    assert paused['pause_reason'] == 'series_changed'
    assert paused['residual'] == [{'series_revision': updated['revision']}]
    assert store.get(series['id'], run['id'])['stale'] is True


def test_authored_candidate_is_immutable_and_requires_approval(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    awaiting = prepare_candidate(store, series, run, works[0])
    candidate = awaiting['candidate']
    artifact = store.series.works.artifacts.get(candidate['artifact_id'], version=1)
    assert artifact is not None
    assert artifact.readonly
    assert artifact.content == CLEAN
    assert artifact.kind == 'markdown'
    assert candidate['work_id'] == works[0]['id']
    assert candidate['work_revision'] == works[0]['revision']
    assert candidate['base_draft_id'] is None
    assert store.series.works.get(works[0]['id'])['active_draft_id'] is None
    with pytest.raises(CatalogError, match='not running'):
        asyncio.run(store.advance(series['id'], run['id']))


def test_candidate_submit_validates_state_revision_and_shape(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    with pytest.raises(CatalogError, match='not currently requested'):
        store.submit(series['id'], run['id'], {'request_id': 'early', 'work_revision': 1, 'text': CLEAN, 'note': ''})
    asyncio.run(store.advance(series['id'], run['id']))
    with pytest.raises(CatalogError) as stale:
        store.submit(series['id'], run['id'], {'request_id': 'stale', 'work_revision': 2, 'text': CLEAN, 'note': ''})
    assert stale.value.status == 409
    with pytest.raises(CatalogError, match='text'):
        store.submit(series['id'], run['id'], {'request_id': 'empty', 'work_revision': works[0]['revision'], 'text': '', 'note': ''})
    with pytest.raises(CatalogError, match='Unexpected'):
        store.submit(series['id'], run['id'], {'request_id': 'override', 'work_revision': works[0]['revision'], 'text': CLEAN, 'note': '', 'provider': 'x'})


def test_approval_promotes_only_the_pinned_candidate_into_canonical_work(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    awaiting = prepare_candidate(store, series, run, works[0])
    applied = store.approve(series['id'], run['id'], {'decision': 'apply'})
    assert applied['status'] == 'running'
    assert applied['approval'] is None
    assert applied['candidate']['applied_revision'] == works[0]['revision'] + 1
    assert applied['candidate']['applied_draft_id']
    assert applied['source_pins'][0]['chapter_id'] == 'chapter-1'
    assert applied['source_pins'][0]['work_revision'] == works[0]['revision'] + 1
    assert applied['source_pins'][0]['draft_id'] == applied['candidate']['applied_draft_id']
    assert applied['source_pins'][0]['artifact_version'] == 1
    assert applied['source_pins'][0]['content_hash'] == hashlib.sha256(CLEAN.encode()).hexdigest()
    work = store.series.works.get(works[0]['id'])
    assert work['text'] == CLEAN
    assert work['revision'] == works[0]['revision'] + 1
    assert work['active_draft_id'] == applied['candidate']['applied_draft_id']
    assert awaiting['candidate']['artifact_id'] != work['active_draft']['artifact_id']


def test_promotion_pin_survives_restart_and_resolves_to_exact_canonical_source(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    prepare_candidate(store, series, run, works[0])
    applied = store.approve(series['id'], run['id'], {'decision': 'apply'})
    reopened = SeriesProductionStore(tmp_path)
    state = reopened.get(series['id'], run['id'])
    assert state['status'] == 'paused'
    assert state['pause_reason'] == 'runtime_restarted'
    assert state['source_pins'] == applied['source_pins']
    assert state['sources'][0] == {**state['source_pins'][0], 'missing': False, 'content_hash': state['source_pins'][0]['content_hash']}


def test_promotion_failure_rolls_back_canonical_work_and_source_pin(tmp_path, monkeypatch):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    prepare_candidate(store, series, run, works[0])

    def fail_write(db, record):
        raise RuntimeError('production record write failed')

    original_write = store._write
    monkeypatch.setattr(store, '_write', fail_write)
    with pytest.raises(RuntimeError, match='production record write failed'):
        store.approve(series['id'], run['id'], {'decision': 'apply'})
    monkeypatch.setattr(store, '_write', original_write)
    work = store.series.works.get(works[0]['id'])
    state = store.get(series['id'], run['id'])
    assert work['revision'] == works[0]['revision']
    assert work['active_draft_id'] is None
    assert state['status'] == 'awaiting_approval'
    assert state['source_pins'] == []
    reopened = SeriesProductionStore(tmp_path)
    recovered = reopened.approve(series['id'], run['id'], {'decision': 'apply'})
    assert recovered['source_pins'][0]['content_hash'] == hashlib.sha256(CLEAN.encode()).hexdigest()
    assert reopened.get(series['id'], run['id'])['sources'][0]['missing'] is False


@pytest.mark.parametrize(('decision', 'message'), [
    ('keep', 'match'), ('review', 'match'), ('reject', 'Unknown'),
])
def test_draft_approval_rejects_wrong_decisions(tmp_path, decision, message):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    prepare_candidate(store, series, run, works[0])
    with pytest.raises(CatalogError, match=message):
        store.approve(series['id'], run['id'], {'decision': decision})
    assert store.series.works.get(works[0]['id'])['active_draft_id'] is None


def test_clean_generated_draft_is_editorially_pinned_then_kept_by_explicit_approval(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    prepare_candidate(store, series, run, works[0], body=CLEAN)
    store.approve(series['id'], run['id'], {'decision': 'apply'})
    quality = asyncio.run(store.advance(series['id'], run['id']))
    assert quality['status'] == 'awaiting_approval'
    assert quality['approval']['kind'] == 'quality'
    assert quality['approval']['finding_count'] == 0
    assert quality['residual'] == []
    assert quality['editorial_runs'][0]['work_id'] == works[0]['id']
    assert quality['editorial_runs'][0]['readiness'] == 'selected_checks_clear'
    kept = store.approve(series['id'], run['id'], {'decision': 'keep'})
    assert kept['status'] == 'running'
    assert kept['cursor'] == 1
    assert kept['candidate'] is None
    assert store.series.get(series['id'])['chapter_status'][0]['stage'] == 'reviewed'
    done = asyncio.run(store.advance(series['id'], run['id']))
    assert done['status'] == 'done'
    assert done['cursor'] == 1
    assert done['events'][-1]['type'] == 'completed'


def test_generated_draft_with_findings_can_be_rolled_back_without_orphan_success(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series)
    prepare_candidate(store, series, run, works[0], body=FINDING)
    applied = store.approve(series['id'], run['id'], {'decision': 'apply'})
    quality = asyncio.run(store.advance(series['id'], run['id']))
    assert quality['approval']['kind'] == 'quality'
    assert quality['approval']['finding_count'] >= 1
    assert any(item['quote'] == 'Time stood still' for item in quality['residual'])
    rolled = store.rollback(series['id'], run['id'])
    assert rolled['status'] == 'paused'
    assert rolled['pause_reason'] == 'quality_regression_rolled_back'
    assert rolled['candidate'] is None
    work = store.series.works.get(works[0]['id'])
    assert work['revision'] == applied['candidate']['applied_revision'] + 1
    assert work['active_draft_id'] is None
    assert work['text'] == ''
    assert store.series.get(series['id'])['chapter_status'][0]['stage'] == 'planned'


def test_existing_authored_draft_gets_review_approval_without_generated_candidate(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    drafted = store.series.works.draft(works[0]['id'], {'request_id': 'existing', 'revision': 1, 'text': CLEAN})['work']
    run = start(store, series)
    review = asyncio.run(store.advance(series['id'], run['id']))
    assert review['status'] == 'awaiting_approval'
    assert review['approval']['kind'] == 'chapter_review'
    assert review['approval']['work_revision'] == drafted['revision']
    assert review['candidate'] is None
    approved = store.approve(series['id'], run['id'], {'decision': 'review'})
    assert approved['cursor'] == 1
    assert store.series.get(series['id'])['chapter_status'][0]['stage'] == 'reviewed'


def test_pause_resume_cancel_are_durable_and_terminal_safe(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, _ = create_series(store)
    run = start(store, series)
    requested = store.control(series['id'], run['id'], 'pause')
    assert requested['pause_requested'] is True
    paused = asyncio.run(store.advance(series['id'], run['id']))
    assert paused['status'] == 'paused'
    assert paused['pause_reason'] == 'user_requested'
    resumed = store.control(series['id'], run['id'], 'resume')
    assert resumed['status'] == 'running'
    assert resumed['pause_requested'] is False
    canceled_request = store.control(series['id'], run['id'], 'cancel')
    assert canceled_request['cancel_requested'] is True
    canceled = asyncio.run(store.advance(series['id'], run['id']))
    assert canceled['status'] == 'canceled'
    assert canceled['residual'] == []
    with pytest.raises(CatalogError) as terminal:
        store.control(series['id'], run['id'], 'resume')
    assert terminal.value.status == 409


def test_store_reopen_turns_running_marker_into_resumable_pause(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, _ = create_series(store)
    run = start(store, series)
    reopened = SeriesProductionStore(tmp_path)
    state = reopened.get(series['id'], run['id'])
    assert state['status'] == 'paused'
    assert state['pause_reason'] == 'runtime_restarted'
    resumed = reopened.control(series['id'], run['id'], 'resume')
    assert resumed['status'] == 'running'
    assert resumed['events'][-1]['type'] == 'resumed'


def test_model_mode_uses_configured_adapter_and_never_invents_content(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, works = create_series(store)
    run = start(store, series, mode='model', attempts=1)
    result = asyncio.run(store.advance(series['id'], run['id']))
    assert result['status'] in ('awaiting_approval', 'exhausted')
    if result['status'] == 'exhausted':
        assert result['pause_reason'] == 'configured_model_failed'
        assert result['attempts'] == {'chapter-1': 1}
        assert result['candidate'] is None
        assert result['residual'][0]['chapter_id'] == 'chapter-1'
        assert store.series.works.get(works[0]['id'])['active_draft_id'] is None
    else:
        artifact = store.series.works.artifacts.get(result['candidate']['artifact_id'], version=1)
        assert artifact is not None
        assert artifact.content.strip()
        assert store.series.works.get(works[0]['id'])['active_draft_id'] is None


def test_model_failure_with_retry_budget_pauses_then_can_resume(tmp_path):
    store = SeriesProductionStore(tmp_path)
    series, _ = create_series(store)
    run = start(store, series, request='retry', mode='model', attempts=2)
    first = asyncio.run(store.advance(series['id'], run['id']))
    if first['status'] == 'paused':
        assert first['pause_reason'] == 'configured_model_failed'
        assert first['attempts'] == {'chapter-1': 1}
        resumed = store.control(series['id'], run['id'], 'resume')
        assert resumed['status'] == 'running'
    else:
        assert first['status'] == 'awaiting_approval'


def test_list_is_series_isolated_and_unknown_records_are_404(tmp_path):
    store = SeriesProductionStore(tmp_path)
    one, _ = create_series(store, chapters=1)
    two = store.series.create({'request_id': 'second', 'title': 'Second', 'synopsis': '',
        'volumes': [{'id': 'v2', 'title': 'V2', 'chapters': []}], 'arcs': []})
    run = start(store, one)
    assert [row['id'] for row in store.list(one['id'])['items']] == [run['id']]
    assert store.list(two['id']) == {'items': []}
    with pytest.raises(CatalogError) as missing:
        store.get(one['id'], 'missing')
    assert missing.value.status == 404
    with pytest.raises(CatalogError) as foreign:
        store.get(two['id'], run['id'])
    assert foreign.value.status == 404


async def test_real_http_start_advance_submit_approve_and_controls(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    store = app[PRODUCTION]
    series, works = create_series(store)
    root = f'/api/capabilities/creative/series/{series["id"]}/production'
    async with TestClient(TestServer(app)) as client:
        response = await client.post(root, json={'request_id': 'http', 'series_revision': 1, 'mode': 'authored', 'max_attempts': 2})
        assert response.status == 200
        run = await response.json()
        assert (await client.post(root + f'/{run["id"]}/advance', json={})).status == 200
        response = await client.post(root + f'/{run["id"]}/submit', json={
            'request_id': 'http-candidate', 'work_revision': works[0]['revision'], 'text': CLEAN, 'note': 'HTTP'})
        assert response.status == 200
        assert (await response.json())['status'] == 'awaiting_approval'
        response = await client.post(root + f'/{run["id"]}/approve', json={'decision': 'apply'})
        assert response.status == 200
        assert (await response.json())['status'] == 'running'
        response = await client.post(root + f'/{run["id"]}/advance', json={})
        assert response.status == 200
        assert (await response.json())['approval']['kind'] == 'quality'
        response = await client.post(root + f'/{run["id"]}/approve', json={'decision': 'keep'})
        assert response.status == 200
        assert (await response.json())['cursor'] == 1
        response = await client.post(root + f'/{run["id"]}/advance', json={})
        assert (await response.json())['status'] == 'done'
        listed = await (await client.get(root)).json()
        assert listed['items'][0]['status'] == 'done'
        state = await (await client.get(root + f'/{run["id"]}')).json()
        assert state['sources'][0]['artifact_version'] == 1
        assert state['sources'][0]['missing'] is False


async def test_native_provider_uses_same_durable_store_and_closed_schemas(tmp_path):
    provider = CreativeProductionToolProvider(tmp_path)
    series, works = create_series(provider.store)
    started = await provider.invoke('creative_production_start', {'series_id': series['id'], 'payload': {
        'request_id': 'native', 'series_revision': 1, 'mode': 'authored', 'max_attempts': 2}})
    assert started.success
    run = json.loads(started.output)
    advanced = await provider.invoke('creative_production_advance', {'series_id': series['id'], 'run_id': run['id']})
    assert advanced.success
    submitted = await provider.invoke('creative_production_submit', {'series_id': series['id'], 'run_id': run['id'], 'payload': {
        'request_id': 'native-candidate', 'work_revision': works[0]['revision'], 'text': CLEAN, 'note': 'Native'}})
    assert submitted.success
    approved = await provider.invoke('creative_production_approve', {'series_id': series['id'], 'run_id': run['id'], 'payload': {'decision': 'apply'}})
    assert approved.success
    listed = await provider.invoke('creative_production_list', {'series_id': series['id']})
    assert listed.success
    assert json.loads(listed.output)['items'][0]['candidate']['applied_revision'] == 2
    for name, schema in SCHEMAS.items():
        assert schema['additionalProperties'] is False
        for forbidden in ('provider', 'model', 'credentials', 'api_key', 'home', 'runtime'):
            assert forbidden not in schema['properties']
            payload = schema['properties'].get('payload')
            if payload:
                assert forbidden not in payload['properties']


async def test_native_rejects_unknown_tool_foreign_series_and_extra_arguments(tmp_path):
    provider = CreativeProductionToolProvider(tmp_path)
    series, _ = create_series(provider.store)
    unknown = await provider.invoke('creative_production_missing', {})
    assert not unknown.success
    assert unknown.metadata['status'] == 404
    foreign = await provider.invoke('creative_production_get', {'series_id': series['id'], 'run_id': 'missing'})
    assert not foreign.success
    assert foreign.metadata['status'] == 404
    extra = await provider.invoke('creative_production_list', {'series_id': series['id'], 'provider': 'override'})
    assert not extra.success
    assert extra.metadata['status'] == 400
