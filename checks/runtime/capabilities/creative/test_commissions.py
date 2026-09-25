import asyncio
import hashlib
import io
import json
import wave
from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.triggers.service import tick, to_iso
from gideon.automation.triggers.store import TriggerStore
from gideon.integrations.action_providers.base import ActionContext
from gideon.interfaces.dashboard.handlers.capabilities_creative_commissions import COMMISSIONS, register
from gideon.workspace.capabilities.creative.commission_tools import CommissionTools, SCHEMAS
from gideon.workspace.capabilities.creative.commissions import (
    CommissionActionProvider,
    CommissionStore,
    TRIGGER_PREFIX,
    _recurrence_next,
    digest,
)
from gideon.workspace.capabilities.creative.commission_dispatch import CommissionDispatcher, normalize_dispatch
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.store import CatalogError
from gideon.workspace.capabilities.creative.works import WorkStore


TEXT = 'Mara follows the brass key through the quiet station.'


def source(home, request='source'):
    works = WorkStore(home)
    work = works.create({'request_id': request, 'title': 'Commission source', 'kind': 'work', 'prompt': 'A key story.',
                         'author_ref': None, 'universe_ref': None, 'active_draft_id': None})
    drafted = works.draft(work['id'], {'request_id': request + '-draft', 'revision': 1, 'text': TEXT, 'note': 'Reviewed source'})
    return works, drafted['work'], drafted['draft']


def payload(work, request='commission', cadence=None, attempts=2):
    return {
        'request_id': request,
        'name': 'Standing key treatments',
        'target_ability': 'series',
        'brief': {'intent': 'Find a new visual direction without changing the manuscript.', 'genre': 'mystery',
                  'category': 'treatment', 'style': 'Quiet and precise.', 'constraints': {'rating': 'PG'},
                  'seed_refs': []},
        'cadence': cadence or {'kind': 'interval', 'seconds': 900, 'timezone': 'UTC'},
        'sources': [{'kind': 'work', 'id': work['id'], 'revision': work['revision']}],
        'steps': [
            {'id': 'verify', 'title': 'Verify source', 'operation': 'source.verify', 'depends_on': []},
            {'id': 'snapshot', 'title': 'Save treatment', 'operation': 'treatment.snapshot', 'depends_on': ['verify']},
        ],
        'enabled': True,
        'max_attempts': attempts,
    }


def make_store(tmp_path):
    direction = DirectionStore(tmp_path)
    triggers = TriggerStore(base_dir=tmp_path)
    return CommissionStore(tmp_path, direction=direction, triggers=triggers), direction, triggers


def output_ref(run):
    output = run['outputs'][0]
    return {key: output[key] for key in ('artifact_id', 'artifact_version', 'content_hash')}


def test_create_persists_typed_brief_and_real_trigger(tmp_path):
    works, work, _ = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    commission = store.create(payload(work))
    assert commission['revision'] == 1
    assert commission['target_ability'] == 'series'
    assert commission['brief']['constraints'] == {'rating': 'PG'}
    assert commission['cadence']['spec'] == {'kind': 'interval', 'interval_secs': 900, 'timezone': 'UTC'}
    assert commission['schedule_error'] == ''
    loaded = triggers.get(TRIGGER_PREFIX + commission['id']).trigger
    action = loaded.workflow['inline']
    assert loaded.kind == 'clock'
    assert loaded.overlap == 'skip'
    assert loaded.capabilities == {'providers': ['creative-commission']}
    assert action['provider'] == 'creative-commission'
    assert action['config']['commission_id'] == commission['id']
    assert action['config']['schedule_revision'] == 1
    assert action['config']['cadence_hash'] == digest(commission['cadence']['spec'])
    assert works.get(work['id'])['text'] == TEXT


