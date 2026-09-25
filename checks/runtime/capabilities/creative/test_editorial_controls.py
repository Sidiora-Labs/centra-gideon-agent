import asyncio
import json
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, WORKS, register
from gideon.workspace.capabilities.creative.editorial import EditorialStore
from gideon.workspace.capabilities.creative.editorial_controls import EditorialControlsStore
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider, SCHEMAS
from gideon.workspace.capabilities.creative.works import WorkStore


SOURCE_ONE = 'Time stood still in the station. Mara kept the last sentence unchanged.'
SOURCE_TWO = 'Rain crossed the observatory windows. The brass key stayed with Mara.'


def standalone(home, source=SOURCE_ONE):
    works = WorkStore(home)
    work = works.create({'request_id': 'standalone-work', 'title': 'Standalone'})
    work = works.draft(work['id'], {'request_id': 'standalone-draft', 'revision': 1, 'text': source})['work']
    return works, EditorialStore(works), work


def series_fixture(home):
    series = SeriesStore(home)
    record = series.create({
        'request_id': 'series', 'title': 'Two chapter series', 'synopsis': 'Mara follows the key.',
        'volumes': [{'id': 'volume', 'title': 'Volume', 'chapters': [
            {'id': 'chapter-one', 'title': 'One', 'prompt': 'Open at the station.'},
            {'id': 'chapter-two', 'title': 'Two', 'prompt': 'Continue at the observatory.'},
        ]}],
        'arcs': [{'id': 'main-arc', 'title': 'Key arc', 'summary': 'Follow the key.',
                  'chapter_ids': ['chapter-one', 'chapter-two']}],
    })
    first = series.prepare(record['id'], 'chapter-one', {'revision': record['revision']})
    second = series.prepare(record['id'], 'chapter-two', {'revision': record['revision']})
    first = series.works.draft(first['id'], {'request_id': 'draft-one', 'revision': 1, 'text': SOURCE_ONE})['work']
    second = series.works.draft(second['id'], {'request_id': 'draft-two', 'revision': 1, 'text': SOURCE_TWO})['work']
    return series, EditorialStore(series.works), record, first, second


def configure(editorial, work, **changes):
    payload = {
        'request_id': changes.pop('request_id', str(uuid4())),
        'work_revision': work['revision'],
        'policy_revision': changes.pop('policy_revision', editorial.controls.state(work['id'])['policy']['revision']),
        'readiness_gate': changes.pop('readiness_gate', 'block_any'),
        'checks': changes.pop('checks', {}),
        **changes,
    }
    return editorial.controls.configure(work['id'], payload, {row['id'] for row in editorial.catalog(work['id'])})


def custom_payload(work, operation='create', **changes):
    return {
        'request_id': changes.pop('request_id', str(uuid4())),
        'work_revision': work['revision'],
        'operation': operation,
        'id': changes.pop('id', None),
        'revision': changes.pop('revision', None),
        'label': changes.pop('label', 'Continuity promise'),
        'prompt': changes.pop('prompt', 'Find a broken promise and quote exact prose.'),
        'scope': changes.pop('scope', 'series'),
        'severity': changes.pop('severity', 'high'),
        **changes,
    }


def run_payload(work, request='run', checks=None):
    return {'request_id': request, 'work_revision': work['revision'], 'start': 0,
            'end': len(SOURCE_ONE), 'check_ids': checks or ['prose.cliches']}


def test_policy_is_scoped_to_canonical_series_and_visible_from_every_chapter(tmp_path):
    _, editorial, series, first, second = series_fixture(tmp_path)
    saved = configure(editorial, first, request_id='policy', readiness_gate='block_high', checks={
        'prose.cliches': {'enabled': False, 'severity': 'high'},
        'prose.filter-words': {'enabled': True, 'severity': 'low'},
    })
    assert saved['scope_id'] == series['id']
    assert saved['revision'] == 1
    assert saved['readiness_gate'] == 'block_high'
    assert editorial.controls.state(second['id'])['policy'] == saved
    catalog = {row['id']: row for row in editorial.catalog(second['id'])}
    assert catalog['prose.cliches']['enabled'] is False
    assert catalog['prose.cliches']['severity'] == 'high'
    assert catalog['prose.cliches']['severity_default'] == 'low'
    assert catalog['prose.filter-words']['enabled'] is True
    assert catalog['prose.filter-words']['severity'] == 'low'


