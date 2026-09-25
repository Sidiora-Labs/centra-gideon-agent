import asyncio
import json
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.context_checks import deterministic
from gideon.workspace.capabilities.creative.editorial import EditorialStore
from gideon.workspace.capabilities.creative.editorial_context import FAMILIES, family_for, validate_context
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider, SCHEMAS


SOURCE = ('Mara entered the observatory. The brass key hung at her belt. '
          '"We leave tonight," Mara said. Rain crossed the western window.')


def fixture(home):
    works = WorkStore(home)
    work = works.create({'request_id': 'context-work', 'title': 'Context manuscript'})
    work = works.draft(work['id'], {'request_id': 'context-draft', 'revision': 1, 'text': SOURCE})['work']
    return works, EditorialStore(works), work


def bind(editorial, work, family, data, request=None):
    return editorial.context.bind(work['id'], {
        'request_id': request or f'{family}-{uuid4()}',
        'work_revision': work['revision'],
        'family': family,
        'schema_version': 1,
        'data': data,
    })


VALID = {
    'canon': {
        'characters': [
            {'id': 'mara', 'name': 'Mara', 'relationships': [{'target': 'missing'}]},
            {'id': 'maran', 'name': 'Maran'},
        ],
        'objects': [{'id': 'key', 'name': 'brass key', 'significant': True}],
        'rules': ['Magic requires a spoken price.'],
    },
    'cast': {'characters': [{'id': 'mara', 'name': 'Mara'}]},
    'scene': {'scenes': [{'id': 'opening', 'start': 0, 'end': 28, 'goal': 'enter', 'shots': [
        {'continuity_out': 'night'}, {'continuity_in': 'day'},
    ]}]},
    'pov': {'policy': 'Close third person.', 'allowed': ['Mara'], 'transitions': []},
    'arc': {'arcs': [{'id': 'main', 'name': 'The key'}], 'themes': ['trust'],
            'ticking_clock': {'deadline': 'midnight', 'consequence': 'the gate closes'}, 'reader_map': []},
    'world': {'rules': ['Magic requires a spoken price.'], 'places': [{'id': 'observatory', 'name': 'Observatory'}]},
    'research': {'claims': [{'id': 'rain', 'claim': 'Rain falls.', 'source': 'weather notes'}]},
    'comic': {'pages': [{'number': 1, 'panels': [{'speaker': 'Mara', 'dialogue': 'We leave tonight'}]}]},
}


def run_payload(work, check_ids, request=None):
    return {'request_id': request or str(uuid4()), 'work_revision': work['revision'],
            'start': 0, 'end': len(SOURCE), 'check_ids': check_ids}


def test_family_mapping_covers_context_families_and_leaves_manuscript_checks_unbound():
    expected = {
        'naming.dissimilar-names': 'canon',
        'cast.representation-balance': 'cast',
        'visual.shot-continuity': 'scene',
        'pov.justified': 'pov',
        'arc.transitions': 'arc',
        'world.cost-free-power': 'world',
        'research.fact-accuracy': 'research',
        'comic.prose-sync': 'comic',
    }
    assert {check: family_for(check) for check in expected} == expected
    assert family_for('prose.telling-emotion') is None
    assert family_for('dialogue.on-the-nose') is None
    assert set(expected.values()) == set(FAMILIES)


@pytest.mark.parametrize('family', FAMILIES)
def test_every_typed_family_binds_an_immutable_versioned_json_artifact(tmp_path, family):
    works, editorial, work = fixture(tmp_path)
    record = bind(editorial, work, family, VALID[family], request=f'bind-{family}')
    assert record['family'] == family
    assert record['revision'] == 1
    assert record['schema_version'] == 1
    assert record['artifact_version'] == 1
    artifact = works.artifacts.get(record['artifact_id'], version=record['artifact_version'])
    assert artifact is not None
    assert artifact.readonly
    assert artifact.kind == 'json'
    body = json.loads(artifact.content)
    assert body == {'schema_version': 1, 'family': family, 'data': VALID[family]}
    state = editorial.context.current(work['id'], family)
    assert state['status'] == 'available'
    assert state['data'] == VALID[family]