def test_create_is_idempotent_and_request_conflicts_are_explicit(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    first = store.create(payload(work, request='same'))
    assert store.create(payload(work, request='same')) == first
    changed = payload(work, request='same')
    changed['name'] = 'Different'
    with pytest.raises(CatalogError) as conflict:
        store.create(changed)
    assert conflict.value.status == 409
    assert len(store.list()['items']) == 1


@pytest.mark.parametrize('ability', ['video', 'image', 'music', 'music-video', 'series'])
def test_all_declared_abilities_are_typed(ability, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    body = payload(work, request='ability-' + ability)
    body['target_ability'] = ability
    assert store.create(body)['target_ability'] == ability


@pytest.mark.parametrize('cadence,kind', [
    ({'kind': 'daily', 'at': '09:30', 'timezone': 'UTC', 'weekdays_only': True}, 'cron'),
    ({'kind': 'weekly', 'at': '18:05', 'weekday': 4, 'timezone': 'UTC'}, 'cron'),
    ({'kind': 'custom', 'cron': '15 7 * * 1', 'timezone': 'UTC'}, 'cron'),
    ({'kind': 'interval', 'seconds': 1800}, 'interval'),
])
def test_supported_cadences_map_to_shared_clock_specs(cadence, kind, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    commission = store.create(payload(work, request='cadence-' + cadence['kind'], cadence=cadence))
    trigger = triggers.get(TRIGGER_PREFIX + commission['id']).trigger
    assert trigger.spec['kind'] == kind
    assert trigger.next_fire_at


@pytest.mark.parametrize('cadence', [
    {'kind': 'interval', 'seconds': 60},
    {'kind': 'custom', 'cron': 'not cron'},
    {'kind': 'daily', 'at': '25:00'},
    {'kind': 'weekly', 'at': '10:00', 'weekday': 8},
    {'kind': 'daily', 'at': '10:00', 'timezone': 'Mars/Olympus'},
])
def test_invalid_or_unbounded_cadences_are_rejected(cadence, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    with pytest.raises(CatalogError):
        store.create(payload(work, cadence=cadence))
    assert store.list() == {'items': []}


async def test_real_scheduler_due_fire_executes_exactly_one_linked_project(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, triggers = make_store(tmp_path)
    commission = store.create(payload(work))
    trigger = triggers.get(TRIGGER_PREFIX + commission['id']).trigger
    trigger.next_fire_at = to_iso(1000)
    triggers.upsert(trigger)
    due = await tick(triggers, now=1001, persist=True, base_dir=tmp_path)
    assert len(due.fires) == 1
    fire = due.fires[0].to_dict()
    provider = CommissionActionProvider(store)
    config = trigger.workflow['inline']['config']
    result = await provider.execute(config, ActionContext(event='trigger.fired', payload=fire))
    assert result.success
    run = json.loads(result.stdout)
    assert run['status'] == 'completed'
    assert run['trigger'] == 'schedule'
    assert run['project_id']
    assert len(run['outputs']) == 1
    assert len(run['attempts']) == 1
    project = direction.get(run['project_id'])
    assert project['status'] == 'completed'
    assert [step['status'] for step in project['steps']] == ['done', 'done']
    repeated = await provider.execute(config, ActionContext(event='trigger.fired', payload=fire))
    assert json.loads(repeated.stdout) == run
    assert len(direction.list()) == 1
    assert len(store.runs(commission['id'])['items']) == 1


async def test_disable_before_stale_tick_prevents_dispatch_and_removes_trigger(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, triggers = make_store(tmp_path)
    commission = store.create(payload(work))
    config = triggers.get(TRIGGER_PREFIX + commission['id']).trigger.workflow['inline']['config']
    disabled = store.update(commission['id'], {'revision': 1, 'enabled': False})
    assert disabled['enabled'] is False
    assert triggers.get(TRIGGER_PREFIX + commission['id']) is None
    result = await CommissionActionProvider(store).execute(config,
        ActionContext(event='trigger.fired', payload={'scheduled_for': 1200}))
    assert result.success
    assert json.loads(result.stdout)['reason'] == 'disabled'
    assert direction.list() == []
    assert store.runs(commission['id']) == {'items': []}


async def test_changed_cadence_rejects_stale_armed_action(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, triggers = make_store(tmp_path)
    commission = store.create(payload(work))
    old = triggers.get(TRIGGER_PREFIX + commission['id']).trigger.workflow['inline']['config']
    changed = store.update(commission['id'], {'revision': 1, 'cadence': {'kind': 'interval', 'seconds': 1800}})
    assert changed['schedule_revision'] == 2
    result = await CommissionActionProvider(store).execute(old,
        ActionContext(event='trigger.fired', payload={'scheduled_for': 1200}))
    assert result.success
    assert json.loads(result.stdout)['reason'] == 'stale_schedule'
    assert direction.list() == []


async def test_failure_is_recorded_and_real_retry_reuses_occurrence(tmp_path):
    works, work, draft = source(tmp_path)
    store, direction, _ = make_store(tmp_path)
    commission = store.create(payload(work, attempts=2))
    artifact = works.artifacts.get(draft['artifact_id'], version=draft['artifact_version'])
    assert artifact is not None
    assert works.artifacts.delete(draft['artifact_id']) is True
    failed = await store.execute(commission['id'], 'manual:retry', trigger='manual')
    assert failed['status'] == 'failed'
    assert failed['project_id'] is None
    assert failed['attempts'] == [{'number': 1, 'status': 'failed', 'started_at': failed['attempts'][0]['started_at'],
                                    'finished_at': failed['attempts'][0]['finished_at'], 'error': 'execution-failed:CatalogError'}]
    assert TEXT not in failed['attempts'][0]['error']
    restored = works.artifacts.create(name='Commission source draft', slug=draft['artifact_id'], kind='markdown',
        content=TEXT, description='restored exact source', readonly=True)
    assert restored.slug == draft['artifact_id']
    recovered = await store.retry(commission['id'], failed['id'])
    assert recovered['status'] == 'completed'
    assert recovered['id'] == failed['id']
    assert len(recovered['attempts']) == 2
    assert recovered['attempts'][1]['status'] == 'completed'
    assert len(direction.list()) == 1


async def test_retry_budget_exhaustion_preserves_each_failed_attempt(tmp_path):
    works, work, draft = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    commission = store.create(payload(work, attempts=2))
    works.artifacts.delete(draft['artifact_id'])
    first = await store.execute(commission['id'], 'manual:exhaust', trigger='manual')
    second = await store.retry(commission['id'], first['id'])
    assert second['status'] == 'exhausted'
    assert [row['number'] for row in second['attempts']] == [1, 2]
    assert all(row['error'] == 'execution-failed:CatalogError' for row in second['attempts'])
    with pytest.raises(CatalogError, match='retryable'):
        await store.retry(commission['id'], second['id'])


async def test_restart_preserves_schedule_run_history_output_and_project(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, triggers = make_store(tmp_path)
    commission = store.create(payload(work))
    run = await store.execute(commission['id'], 'manual:restart', trigger='manual')
    reopened = CommissionStore(tmp_path, direction=DirectionStore(tmp_path), triggers=TriggerStore(base_dir=tmp_path))
    assert reopened.get(commission['id']) == commission
    persisted = reopened.runs(commission['id'])['items'][0]
    assert persisted == run
    assert persisted['outputs'][0]['content_hash'] == hashlib.sha256(
        direction.artifacts.get(persisted['outputs'][0]['artifact_id'], version=1).content.encode()).hexdigest()
    assert reopened.triggers.get(TRIGGER_PREFIX + commission['id']) is not None


async def test_feedback_is_output_linked_author_isolated_revisioned_and_tombstoned(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    run = await store.execute(commission['id'], 'manual:feedback', trigger='manual')
    output = output_ref(run)
    one = store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
        'rating': 'liked', 'note': 'Keep it', 'tags': ['quiet']})
    two = store.react(commission['id'], {'run_id': run['id'], 'author': 'bob', 'output': output,
        'rating': 'disliked', 'note': 'More motion', 'tags': ['pace']})
    assert one['id'] != two['id']
    updated = store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
        'rating': 'disliked', 'note': 'Changed view', 'tags': ['revision'], 'revision': one['revision']})
    assert updated['id'] == one['id']
    assert updated['revision'] == 2
    assert next(row for row in store.feedback(commission['id'])['items'] if row['id'] == two['id'])['rating'] == 'disliked'
    removed = store.remove_reaction(commission['id'], updated['id'], {'revision': 2, 'author': 'alice'})
    assert removed['deleted'] is True
    assert removed['revision'] == 3
    assert next(row for row in store.feedback(commission['id'])['items'] if row['id'] == two['id'])['deleted'] is False


async def test_next_run_consumes_only_live_bounded_feedback(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    first = await store.execute(commission['id'], 'manual:first', trigger='manual')
    reaction = store.react(commission['id'], {'run_id': first['id'], 'author': 'alice', 'output': output_ref(first),
        'rating': 'liked', 'note': 'Use the quiet rhythm.', 'tags': ['rhythm']})
    second = await store.execute(commission['id'], 'manual:second', trigger='manual')
    assert second['feedback_refs'] == [{'id': reaction['id'], 'revision': 1}]
    treatment = json.loads(direction.get(second['project_id'])['treatment'])
    assert treatment['feedback'][0]['note'] == 'Use the quiet rhythm.'
    assert treatment['feedback'][0]['output'] == output_ref(first)
    store.remove_reaction(commission['id'], reaction['id'], {'revision': 1, 'author': 'alice'})
    third = await store.execute(commission['id'], 'manual:third', trigger='manual')
    assert third['feedback_refs'] == []
    assert json.loads(direction.get(third['project_id'])['treatment'])['feedback'] == []


async def test_feedback_rejects_foreign_output_author_and_stale_revision(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    run = await store.execute(commission['id'], 'manual:guards', trigger='manual')
    output = output_ref(run)
    foreign = {**output, 'content_hash': '0' * 64}
    with pytest.raises(CatalogError, match='not linked'):
        store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': foreign,
            'rating': 'liked', 'note': '', 'tags': []})
    row = store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
        'rating': 'liked', 'note': '', 'tags': []})
    with pytest.raises(CatalogError) as wrong_author:
        store.remove_reaction(commission['id'], row['id'], {'revision': 1, 'author': 'bob'})
    assert wrong_author.value.status == 403
    with pytest.raises(CatalogError) as stale:
        store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
            'rating': 'disliked', 'note': '', 'tags': [], 'revision': 9})
    assert stale.value.status == 409


async def test_http_create_manual_run_feedback_disable_and_restart(tmp_path):
    _, work, _ = source(tmp_path)
    direction = DirectionStore(tmp_path); triggers = TriggerStore(base_dir=tmp_path)
    app = web.Application(); register(app, tmp_path, direction=direction, triggers=triggers)
    async with TestClient(TestServer(app)) as client:
        response = await client.post('/api/capabilities/creative/commissions', json=payload(work, request='http'))
        assert response.status == 200
        commission = await response.json()
        response = await client.post(f'/api/capabilities/creative/commissions/{commission["id"]}/run', json={'request_id': 'http-run'})
        assert response.status == 200
        run = await response.json()
        assert run['status'] == 'completed'
        response = await client.post(f'/api/capabilities/creative/commissions/{commission["id"]}/feedback', json={
            'run_id': run['id'], 'author': 'http-owner', 'output': output_ref(run), 'rating': 'liked', 'note': 'Keep', 'tags': []})
        assert response.status == 200
        reaction = await response.json()
        detail = await (await client.get(f'/api/capabilities/creative/commissions/{commission["id"]}')).json()
        assert detail['runs'][0]['project_id'] == run['project_id']
        assert detail['feedback'][0]['id'] == reaction['id']
        response = await client.patch(f'/api/capabilities/creative/commissions/{commission["id"]}', json={'revision': 1, 'enabled': False})
        assert response.status == 200
        assert (await response.json())['enabled'] is False


async def test_native_tools_use_same_store_and_closed_top_level_schemas(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    provider = CommissionTools(store)
    created = await provider.invoke('creative_commission_create', {'payload': payload(work, request='native')})
    assert created.success
    commission = json.loads(created.output)
    run_result = await provider.invoke('creative_commission_run', {'id': commission['id'], 'request_id': 'native-run'})
    assert run_result.success
    run = json.loads(run_result.output)
    reaction = await provider.invoke('creative_commission_feedback', {'id': commission['id'], 'payload': {
        'run_id': run['id'], 'author': 'native-owner', 'output': output_ref(run), 'rating': 'liked', 'note': '', 'tags': []}})
    assert reaction.success
    listed = await provider.invoke('creative_commission_get', {'id': commission['id']})
    detail = json.loads(listed.output)
    assert detail['runs'][0]['outputs'][0]['artifact_id'] == run['outputs'][0]['artifact_id']
    assert detail['feedback'][0]['author'] == 'native-owner'
    for schema in SCHEMAS.values():
        assert schema['additionalProperties'] is False
        for forbidden in ('provider', 'model', 'credentials', 'home', 'runtime'):
            assert forbidden not in schema['properties']


async def test_action_provider_rejects_missing_occurrence_and_unknown_commission(tmp_path):
    store, _, _ = make_store(tmp_path)
    provider = CommissionActionProvider(store)
    missing = await provider.execute({'commission_id': 'missing', 'schedule_revision': 1, 'cadence_hash': '0' * 64},
        ActionContext(event='trigger.fired', payload={'scheduled_for': 1000}))
    assert not missing.success
    assert 'not found' in missing.error
    absent_occurrence = await provider.execute({'commission_id': 'missing', 'schedule_revision': 1, 'cadence_hash': '0' * 64},
        ActionContext(event='trigger.fired', payload={}))
    assert not absent_occurrence.success


@pytest.mark.parametrize(('field', 'value'), [
    ('target_ability', 'universe'),
    ('enabled', 'yes'),
    ('max_attempts', 0),
    ('max_attempts', 4),
])
def test_create_rejects_invalid_ability_flags_and_retry_bounds(field, value, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    body = payload(work)
    body[field] = value
    with pytest.raises(CatalogError):
        store.create(body)
    assert store.list()['items'] == []


@pytest.mark.parametrize('brief', [
    {'intent': '', 'genre': '', 'category': '', 'style': '', 'constraints': {}, 'seed_refs': []},
    {'intent': 'x', 'genre': '', 'category': '', 'style': '', 'constraints': [], 'seed_refs': []},
    {'intent': 'x', 'genre': '', 'category': '', 'style': '', 'constraints': {}, 'seed_refs': ['bad id!']},
    {'intent': 'x', 'genre': '', 'category': '', 'style': '', 'constraints': {}, 'seed_refs': list(map(str, range(51)))},
])
def test_brief_validation_is_typed_and_bounded(brief, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    body = payload(work)
    body['brief'] = brief
    with pytest.raises(CatalogError):
        store.create(body)


@pytest.mark.parametrize('steps', [
    [],
    [{'id': 'one', 'title': 'Unknown', 'operation': 'generate.anything', 'depends_on': []}],
    [{'id': 'one', 'title': 'One', 'operation': 'source.verify', 'depends_on': ['later']}],
    [
        {'id': 'same', 'title': 'One', 'operation': 'source.verify', 'depends_on': []},
        {'id': 'same', 'title': 'Two', 'operation': 'treatment.snapshot', 'depends_on': []},
    ],
])
def test_plan_validation_prevents_unbounded_or_unknown_execution(steps, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    body = payload(work)
    body['steps'] = steps
    with pytest.raises(CatalogError):
        store.create(body)


@pytest.mark.parametrize('sources', [
    [],
    [{'kind': 'artifact', 'id': 'source', 'revision': 1}],
    [{'kind': 'work', 'id': 'bad id!', 'revision': 1}],
    [{'kind': 'work', 'id': 'missing', 'revision': 0}],
])
def test_source_contract_rejects_noncanonical_references(sources, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    body = payload(work)
    body['sources'] = sources
    with pytest.raises(CatalogError):
        store.create(body)


def test_create_resolves_source_existence_before_arming_schedule(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, triggers = make_store(tmp_path)
    body = payload(work)
    body['sources'] = [{'kind': 'work', 'id': 'missing-work', 'revision': 1}]
    with pytest.raises(CatalogError) as missing:
        store.create(body)
    assert missing.value.status == 404
    assert store.list() == {'items': []}
    assert direction.list() == []
    assert triggers.list_triggers() == []


def test_update_is_optimistic_and_brief_edit_does_not_change_schedule_identity(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    commission = store.create(payload(work))
    before = triggers.get(TRIGGER_PREFIX + commission['id']).trigger.workflow['inline']['config']
    brief = dict(commission['brief'])
    brief['intent'] = 'A revised but still bounded intent.'
    updated = store.update(commission['id'], {'revision': 1, 'brief': brief})
    after = triggers.get(TRIGGER_PREFIX + commission['id']).trigger.workflow['inline']['config']
    assert updated['revision'] == 2
    assert updated['schedule_revision'] == 1
    assert after == before
    with pytest.raises(CatalogError) as stale:
        store.update(commission['id'], {'revision': 1, 'enabled': False})
    assert stale.value.status == 409


def test_trigger_write_failure_is_persisted_without_claiming_schedule_success(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    commission = store.create(payload(work))
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('occupied')
    store.triggers = TriggerStore(base_dir=blocked)
    updated = store.update(commission['id'], {'revision': 1, 'cadence': {'kind': 'interval', 'seconds': 1800}})
    assert updated['schedule_error'].startswith('schedule-write:')
    assert triggers.get(TRIGGER_PREFIX + commission['id']) is not None
    reopened = CommissionStore(tmp_path, direction=DirectionStore(tmp_path), triggers=triggers)
    assert reopened.get(commission['id'])['schedule_error'] == updated['schedule_error']


async def test_manual_occurrence_idempotency_prevents_duplicate_attempt_and_project(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    first = await store.execute(commission['id'], 'manual:same', trigger='manual')
    second = await store.execute(commission['id'], 'manual:same', trigger='manual')
    assert second == first
    assert len(second['attempts']) == 1
    assert len(direction.list()) == 1
    assert store.runs(commission['id'])['items'] == [first]


async def test_output_reference_resolves_exact_readonly_bytes(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    run = await store.execute(commission['id'], 'manual:output', trigger='manual')
    output = run['outputs'][0]
    artifact = direction.artifacts.get(output['artifact_id'], version=output['artifact_version'])
    assert artifact is not None
    assert artifact.readonly
    assert hashlib.sha256(artifact.content.encode()).hexdigest() == output['content_hash']
    assert output['path'] == f'/api/artifacts/{output["artifact_id"]}?version=1'


async def test_feedback_identity_is_deterministic_for_same_author_output(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    run = await store.execute(commission['id'], 'manual:identity', trigger='manual')
    output = output_ref(run)
    first = store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
        'rating': 'liked', 'note': 'First', 'tags': []})
    second = store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
        'rating': 'disliked', 'note': 'Second', 'tags': [], 'revision': 1})
    assert second['id'] == first['id']
    assert second['created_at'] == first['created_at']
    assert second['revision'] == 2
    assert len(store.feedback(commission['id'])['items']) == 1


async def test_feedback_rejects_unknown_run_invalid_rating_and_excess_tags(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    run = await store.execute(commission['id'], 'manual:feedback-errors', trigger='manual')
    output = output_ref(run)
    with pytest.raises(CatalogError) as missing:
        store.react(commission['id'], {'run_id': 'missing', 'author': 'alice', 'output': output,
            'rating': 'liked', 'note': '', 'tags': []})
    assert missing.value.status == 404
    with pytest.raises(CatalogError, match='rating'):
        store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
            'rating': 'five-stars', 'note': '', 'tags': []})
    with pytest.raises(CatalogError, match='tags'):
        store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
            'rating': 'liked', 'note': '', 'tags': [str(index) for index in range(21)]})


async def test_removed_reaction_can_be_reactivated_with_lineage(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    commission = store.create(payload(work))
    run = await store.execute(commission['id'], 'manual:reactivate', trigger='manual')
    output = output_ref(run)
    first = store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
        'rating': 'liked', 'note': '', 'tags': []})
    removed = store.remove_reaction(commission['id'], first['id'], {'revision': 1, 'author': 'alice'})
    active = store.react(commission['id'], {'run_id': run['id'], 'author': 'alice', 'output': output,
        'rating': 'disliked', 'note': 'Restored', 'tags': [], 'revision': removed['revision']})
    assert active['id'] == first['id']
    assert active['revision'] == 3
    assert active['deleted'] is False
    assert active['deleted_at'] is None


async def test_native_unknown_tools_and_extra_top_level_arguments_fail_closed(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    provider = CommissionTools(store)
    unknown = await provider.invoke('creative_commission_missing', {})
    assert not unknown.success
    assert unknown.metadata['status'] == 404
    extra = await provider.invoke('creative_commission_list', {'provider': 'override'})
    assert not extra.success
    assert extra.metadata['status'] == 400
    missing = await provider.invoke('creative_commission_get', {})
    assert not missing.success
    assert missing.metadata['status'] == 400
    assert store.list()['items'] == []


async def test_http_rejects_query_fields_and_foreign_feedback_output(tmp_path):
    _, work, _ = source(tmp_path)
    app = web.Application()
    register(app, tmp_path, direction=DirectionStore(tmp_path), triggers=TriggerStore(base_dir=tmp_path))
    async with TestClient(TestServer(app)) as client:
        assert (await client.get('/api/capabilities/creative/commissions?home=/tmp/other')).status == 400
        commission = await (await client.post('/api/capabilities/creative/commissions', json=payload(work, request='http-guards'))).json()
        run = await (await client.post(f'/api/capabilities/creative/commissions/{commission["id"]}/run',
            json={'request_id': 'guard-run'})).json()
        foreign = {**output_ref(run), 'artifact_id': 'foreign-artifact'}
        response = await client.post(f'/api/capabilities/creative/commissions/{commission["id"]}/feedback', json={
            'run_id': run['id'], 'author': 'owner', 'output': foreign, 'rating': 'liked', 'note': '', 'tags': []})
        assert response.status == 409
        assert (await response.json())['code'] == 'creative_commission_invalid'


def recurrence(rule='FREQ=DAILY;COUNT=4', start='2027-03-13T09:00:00', exdates=None):
    return {'kind': 'recurrence', 'dtstart': start, 'rrule': rule,
            'timezone': 'America/New_York', 'exdates': exdates or []}


def test_recurrence_create_arms_one_shared_at_trigger_and_persists_next_fire(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    commission = store.create(payload(work, cadence=recurrence()))
    trigger = triggers.get(TRIGGER_PREFIX + commission['id']).trigger
    assert commission['cadence']['spec'] == {'kind': 'recurrence'}
    assert commission['schedule_state'] == 'active'
    assert commission['next_fire_at'] == trigger.next_fire_at
    assert trigger.spec['kind'] == 'at'
    assert trigger.spec['strict'] is True
    assert trigger.spec['timezone'] == 'America/New_York'
    assert trigger.workflow['inline']['config']['cadence_hash'] == digest({
        key: value for key, value in commission['cadence'].items() if key != 'spec'})


def test_daily_recurrence_preserves_local_wall_time_across_dst():
    cadence = recurrence(start='2026-03-07T09:00:00')
    before = datetime(2026, 3, 6, 12, tzinfo=timezone.utc).timestamp()
    first = _recurrence_next({**cadence, 'spec': {'kind': 'recurrence'}}, before)
    second = _recurrence_next({**cadence, 'spec': {'kind': 'recurrence'}}, first)
    third = _recurrence_next({**cadence, 'spec': {'kind': 'recurrence'}}, second)
    assert datetime.fromtimestamp(first, timezone.utc).isoformat() == '2026-03-07T14:00:00+00:00'
    assert datetime.fromtimestamp(second, timezone.utc).isoformat() == '2026-03-08T13:00:00+00:00'
    assert datetime.fromtimestamp(third, timezone.utc).isoformat() == '2026-03-09T13:00:00+00:00'


def test_monthly_last_weekday_and_exclusions_use_dateutil_calendar_semantics():
    cadence = recurrence(rule='FREQ=MONTHLY;COUNT=4;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1',
        start='2026-01-30T09:00:00', exdates=['2026-02-27'])
    normalized = {**cadence, 'spec': {'kind': 'recurrence'}}
    before = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    first = _recurrence_next(normalized, before)
    second = _recurrence_next(normalized, first)
    third = _recurrence_next(normalized, second)
    assert datetime.fromtimestamp(first, timezone.utc).isoformat() == '2026-01-30T14:00:00+00:00'
    assert datetime.fromtimestamp(second, timezone.utc).isoformat() == '2026-03-31T13:00:00+00:00'
    assert datetime.fromtimestamp(third, timezone.utc).isoformat() == '2026-04-30T13:00:00+00:00'


@pytest.mark.parametrize('cadence', [
    {'kind': 'recurrence', 'dtstart': 'bad', 'rrule': 'FREQ=DAILY', 'timezone': 'UTC', 'exdates': []},
    {'kind': 'recurrence', 'dtstart': '2027-01-01T09:00:00', 'rrule': 'FREQ=HOURLY', 'timezone': 'UTC', 'exdates': []},
    {'kind': 'recurrence', 'dtstart': '2027-01-01T09:00:00', 'rrule': 'FREQ=DAILY;COUNT=0', 'timezone': 'UTC', 'exdates': []},
    {'kind': 'recurrence', 'dtstart': '2027-01-01T09:00:00', 'rrule': 'FREQ=DAILY;BYEASTER=1', 'timezone': 'UTC', 'exdates': []},
    {'kind': 'recurrence', 'dtstart': '2027-01-01T09:00:00', 'rrule': 'FREQ=DAILY', 'timezone': 'Mars/Olympus', 'exdates': []},
    {'kind': 'recurrence', 'dtstart': '2027-01-01T09:00:00', 'rrule': 'FREQ=DAILY', 'timezone': 'UTC', 'exdates': ['x'] * 101},
])
def test_invalid_recurrence_rules_fail_before_record_or_trigger(cadence, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    with pytest.raises(CatalogError):
        store.create(payload(work, cadence=cadence))
    assert store.list() == {'items': []}
    assert triggers.list_triggers() == []


async def test_real_recurrence_due_fire_rearms_next_occurrence_after_dispatch(tmp_path):
    _, work, _ = source(tmp_path)
    store, direction, triggers = make_store(tmp_path)
    commission = store.create(payload(work, cadence=recurrence(rule='FREQ=DAILY;COUNT=4')))
    record = store.get(commission['id'])
    before = datetime(2027, 3, 12, 12, tzinfo=timezone.utc).timestamp()
    trigger = store._trigger(record, at=before)
    first = datetime.fromisoformat(trigger.next_fire_at).timestamp()
    triggers.upsert(trigger)
    due = await tick(triggers, now=first + 1, persist=True, base_dir=tmp_path)
    assert len(due.fires) == 1
    result = await CommissionActionProvider(store).execute(trigger.workflow['inline']['config'],
        ActionContext(event='trigger.fired', payload=due.fires[0].to_dict()))
    assert result.success
    run = json.loads(result.stdout)
    assert run['status'] == 'completed'
    assert len(direction.list()) == 1
    rearmed = triggers.get(TRIGGER_PREFIX + commission['id']).trigger
    assert rearmed.enabled is True
    assert rearmed.spec['kind'] == 'at'
    assert datetime.fromisoformat(rearmed.next_fire_at).timestamp() > first
    state = store.get(commission['id'])
    assert state['schedule_state'] == 'active'
    assert state['next_fire_at'] == rearmed.next_fire_at


async def test_failed_recurrence_fire_records_attempt_rearms_and_can_retry(tmp_path):
    works, work, draft = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    commission = store.create(payload(work, cadence=recurrence(), attempts=2))
    record = store.get(commission['id'])
    before = datetime(2027, 3, 12, 12, tzinfo=timezone.utc).timestamp()
    trigger = store._trigger(record, at=before)
    occurrence = datetime.fromisoformat(trigger.next_fire_at).timestamp()
    works.artifacts.delete(draft['artifact_id'])
    result = await CommissionActionProvider(store).execute(trigger.workflow['inline']['config'],
        ActionContext(event='trigger.fired', payload={'scheduled_for': occurrence}))
    assert not result.success
    failed = json.loads(result.stdout)
    assert failed['status'] == 'failed'
    assert len(failed['attempts']) == 1
    assert triggers.get(TRIGGER_PREFIX + commission['id']).trigger.enabled is True
    works.artifacts.create(name='Restored source', slug=draft['artifact_id'], kind='markdown', content=TEXT,
                           description='restored', readonly=True)
    retried = await store.retry(commission['id'], failed['id'])
    assert retried['status'] == 'completed'
    assert [attempt['status'] for attempt in retried['attempts']] == ['failed', 'completed']


def test_exhausted_finite_recurrence_disarms_truthfully_after_restart(tmp_path):
    _, work, _ = source(tmp_path)
    store, _, triggers = make_store(tmp_path)
    old = recurrence(rule='FREQ=DAILY;COUNT=2', start='2020-01-01T09:00:00')
    commission = store.create(payload(work, cadence=old))
    assert commission['schedule_state'] == 'exhausted'
    assert commission['next_fire_at'] == ''
    assert triggers.get(TRIGGER_PREFIX + commission['id']) is None
    reopened = CommissionStore(tmp_path, direction=DirectionStore(tmp_path), triggers=TriggerStore(base_dir=tmp_path))
    assert reopened.get(commission['id'])['schedule_state'] == 'exhausted'


@pytest.mark.parametrize(('ability', 'dispatch'), [
    ('image', {'input': {'prompt': 'A brass key on a quiet platform.', 'size': '', 'controls': {}, 'loras': []}}),
    ('video', {'input': {'prompt': 'A quiet station at dawn.', 'duration_seconds': 5, 'aspect_ratio': '', 'controls': {}}}),
])
async def test_unconfigured_media_dispatch_is_explicit_and_queues_no_job(ability, dispatch, tmp_path):
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    from gideon.workspace.capabilities.media.jobs import MediaJobs
    from gideon.workspace.capabilities.media.sketches import SketchStore
    _, work, _ = source(tmp_path)
    sketches = SketchStore(tmp_path / 'capabilities/media/sketches.sqlite3', NativeArtifactProvider(tmp_path / 'artifacts'))
    jobs = MediaJobs(tmp_path / 'capabilities/media/jobs.sqlite3', sketches)
    dispatcher = CommissionDispatcher(tmp_path, media_jobs=jobs)
    store, _, _ = make_store(tmp_path)
    store.dispatcher = dispatcher
    body = payload(work, request='dispatch-' + ability)
    body.update(target_ability=ability, dispatch=dispatch)
    commission = store.create(body)
    run = await store.execute(commission['id'], 'manual:unconfigured', trigger='manual')
    assert run['status'] == 'failed'
    assert run['attempts'][0]['error'] == ability + '_provider_unavailable'
    assert run['dispatch_receipts'] == [{
        **run['dispatch_receipts'][0], 'backend': 'media_jobs', 'operation': ability + '_generate',
        'resource_id': '', 'status': 'external_unavailable', 'upstream_status': '',
        'error_code': ability + '_provider_unavailable', 'artifact_refs': [],
    }]
    assert jobs.list() == {'items': []}


async def test_unconfigured_music_dispatch_persists_receipts_and_retry_identity(tmp_path):
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    from gideon.workspace.capabilities.music.catalog import MusicCatalog
    from gideon.workspace.capabilities.music.generation import MusicGeneration
    _, work, _ = source(tmp_path)
    catalog = MusicCatalog(tmp_path / 'capabilities/music', NativeArtifactProvider(root=tmp_path / 'artifacts'))
    track = catalog.create('tracks', {'title': 'Commission score'})
    music = MusicGeneration(tmp_path, catalog)
    store, _, _ = make_store(tmp_path)
    store.dispatcher = CommissionDispatcher(tmp_path, music_generation=music)
    body = payload(work, request='dispatch-music')
    body.update(target_ability='music', dispatch={'track_id': track['id'], 'track_revision': track['revision'],
        'prompt': 'A restrained mystery score.', 'music_length_ms': 3000, 'force_instrumental': True,
        'license': 'Use is subject to the configured provider terms.'})
    commission = store.create(body)
    first = await store.execute(commission['id'], 'manual:music', trigger='manual')
    assert first['status'] == 'failed'
    assert first['dispatch_receipts'][0]['status'] == 'external_unavailable'
    assert first['dispatch_receipts'][0]['error_code'] == 'engine_unavailable'
    assert music.list() == []
    second = await store.retry(commission['id'], first['id'])
    assert second['status'] == 'exhausted'
    assert [row['attempt'] for row in second['dispatch_receipts']] == [1, 2]
    assert len({row['request_id'] for row in second['dispatch_receipts']}) == 2
    assert music.list() == []


def _wav_bytes():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
        audio.writeframes(b'\x00\x00' * 16000)
    return output.getvalue()


async def test_music_video_dispatch_uses_real_pinned_ffmpeg_project(tmp_path):
    from PIL import Image
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    from gideon.workspace.capabilities.music.catalog import MusicCatalog
    from gideon.workspace.capabilities.music.video import VideoStore
    _, work, _ = source(tmp_path)
    catalog = MusicCatalog(tmp_path / 'capabilities/music', NativeArtifactProvider(root=tmp_path / 'artifacts'))
    track = catalog.create('tracks', {'title': 'Pinned score'})
    audio = catalog.artifacts.create_binary(name='Pinned score', data=_wav_bytes(), mime='audio/wav', kind='audio', source='manual')
    track = catalog.attach(track['id'], {'revision': track['revision'], 'artifact_ref': {'slug': audio.slug, 'version': audio.version},
        'source': {'kind': 'imported', 'label': 'Commission fixture recording', 'license': 'Test-owned recording'}})
    render = track['renders'][0]
    picture = io.BytesIO(); Image.new('RGB', (64, 64), 'navy').save(picture, format='PNG')
    image = catalog.artifacts.create_binary(name='Pinned scene', data=picture.getvalue(), mime='image/png', kind='image', source='manual')
    video = VideoStore(tmp_path / 'capabilities/music', catalog)
    project = video.create({'title': 'Commission music video', 'track_id': track['id'], 'render_id': render['id'],
        'tempo_bpm': 60, 'offset_seconds': 0, 'scenes': [{'id': 'scene', 'image_ref': {'slug': image.slug, 'version': image.version}, 'beats': 1}]})
    store, _, _ = make_store(tmp_path)
    store.dispatcher = CommissionDispatcher(tmp_path, music_video=video)
    body = payload(work, request='dispatch-music-video')
    body.update(target_ability='music-video', dispatch={'project_id': project['id'], 'revision': project['revision']})
    commission = store.create(body)
    run = await store.execute(commission['id'], 'manual:music-video', trigger='manual')
    assert run['status'] == 'submitted'
    receipt = run['dispatch_receipts'][0]
    assert receipt['backend'] == 'music_video'
    assert receipt['resource_id'] == receipt['request_id']
    task = video.tasks[receipt['resource_id']]
    await asyncio.wait_for(task, 30)
    finished = video.get_job(receipt['resource_id'])
    assert finished['status'] == 'completed'
    assert catalog.artifacts.get(finished['artifact_ref']['slug'], version=finished['artifact_ref']['version']) is not None


async def test_series_dispatch_enters_real_approval_gated_production(tmp_path):
    from gideon.workspace.capabilities.creative.production import SeriesProductionStore
    production = SeriesProductionStore(tmp_path)
    series = production.series.create({'request_id': 'commission-series', 'title': 'Commission series',
        'synopsis': 'A key reveals a route.', 'volumes': [{'id': 'volume', 'title': 'Volume', 'chapters': [
            {'id': 'chapter', 'title': 'Station', 'prompt': 'Open at the station.'}]}],
        'arcs': [{'id': 'arc', 'title': 'Key arc', 'summary': 'Follow the key.', 'chapter_ids': ['chapter']}]})
    work = production.series.prepare(series['id'], 'chapter', {'revision': series['revision']})
    await production.series.draft(series['id'], 'chapter', {'request_id': 'source-draft', 'revision': series['revision'],
        'work_revision': work['revision'], 'mode': 'authored', 'text': TEXT})
    direction = DirectionStore(tmp_path)
    store = CommissionStore(tmp_path, direction=direction, triggers=TriggerStore(base_dir=tmp_path),
                            dispatcher=CommissionDispatcher(tmp_path, production=production))
    body = payload(work, request='dispatch-series')
    body.update(sources=[{'kind': 'series', 'id': series['id'], 'revision': series['revision']}],
        dispatch={'series_id': series['id'], 'series_revision': series['revision'], 'mode': 'model', 'max_attempts': 2})
    commission = store.create(body)
    run = await store.execute(commission['id'], 'manual:series', trigger='manual')
    assert run['status'] == 'submitted'
    receipt = run['dispatch_receipts'][0]
    assert receipt['backend'] == 'series_production'
    actual = production.get(series['id'], receipt['resource_id'])
    assert actual['status'] == 'awaiting_approval'
    assert actual['approval']['kind'] == 'chapter_review'


@pytest.mark.parametrize(('ability', 'dispatch'), [
    ('image', {'input': {'prompt': 'x'}, 'provider': 'override'}),
    ('video', {'input': {'prompt': 'x'}, 'model': 'override'}),
    ('music', {'track_id': 'x', 'track_revision': 1, 'prompt': 'x', 'music_length_ms': 3000,
               'force_instrumental': True, 'license': 'x', 'credential_name': 'secret'}),
    ('music-video', {'project_id': 'x', 'revision': 1, 'home': '/tmp/other'}),
])
def test_dispatch_routing_overrides_fail_before_persistence(ability, dispatch, tmp_path):
    _, work, _ = source(tmp_path)
    store, _, _ = make_store(tmp_path)
    body = payload(work, request='override-' + ability)
    body.update(target_ability=ability, dispatch=dispatch)
    with pytest.raises(CatalogError, match='provider-neutral'):
        store.create(body)
    assert store.list() == {'items': []}