def test_standalone_policy_has_explicit_scope_and_exact_replay(tmp_path):
    _, editorial, work = standalone(tmp_path)
    payload = {
        'request_id': 'same-policy', 'work_revision': work['revision'], 'policy_revision': 0,
        'readiness_gate': 'block_medium',
        'checks': {'prose.cliches': {'enabled': True, 'severity': 'high'}},
    }
    known = {row['id'] for row in editorial.catalog(work['id'])}
    first = editorial.controls.configure(work['id'], payload, known)
    assert first['scope_id'] == 'work:' + work['id']
    assert editorial.controls.configure(work['id'], payload, known) == first
    with pytest.raises(CatalogError) as conflict:
        editorial.controls.configure(work['id'], {**payload, 'readiness_gate': 'block_any'}, known)
    assert conflict.value.status == 409
    with pytest.raises(CatalogError) as stale:
        configure(editorial, work, policy_revision=0)
    assert stale.value.status == 409


@pytest.mark.parametrize('changes,message', [
    ({'readiness_gate': 'never'}, 'readiness'),
    ({'checks': []}, 'overrides'),
    ({'checks': {'missing': {'enabled': True, 'severity': 'low'}}}, 'Unknown'),
    ({'checks': {'prose.cliches': {'enabled': 'yes', 'severity': 'low'}}}, 'requires'),
    ({'checks': {'prose.cliches': {'enabled': True, 'severity': 'critical'}}}, 'requires'),
    ({'provider': 'override'}, 'Unexpected'),
])
def test_policy_rejects_unbounded_or_routing_inputs_without_writes(tmp_path, changes, message):
    _, editorial, work = standalone(tmp_path)
    with pytest.raises(CatalogError, match=message):
        configure(editorial, work, **changes)
    assert editorial.controls.state(work['id'])['policy']['revision'] == 0


def test_custom_check_create_update_delete_preserves_revision_and_scope(tmp_path):
    _, editorial, series, first, second = series_fixture(tmp_path)
    created = editorial.controls.custom(first['id'], custom_payload(first, request_id='custom-create'))
    assert created['id'].startswith('custom-')
    assert created['scope_id'] == series['id']
    assert created['revision'] == 1
    assert created['kind'] == 'llm'
    assert editorial.controls.state(second['id'])['custom_checks'] == [created]
    updated = editorial.controls.custom(second['id'], custom_payload(
        second, 'update', request_id='custom-update', id=created['id'], revision=1,
        label='Promise audit', prompt='Quote an exact unresolved promise.', severity='medium'))
    assert updated['id'] == created['id']
    assert updated['revision'] == 2
    assert updated['label'] == 'Promise audit'
    catalog = {row['id']: row for row in editorial.catalog(first['id'])}
    assert catalog[created['id']]['prompt'] == 'Quote an exact unresolved promise.'
    assert catalog[created['id']]['severity'] == 'medium'
    deleted = editorial.controls.custom(first['id'], custom_payload(
        first, 'delete', request_id='custom-delete', id=created['id'], revision=2,
        label=None, prompt=None, scope=None, severity=None))
    assert deleted == {'id': created['id'], 'scope_id': series['id'], 'revision': 3, 'deleted': True}
    assert editorial.controls.state(first['id'])['custom_checks'] == []


def test_custom_request_replay_conflict_and_optimistic_revision(tmp_path):
    _, editorial, work = standalone(tmp_path)
    payload = custom_payload(work, request_id='custom-same')
    first = editorial.controls.custom(work['id'], payload)
    assert editorial.controls.custom(work['id'], payload) == first
    with pytest.raises(CatalogError) as conflict:
        editorial.controls.custom(work['id'], {**payload, 'label': 'Different'})
    assert conflict.value.status == 409
    with pytest.raises(CatalogError) as stale:
        editorial.controls.custom(work['id'], custom_payload(work, 'update', id=first['id'], revision=2))
    assert stale.value.status == 409
    assert editorial.controls.state(work['id'])['custom_checks'][0]['revision'] == 1