def test_rebinding_creates_a_new_canonical_revision_without_mutating_history(tmp_path):
    works, editorial, work = fixture(tmp_path)
    first = bind(editorial, work, 'world', VALID['world'], request='world-one')
    changed = {'rules': ['No magic.'], 'places': [{'id': 'observatory', 'name': 'Observatory'}]}
    second = bind(editorial, work, 'world', changed, request='world-two')
    assert first['revision'] == 1
    assert second['revision'] == 2
    assert first['artifact_id'] != second['artifact_id']
    assert json.loads(works.artifacts.get(first['artifact_id'], version=1).content)['data'] == VALID['world']
    assert editorial.context.current(work['id'], 'world')['data'] == changed
    listed = editorial.context.list(work['id'])
    assert next(row for row in listed['families'] if row['family'] == 'world')['revision'] == 2


def test_binding_replay_is_exact_and_conflicting_request_is_rejected(tmp_path):
    _, editorial, work = fixture(tmp_path)
    payload = {'request_id': 'same', 'work_revision': work['revision'], 'family': 'cast',
               'schema_version': 1, 'data': VALID['cast']}
    first = editorial.context.bind(work['id'], payload)
    assert editorial.context.bind(work['id'], payload) == first
    with pytest.raises(CatalogError) as conflict:
        editorial.context.bind(work['id'], {**payload, 'data': {'characters': []}})
    assert conflict.value.status == 409
    assert editorial.context.current(work['id'], 'cast')['revision'] == 1


@pytest.mark.parametrize(('family', 'data', 'message'), [
    ('canon', {'characters': [{'id': 'mara'}], 'objects': [], 'rules': []}, 'requires name'),
    ('cast', {'characters': 'Mara'}, 'characters must be an array'),
    ('scene', {'scenes': [{'id': 'a', 'start': 3, 'end': 2}]}, 'start before end'),
    ('pov', {'policy': '', 'allowed': [], 'transitions': [], 'extra': True}, 'Unexpected fields'),
    ('arc', {'arcs': [{'id': 'a'}], 'themes': [], 'ticking_clock': {}, 'reader_map': []}, 'requires name'),
    ('world', {'rules': [], 'places': [{'id': 'p'}]}, 'requires name'),
    ('research', {'claims': [{'id': 'c', 'claim': 'x'}]}, 'requires source'),
    ('comic', {'pages': [{'number': 0, 'panels': []}]}, 'positive integer'),
])
def test_malformed_context_is_rejected_before_artifact_creation(tmp_path, family, data, message):
    works, editorial, work = fixture(tmp_path)
    before = len(works.artifacts.list())
    with pytest.raises(CatalogError, match=message):
        bind(editorial, work, family, data)
    assert len(works.artifacts.list()) == before
    assert editorial.context.current(work['id'], family)['status'] == 'missing'


def test_unknown_schema_family_and_stale_work_revision_are_rejected(tmp_path):
    _, editorial, work = fixture(tmp_path)
    base = {'request_id': 'bad', 'work_revision': work['revision'], 'family': 'world',
            'schema_version': 1, 'data': VALID['world']}
    with pytest.raises(CatalogError, match='Unknown'):
        editorial.context.bind(work['id'], {**base, 'family': 'dream'})
    with pytest.raises(CatalogError, match='Unsupported'):
        editorial.context.bind(work['id'], {**base, 'request_id': 'schema', 'schema_version': 2})
    with pytest.raises(CatalogError) as stale:
        editorial.context.bind(work['id'], {**base, 'request_id': 'stale', 'work_revision': 1})
    assert stale.value.status == 409


def test_missing_and_deleted_context_are_truthful_not_clear(tmp_path):
    works, editorial, work = fixture(tmp_path)
    missing = editorial.run(work['id'], run_payload(work, ['naming.dissimilar-names']))
    assert missing['findings'] == []
    assert missing['results'] == [{'check_id': 'naming.dissimilar-names', 'status': 'skipped', 'reason': 'canon_context_missing'}]
    assert missing['readiness'] == 'incomplete'
    record = bind(editorial, work, 'canon', VALID['canon'])
    works.artifacts.delete(record['artifact_id'])
    deleted = editorial.run(work['id'], run_payload(work, ['naming.dissimilar-names']))
    assert deleted['results'][0]['reason'] == 'canon_context_missing'
    assert editorial.context.current(work['id'], 'canon')['reason'] == 'bound_artifact_missing'


def test_contextual_deterministic_checks_emit_exact_manuscript_anchors(tmp_path):
    _, editorial, work = fixture(tmp_path)
    bind(editorial, work, 'canon', VALID['canon'])
    run = editorial.run(work['id'], run_payload(work, [
        'naming.dissimilar-names', 'relationships.dangling-target', 'objects.unattached-significant',
    ]))
    assert all(result['status'] == 'completed' for result in run['results'])
    assert {finding['check_id'] for finding in run['findings']} == {
        'naming.dissimilar-names', 'relationships.dangling-target', 'objects.unattached-significant',
    }
    for finding in run['findings']:
        assert SOURCE[finding['start']:finding['end']] == finding['quote']
    assert next(f for f in run['findings'] if f['check_id'] == 'naming.dissimilar-names')['quote'] == 'Mara'
    assert run['context_bindings'][0]['family'] == 'canon'


def test_scene_and_comic_rules_use_canonical_ranges_and_dialogue(tmp_path):
    _, editorial, work = fixture(tmp_path)
    bind(editorial, work, 'scene', VALID['scene'])
    comic = {'pages': [{'number': 1, 'panels': [
        {'dialogue': 'We leave tonight'},
        {'speaker': 'Mara', 'dialogue': ' '.join(['word'] * 40)},
    ]}]}
    bind(editorial, work, 'comic', comic)
    run = editorial.run(work['id'], run_payload(work, [
        'scene.component-balance', 'visual.shot-continuity', 'comic.balloon-attribution', 'comic.lettering-density',
    ]))
    assert [result['status'] for result in run['results']] == ['completed'] * 4
    ids = {finding['check_id'] for finding in run['findings']}
    assert {'scene.component-balance', 'visual.shot-continuity', 'comic.balloon-attribution'} <= ids
    assert 'comic.lettering-density' not in ids
    for finding in run['findings']:
        assert SOURCE[finding['start']:finding['end']] == finding['quote']


def test_run_is_marked_stale_when_a_bound_context_family_advances(tmp_path):
    _, editorial, work = fixture(tmp_path)
    bind(editorial, work, 'world', VALID['world'], request='world-v1')
    run = editorial.run(work['id'], run_payload(work, ['world.cost-free-power']))
    assert run['context_bindings'][0]['revision'] == 1
    assert not editorial.get(work['id'])['runs'][0]['stale']
    bind(editorial, work, 'world', {'rules': ['Power is free.'], 'places': []}, request='world-v2')
    refreshed = editorial.get(work['id'])['runs'][0]
    assert refreshed['stale']
    assert refreshed['stale_context_families'] == ['world']


def test_model_check_uses_real_configured_adapter_or_records_external_gap(tmp_path):
    _, editorial, work = fixture(tmp_path)
    bind(editorial, work, 'research', VALID['research'])
    run = editorial.run(work['id'], run_payload(work, ['research.fact-accuracy']))
    result = run['results'][0]
    assert result['status'] in ('completed', 'external_unavailable')
    if result['status'] == 'external_unavailable':
        assert result['reason'] == 'configured_model_unavailable_or_invalid_output'
        assert run['findings'] == []
        assert run['readiness'] == 'incomplete'
    else:
        for finding in run['findings']:
            assert SOURCE[finding['start']:finding['end']] == finding['quote']