@pytest.mark.parametrize('changes,message', [
    ({'scope': 'global'}, 'scope'),
    ({'severity': 'critical'}, 'severity'),
    ({'label': ''}, 'text'),
    ({'prompt': 'x' * 8001}, 'text'),
    ({'operation': 'copy'}, 'operation'),
    ({'model': 'override'}, 'Unexpected'),
])
def test_custom_checks_reject_invalid_definitions_and_model_overrides(tmp_path, changes, message):
    _, editorial, work = standalone(tmp_path)
    payload = custom_payload(work)
    payload.update(changes)
    with pytest.raises(CatalogError, match=message):
        editorial.controls.custom(work['id'], payload)
    assert editorial.controls.state(work['id'])['custom_checks'] == []


def test_disabled_checks_are_explicit_and_readiness_uses_effective_severity(tmp_path):
    _, editorial, work = standalone(tmp_path)
    configure(editorial, work, request_id='disabled', readiness_gate='block_any', checks={
        'prose.cliches': {'enabled': False, 'severity': 'high'},
    })
    disabled = editorial.run(work['id'], run_payload(work, 'disabled-run'))
    assert disabled['results'] == [{'check_id': 'prose.cliches', 'status': 'skipped', 'reason': 'disabled_by_editorial_policy'}]
    assert disabled['findings'] == []
    assert disabled['readiness'] == 'incomplete'
    configure(editorial, work, request_id='advisory', policy_revision=1, readiness_gate='block_high', checks={
        'prose.cliches': {'enabled': True, 'severity': 'low'},
    })
    advisory = editorial.run(work['id'], run_payload(work, 'advisory-run'))
    assert advisory['findings'][0]['quote'] == 'Time stood still'
    assert advisory['findings'][0]['severity'] == 'low'
    assert advisory['readiness'] == 'advisory_findings'
    configure(editorial, work, request_id='blocking', policy_revision=2, readiness_gate='block_high', checks={
        'prose.cliches': {'enabled': True, 'severity': 'high'},
    })
    blocking = editorial.run(work['id'], run_payload(work, 'blocking-run'))
    assert blocking['findings'][0]['severity'] == 'high'
    assert blocking['readiness'] == 'review_required'


@pytest.mark.parametrize(('gate', 'severities', 'expected'), [
    ('block_high', ['low', 'medium'], 'advisory_findings'),
    ('block_high', ['low', 'high'], 'review_required'),
    ('block_medium', ['low'], 'advisory_findings'),
    ('block_medium', ['medium'], 'review_required'),
    ('block_any', ['low'], 'review_required'),
])
def test_readiness_gates_are_transparent(gate, severities, expected, tmp_path):
    works = WorkStore(tmp_path)
    controls = EditorialControlsStore(works)
    assert controls.readiness([{'severity': severity} for severity in severities], gate) == expected


def test_rank_requires_two_canonical_drafts_before_any_model_call(tmp_path):
    _, editorial, work = standalone(tmp_path)
    with pytest.raises(CatalogError, match='at least two'):
        asyncio.run(editorial.controls.review(work['id'], {
            'request_id': 'rank', 'work_revision': work['revision'], 'mode': 'rank'}))
    assert editorial.controls.state(work['id'])['reviews'] == []


def test_explicit_judge_uses_configured_adapter_or_records_external_gap(tmp_path):
    _, editorial, work = standalone(tmp_path)
    review = asyncio.run(editorial.controls.review(work['id'], {
        'request_id': 'review-judge', 'work_revision': work['revision'], 'mode': 'judge'}))
    assert review['mode'] == 'judge'
    assert review['status'] in ('completed', 'external_unavailable')
    assert review['source_revisions'] == {work['id']: work['revision']}
    if review['status'] == 'external_unavailable':
        assert review['output'] is None
        assert review['reason'] == 'configured_model_unavailable_or_invalid_output'
    else:
        assert review['output'] is not None
    state = editorial.controls.state(work['id'])['reviews'][0]
    assert state['id'] == review['id']
    assert state['stale'] is False
    assert state['missing_source_ids'] == []


def test_review_replay_conflict_and_source_revision_staleness(tmp_path):
    works, editorial, work = standalone(tmp_path)
    payload = {'request_id': 'review-replay', 'work_revision': work['revision'], 'mode': 'judge'}
    first = asyncio.run(editorial.controls.review(work['id'], payload))
    assert asyncio.run(editorial.controls.review(work['id'], payload)) == first
    with pytest.raises(CatalogError) as conflict:
        asyncio.run(editorial.controls.review(work['id'], {**payload, 'mode': 'panel'}))
    assert conflict.value.status == 409
    newer = works.draft(work['id'], {'request_id': 'newer', 'revision': work['revision'], 'text': SOURCE_ONE + ' New.'})['work']
    state = editorial.controls.state(work['id'])['reviews'][0]
    assert state['stale'] is True
    assert state['source_revisions'] == {work['id']: work['revision']}
    assert newer['revision'] == work['revision'] + 1


def test_typed_judge_validation_requires_exact_source_anchors(tmp_path):
    _, _, work = standalone(tmp_path)
    sources = [{'work_id': work['id'], 'revision': work['revision'], 'text': SOURCE_ONE}]
    valid = {'score': 72, 'verdict': 'Revise', 'concerns': [
        {'work_id': work['id'], 'quote': 'Time stood still', 'reason': 'Familiar phrase'},
    ]}
    assert EditorialControlsStore._validate_review('judge', valid, sources) == valid
    with pytest.raises(ValueError, match='anchor'):
        EditorialControlsStore._validate_review('judge', {**valid, 'concerns': [
            {'work_id': work['id'], 'quote': 'invented quote', 'reason': 'Unsupported'},
        ]}, sources)
    with pytest.raises(ValueError, match='shape'):
        EditorialControlsStore._validate_review('judge', {**valid, 'score': 101}, sources)


def test_typed_panel_validation_requires_three_personas_and_grounded_quotes(tmp_path):
    _, _, work = standalone(tmp_path)
    sources = [{'work_id': work['id'], 'revision': work['revision'], 'text': SOURCE_ONE}]
    valid = {'responses': [
        {'persona': 'genre reader', 'verdict': 'clear', 'concern_quote': ''},
        {'persona': 'line reader', 'verdict': 'familiar', 'concern_quote': 'Time stood still'},
        {'persona': 'continuity reader', 'verdict': 'clear', 'concern_quote': ''},
    ], 'consensus': ['Opening needs review']}
    assert EditorialControlsStore._validate_review('panel', valid, sources) == valid
    with pytest.raises(ValueError, match='shape'):
        EditorialControlsStore._validate_review('panel', {**valid, 'responses': valid['responses'][:2]}, sources)
    invalid = json.loads(json.dumps(valid))
    invalid['responses'][1]['concern_quote'] = 'not in manuscript'
    with pytest.raises(ValueError, match='anchor'):
        EditorialControlsStore._validate_review('panel', invalid, sources)


def test_typed_rank_validation_requires_each_candidate_exactly_once(tmp_path):
    _, editorial, _, first, second = series_fixture(tmp_path)
    sources = editorial.controls._sources(first['id'], 'rank')[1]
    valid = {'ranking': [
        {'work_id': second['id'], 'rationale': 'Weaker ending'},
        {'work_id': first['id'], 'rationale': 'Clearer opening'},
    ], 'weakest': [second['id']]}
    assert EditorialControlsStore._validate_review('rank', valid, sources) == valid
    with pytest.raises(ValueError, match='identity'):
        EditorialControlsStore._validate_review('rank', {**valid, 'ranking': valid['ranking'][:1]}, sources)
    with pytest.raises(ValueError, match='identity'):
        EditorialControlsStore._validate_review('rank', {**valid, 'weakest': ['foreign']}, sources)


def test_reviewed_cut_preview_apply_and_undo_preserve_unrelated_text(tmp_path):
    works, editorial, work = standalone(tmp_path)
    run = editorial.run(work['id'], run_payload(work, 'cut-source'))
    finding = run['findings'][0]
    cut = asyncio.run(editorial.controls.cut(editorial, work['id'], run['id'], finding['id'], {
        'request_id': 'cut-preview', 'work_revision': work['revision']}))
    assert cut['status'] == 'previewed'
    assert cut['quote'] == 'Time stood still'
    proposal = editorial.polishing.get(work['id'], cut['proposal']['id'])
    assert proposal['original'] == 'Time stood still'
    assert proposal['replacement'] == ''
    assert proposal['missing'] is False
    assert works.get(work['id'])['text'] == SOURCE_ONE
    applied = editorial.controls.apply_cut(work['id'], cut['id'], {'revision': work['revision']})
    assert applied['status'] == 'applied'
    assert applied['applied_revision'] == work['revision'] + 1
    shortened = works.get(work['id'])
    assert 'Time stood still' not in shortened['text']
    assert shortened['text'].endswith('Mara kept the last sentence unchanged.')
    undone = editorial.controls.undo_cut(work['id'], cut['id'], {'revision': shortened['revision']})
    assert undone['status'] == 'undone'
    assert undone['undo_revision'] == shortened['revision'] + 1
    restored = works.get(work['id'])
    assert restored['text'] == SOURCE_ONE
    assert restored['active_draft_id'] == work['active_draft_id']