def test_model_checks_do_not_accept_provider_routing_or_credentials(tmp_path):
    _, editorial, work = fixture(tmp_path)
    bind(editorial, work, 'research', VALID['research'])
    base = run_payload(work, ['research.fact-accuracy'])
    for forbidden in ('provider', 'model', 'credentials', 'api_key', 'home', 'runtime'):
        with pytest.raises(CatalogError, match='Unexpected fields'):
            editorial.run(work['id'], {**base, 'request_id': forbidden, forbidden: 'override'})


def test_pure_deterministic_rules_do_not_invent_unanchored_findings():
    canon = {'status': 'available', 'data': {
        'characters': [{'id': 'nobody', 'name': 'Absent', 'relationships': [{'target': 'missing'}]}],
        'objects': [{'id': 'lost', 'name': 'Unmentioned', 'significant': True}], 'rules': [],
    }}
    assert deterministic('relationships.dangling-target', SOURCE, canon) == []
    assert deterministic('objects.unattached-significant', SOURCE, canon) == []
    assert deterministic('naming.dissimilar-names', SOURCE, canon) == []


async def test_http_context_binding_run_and_staleness_use_real_server(tmp_path):
    works, _, work = fixture(tmp_path)
    app = web.Application()
    app[STORE] = IngredientStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        root = f'/api/capabilities/creative/works/{work["id"]}/editorial'
        response = await client.post(root + '/context', json={
            'request_id': 'http-canon', 'work_revision': work['revision'], 'family': 'canon',
            'schema_version': 1, 'data': VALID['canon'],
        })
        assert response.status == 200
        binding = await response.json()
        assert binding['artifact_version'] == 1
        response = await client.post(root + '/runs', json=run_payload(work, ['naming.dissimilar-names'], request='http-run'))
        assert response.status == 200
        run = await response.json()
        assert run['findings'][0]['quote'] == 'Mara'
        state = await (await client.get(root)).json()
        canon = next(row for row in state['contexts'] if row['family'] == 'canon')
        assert canon['status'] == 'available'
        assert state['runs'][0]['context_bindings'][0]['artifact_id'] == binding['artifact_id']
        assert (await client.post(root + '/context', json={
            'request_id': 'http-invalid', 'work_revision': work['revision'], 'family': 'comic',
            'schema_version': 1, 'data': {'pages': [{'number': 0, 'panels': []}]},
        })).status == 400
    assert works.get(work['id'])['text'] == SOURCE


def test_context_state_survives_store_reopen(tmp_path):
    works, editorial, work = fixture(tmp_path)
    records = {family: bind(editorial, work, family, VALID[family]) for family in FAMILIES}
    reopened = EditorialStore(WorkStore(tmp_path))
    state = reopened.get(work['id'])
    assert {row['family'] for row in state['contexts'] if row['status'] == 'available'} == set(FAMILIES)
    for row in state['contexts']:
        assert row['artifact_id'] == records[row['family']]['artifact_id']
        assert row['artifact_version'] == 1


def test_catalog_availability_tracks_each_bound_family(tmp_path):
    _, editorial, work = fixture(tmp_path)
    before = {row['id']: row for row in editorial.get(work['id'])['catalog']}
    assert before['prose.cliches']['availability'] == 'available'
    assert before['prose.telling-emotion']['availability'] == 'configured_model_required'
    assert before['naming.dissimilar-names']['availability'] == 'requires_context'
    assert before['visual.eyeline-match']['availability'] == 'requires_context'
    bind(editorial, work, 'canon', VALID['canon'])
    after_canon = {row['id']: row for row in editorial.get(work['id'])['catalog']}
    assert after_canon['naming.dissimilar-names']['availability'] == 'available'
    assert after_canon['relationships.dangling-target']['availability'] == 'available'
    assert after_canon['visual.eyeline-match']['availability'] == 'requires_context'
    bind(editorial, work, 'scene', VALID['scene'])
    after_scene = {row['id']: row for row in editorial.get(work['id'])['catalog']}
    assert after_scene['visual.eyeline-match']['availability'] == 'available'
    assert after_scene['scene.component-balance']['availability'] == 'available'
    assert after_scene['comic.panel-rhythm']['availability'] == 'requires_context'