def test_cut_preview_is_idempotent_and_rejects_foreign_or_stale_sources(tmp_path):
    works, editorial, work = standalone(tmp_path)
    run = editorial.run(work['id'], run_payload(work, 'cut-run'))
    finding = run['findings'][0]
    payload = {'request_id': 'same-cut', 'work_revision': work['revision']}
    first = asyncio.run(editorial.controls.cut(editorial, work['id'], run['id'], finding['id'], payload))
    assert asyncio.run(editorial.controls.cut(editorial, work['id'], run['id'], finding['id'], payload)) == first
    with pytest.raises(CatalogError) as conflict:
        asyncio.run(editorial.controls.cut(editorial, work['id'], run['id'], finding['id'], {**payload, 'work_revision': 1}))
    assert conflict.value.status == 409
    with pytest.raises(CatalogError) as missing:
        asyncio.run(editorial.controls.cut(editorial, work['id'], run['id'], 'missing', {
            'request_id': 'missing-cut', 'work_revision': work['revision']}))
    assert missing.value.status == 404
    newer = works.draft(work['id'], {'request_id': 'changed', 'revision': work['revision'], 'text': SOURCE_ONE + ' Changed.'})['work']
    with pytest.raises(CatalogError) as stale:
        asyncio.run(editorial.controls.cut(editorial, work['id'], run['id'], finding['id'], {
            'request_id': 'stale-cut', 'work_revision': newer['revision']}))
    assert stale.value.status == 409


def test_cut_apply_and_undo_enforce_lifecycle_and_revisions(tmp_path):
    _, editorial, work = standalone(tmp_path)
    run = editorial.run(work['id'], run_payload(work, 'lifecycle-run'))
    finding = run['findings'][0]
    cut = asyncio.run(editorial.controls.cut(editorial, work['id'], run['id'], finding['id'], {
        'request_id': 'lifecycle-cut', 'work_revision': work['revision']}))
    with pytest.raises(CatalogError) as early:
        editorial.controls.undo_cut(work['id'], cut['id'], {'revision': work['revision']})
    assert early.value.status == 409
    with pytest.raises(CatalogError) as stale:
        editorial.controls.apply_cut(work['id'], cut['id'], {'revision': work['revision'] + 1})
    assert stale.value.status == 409
    with pytest.raises(CatalogError) as missing:
        editorial.controls.apply_cut(work['id'], 'missing', {'revision': work['revision']})
    assert missing.value.status == 404


async def test_real_http_policy_custom_review_and_cut_routes(tmp_path):
    app = web.Application()
    app[STORE] = IngredientStore(tmp_path)
    register(app)
    works = app[WORKS]
    work = works.create({'request_id': 'http-work', 'title': 'HTTP editorial'})
    work = works.draft(work['id'], {'request_id': 'http-draft', 'revision': 1, 'text': SOURCE_ONE})['work']
    root = f'/api/capabilities/creative/works/{work["id"]}/editorial'
    async with TestClient(TestServer(app)) as client:
        response = await client.patch(root + '/policy', json={
            'request_id': 'http-policy', 'work_revision': work['revision'], 'policy_revision': 0,
            'readiness_gate': 'block_high', 'checks': {'prose.cliches': {'enabled': True, 'severity': 'low'}},
        })
        assert response.status == 200
        assert (await response.json())['revision'] == 1
        response = await client.post(root + '/custom-checks', json={
            'request_id': 'http-custom', 'work_revision': work['revision'], 'label': 'Promise audit',
            'prompt': 'Find broken promises.', 'scope': 'series', 'severity': 'medium',
        })
        assert response.status == 200
        custom = await response.json()
        assert custom['id'].startswith('custom-')
        response = await client.post(root + '/runs', json=run_payload(work, 'http-run'))
        assert response.status == 200
        run = await response.json()
        assert run['readiness'] == 'advisory_findings'
        finding = run['findings'][0]
        response = await client.post(root + f'/runs/{run["id"]}/findings/{finding["id"]}/cut', json={
            'request_id': 'http-cut', 'work_revision': work['revision']})
        assert response.status == 200
        cut = await response.json()
        assert cut['status'] == 'previewed'
        response = await client.post(root + f'/cuts/{cut["id"]}/apply', json={'revision': work['revision']})
        assert response.status == 200
        applied = await response.json()
        assert applied['status'] == 'applied'
        response = await client.post(root + f'/cuts/{cut["id"]}/undo', json={'revision': applied['applied_revision']})
        assert response.status == 200
        assert (await response.json())['status'] == 'undone'
        state = await (await client.get(root)).json()
        assert state['controls']['policy']['readiness_gate'] == 'block_high'
        assert state['controls']['custom_checks'][0]['id'] == custom['id']
        assert state['controls']['cuts'][0]['status'] == 'undone'
    assert works.get(work['id'])['text'] == SOURCE_ONE


async def test_native_policy_custom_and_cut_use_same_authoritative_state(tmp_path):
    provider = CreativeToolProvider(tmp_path)
    work = provider.works.create({'request_id': 'native-work', 'title': 'Native editorial'})
    work = provider.works.draft(work['id'], {'request_id': 'native-draft', 'revision': 1, 'text': SOURCE_ONE})['work']
    configured = await provider.invoke('creative_work_editorial_policy_configure', {'id': work['id'], 'payload': {
        'request_id': 'native-policy', 'work_revision': work['revision'], 'policy_revision': 0,
        'readiness_gate': 'block_medium', 'checks': {'prose.cliches': {'enabled': True, 'severity': 'high'}},
    }})
    assert configured.success
    assert json.loads(configured.output)['revision'] == 1
    custom = await provider.invoke('creative_work_editorial_custom_create', {'id': work['id'], 'payload': {
        'request_id': 'native-custom', 'work_revision': work['revision'], 'label': 'Promise audit',
        'prompt': 'Find broken promises.', 'scope': 'work', 'severity': 'low',
    }})
    assert custom.success
    custom_record = json.loads(custom.output)
    assert custom_record['scope_id'] == 'work:' + work['id']
    run_result = await provider.invoke('creative_work_editorial_run', {'id': work['id'], 'payload': run_payload(work, 'native-run')})
    run = json.loads(run_result.output)
    cut_result = await provider.invoke('creative_work_editorial_cut_preview', {
        'id': work['id'], 'run_id': run['id'], 'finding_id': run['findings'][0]['id'],
        'payload': {'request_id': 'native-cut', 'work_revision': work['revision']},
    })
    assert cut_result.success
    cut = json.loads(cut_result.output)
    applied = await provider.invoke('creative_work_editorial_cut_apply', {
        'id': work['id'], 'cut_id': cut['id'], 'payload': {'revision': work['revision']}})
    assert applied.success
    applied_record = json.loads(applied.output)
    undone = await provider.invoke('creative_work_editorial_cut_undo', {
        'id': work['id'], 'cut_id': cut['id'], 'payload': {'revision': applied_record['applied_revision']}})
    assert undone.success
    assert provider.works.get(work['id'])['text'] == SOURCE_ONE


def test_native_schemas_close_provider_model_home_and_runtime_overrides():
    names = [
        'creative_work_editorial_policy_configure', 'creative_work_editorial_custom_create',
        'creative_work_editorial_custom_update', 'creative_work_editorial_custom_delete',
        'creative_work_editorial_review', 'creative_work_editorial_cut_preview',
        'creative_work_editorial_cut_apply', 'creative_work_editorial_cut_undo',
    ]
    for name in names:
        schema = SCHEMAS[name]
        assert schema['additionalProperties'] is False
        assert schema['required'][0] == 'id'
        payload = schema['properties']['payload']
        assert payload['additionalProperties'] is False
        for forbidden in ('provider', 'providerId', 'model', 'credentials', 'api_key', 'home', 'runtime'):
            assert forbidden not in schema['properties']
            assert forbidden not in payload['properties']