def test_contextual_findings_respect_requested_coverage(tmp_path):
    _, editorial, work = fixture(tmp_path)
    bind(editorial, work, 'canon', VALID['canon'])
    full = editorial.run(work['id'], run_payload(work, ['naming.dissimilar-names']))
    assert full['findings'][0]['quote'] == 'Mara'
    late = editorial.run(work['id'], {
        'request_id': 'late-coverage', 'work_revision': work['revision'],
        'start': 40, 'end': len(SOURCE), 'check_ids': ['naming.dissimilar-names'],
    })
    assert late['results'][0]['status'] == 'completed'
    assert late['results'][0]['finding_count'] == 0
    assert late['findings'] == []
    assert late['readiness'] == 'incomplete'


def test_context_artifact_body_is_schema_bound_not_bare_user_json(tmp_path):
    works, editorial, work = fixture(tmp_path)
    record = bind(editorial, work, 'research', VALID['research'])
    artifact = works.artifacts.get(record['artifact_id'], version=1)
    body = json.loads(artifact.content)
    assert set(body) == {'schema_version', 'family', 'data'}
    assert body['schema_version'] == record['schema_version']
    assert body['family'] == record['family']
    assert body['data']['claims'][0]['source'] == 'weather notes'
    assert 'request_id' not in body
    assert 'work_revision' not in body


def test_binding_does_not_change_work_or_canonical_draft(tmp_path):
    works, editorial, work = fixture(tmp_path)
    draft_before = works.read_draft(work['id'], work['active_draft_id'])
    bind(editorial, work, 'arc', VALID['arc'])
    current = works.get(work['id'])
    draft_after = works.read_draft(work['id'], work['active_draft_id'])
    assert current['revision'] == work['revision']
    assert current['active_draft_id'] == work['active_draft_id']
    assert current['text'] == SOURCE
    assert draft_after == draft_before
    assert len(works.drafts(work['id'])['items']) == 1


def test_empty_but_typed_context_is_available_and_does_not_invent_findings(tmp_path):
    _, editorial, work = fixture(tmp_path)
    empty = {'characters': [], 'objects': [], 'rules': []}
    bind(editorial, work, 'canon', empty)
    run = editorial.run(work['id'], run_payload(work, [
        'naming.dissimilar-names', 'roster.economy', 'relationships.dangling-target',
        'objects.unattached-significant',
    ]))
    assert all(row['status'] == 'completed' for row in run['results'])
    assert all(row['finding_count'] == 0 for row in run['results'])
    assert run['findings'] == []
    assert run['readiness'] == 'selected_checks_clear'


def test_multiple_family_run_pins_each_exact_binding_once(tmp_path):
    _, editorial, work = fixture(tmp_path)
    canon = bind(editorial, work, 'canon', VALID['canon'])
    scene = bind(editorial, work, 'scene', VALID['scene'])
    comic = bind(editorial, work, 'comic', VALID['comic'])
    run = editorial.run(work['id'], run_payload(work, [
        'naming.dissimilar-names', 'relationships.dangling-target',
        'scene.component-balance', 'visual.shot-continuity',
        'comic.balloon-attribution', 'comic.panel-rhythm',
    ]))
    assert [row['family'] for row in run['context_bindings']] == ['canon', 'scene', 'comic']
    pinned = {row['family']: row for row in run['context_bindings']}
    assert pinned['canon']['artifact_id'] == canon['artifact_id']
    assert pinned['scene']['artifact_id'] == scene['artifact_id']
    assert pinned['comic']['artifact_id'] == comic['artifact_id']
    assert all(row['artifact_version'] == 1 for row in pinned.values())


@pytest.mark.parametrize(('check_id', 'family'), [
    ('naming.dissimilar-names', 'canon'),
    ('cast.representation-balance', 'cast'),
    ('scene.component-balance', 'scene'),
    ('pov.justified', 'pov'),
    ('arc.transitions', 'arc'),
    ('world.unforeshadowed-solution', 'world'),
    ('research.fact-accuracy', 'research'),
    ('comic.page-turn-beats', 'comic'),
])
def test_each_context_family_has_a_distinct_truthful_missing_result(tmp_path, check_id, family):
    _, editorial, work = fixture(tmp_path)
    run = editorial.run(work['id'], run_payload(work, [check_id]))
    assert run['results'] == [{
        'check_id': check_id,
        'status': 'skipped',
        'reason': f'{family}_context_missing',
    }]
    assert run['findings'] == []
    assert run['context_bindings'] == []
    assert run['readiness'] == 'incomplete'


async def test_native_context_binding_and_async_run_share_authoritative_store(tmp_path):
    works, _, work = fixture(tmp_path)
    provider = CreativeToolProvider(tmp_path)
    bound = await provider.invoke('creative_work_editorial_context_bind', {
        'id': work['id'],
        'payload': {
            'request_id': 'native-context',
            'work_revision': work['revision'],
            'family': 'canon',
            'schema_version': 1,
            'data': VALID['canon'],
        },
    })
    assert bound.success
    binding = json.loads(bound.output)
    assert binding['family'] == 'canon'
    assert binding['artifact_version'] == 1
    run_result = await provider.invoke('creative_work_editorial_run', {
        'id': work['id'],
        'payload': run_payload(work, ['naming.dissimilar-names'], request='native-context-run'),
    })
    assert run_result.success
    run = json.loads(run_result.output)
    assert run['results'][0]['status'] == 'completed'
    assert run['findings'][0]['quote'] == 'Mara'
    assert run['context_bindings'][0]['artifact_id'] == binding['artifact_id']
    assert works.get(work['id'])['text'] == SOURCE


async def test_native_binding_rejects_unknown_arguments_and_foreign_home(tmp_path):
    _, _, work = fixture(tmp_path)
    provider = CreativeToolProvider(tmp_path)
    extra = await provider.invoke('creative_work_editorial_context_bind', {
        'id': work['id'],
        'payload': {
            'request_id': 'native-extra', 'work_revision': work['revision'],
            'family': 'canon', 'schema_version': 1, 'data': VALID['canon'],
        },
        'provider': 'forbidden',
    })
    assert not extra.success
    assert extra.metadata['status'] == 400
    foreign = CreativeToolProvider(tmp_path / 'foreign')
    denied = await foreign.invoke('creative_work_editorial_context_bind', {
        'id': work['id'],
        'payload': {
            'request_id': 'foreign', 'work_revision': work['revision'],
            'family': 'canon', 'schema_version': 1, 'data': VALID['canon'],
        },
    })
    assert not denied.success
    assert denied.metadata['status'] == 404


def test_native_schema_closes_routing_and_payload_shapes():
    schema = SCHEMAS['creative_work_editorial_context_bind']
    assert schema['additionalProperties'] is False
    assert schema['required'] == ['id', 'payload']
    payload_schema = schema['properties']['payload']
    assert payload_schema['additionalProperties'] is False
    assert set(payload_schema['required']) == {
        'request_id', 'work_revision', 'family', 'schema_version', 'data',
    }
    assert payload_schema['properties']['schema_version'] == {'type': 'integer', 'const': 1}
    assert payload_schema['properties']['family']['enum'] == list(FAMILIES)
    for forbidden in ('provider', 'model', 'credentials', 'api_key', 'home', 'runtime'):
        assert forbidden not in payload_schema['properties']
