import json
from datetime import datetime, timezone
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.capabilities.platform import insights
from gideon.workspace.capabilities.platform.tools import create_provider
from gideon.workspace.capabilities.identity.goal_plans import GoalPlanStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementStore, MeasurementError
from gideon.workspace.capabilities.wellbeing.labs import LabStore
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore
from gideon.interfaces.dashboard.handlers.capabilities_insights import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, use_ephemeral_secret, generate_token

PREFIX = '/api/capabilities/platform/insights'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    return tmp_path


def weight(home, value=80, day='2026-09-01', request='weight-a'):
    return MeasurementStore(home).create(dict(request_id=request, kind='body_weight', observed_at=day+'T12:00:00Z', unit='kg', values={'weight': value}, source='scale', notes='Measured'))


def goal(home):
    store = GoalPlanStore(home / 'capabilities/identity/goals.sqlite3')
    row = store.goals.save_goal(title='Practice', request_id='goal')
    store.configure(goal_id=row['id'], parent_id=None, horizon='short_term', milestones=[dict(id='m1', title='First session', done=True, target_date=None), dict(id='m2', title='Second session', done=False, target_date=None)], links=[], unit='sessions', target_value=10, expected_revision=0, request_id='plan')
    store.checkin(goal_id=row['id'], value=2, observed_at='2026-09-01T00:00:00Z', notes='Reported', request_id='first')
    store.checkin(goal_id=row['id'], value=4, observed_at='2026-09-03T00:00:00Z', notes='Reported', request_id='second')
    return row


def labs(home):
    store = LabStore(home)
    values = [dict(analyte='Vitamin D', observed_at='2026-09-01T00:00:00Z', value=20, unit='ng/mL'), dict(analyte='Vitamin D', observed_at='2026-09-03T00:00:00Z', value=25, unit='ng/mL'), dict(analyte='Vitamin D', observed_at='2026-09-04T00:00:00Z', value=60, unit='nmol/L')]
    payload = dict(filename='result.json', format='json', content=json.dumps(values), source='laboratory')
    preview = store.preview(payload)
    return store.commit({**payload, 'preview_id': preview['preview_id'], 'request_id': 'labs'})


def test_empty_sources_are_unknown_without_invented_health_score(home):
    result = insights.view()
    assert result['rows'] == []
    assert result['narratives'] == []
    assert result['coverage']['possibly_truncated'] is False
    assert 'missing records remain unknown' in result['limitation']
    assert 'overall health score' in result['limitation']
    assert len(result['fingerprint']) == 64
    assert insights.view()['fingerprint'] == result['fingerprint']
    assert not (home / 'capabilities/platform/insights.sqlite3').exists()


def test_goal_metrics_are_actual_human_observations_and_milestones(home):
    source = goal(home)
    result = insights.snapshot()['rows'][0]
    assert result['id'] == source['id']
    assert result['kind'] == 'goal'
    assert result['unit'] == 'sessions'
    assert result['target'] == 10
    assert result['milestones_complete_ratio'] == .5
    assert result['velocity']['value_per_day'] == 1
    assert [row['value'] for row in result['observations']] == [2, 4]
    assert all(row['source'] == 'human_reported' for row in result['observations'])
    assert result['plan_revision'] == 1
    assert result['sources'] == []


def test_measurement_correction_updates_projection_and_preserves_saved_source(home):
    first = weight(home)
    second = weight(home, 78, '2026-09-03', 'weight-b')
    initial = insights.snapshot()
    assert initial['rows'][0]['change'] == {'weight': -2}
    saved = insights.save({'fingerprint': initial['fingerprint'], 'note': 'My observation'})
    store = MeasurementStore(home)
    store.correct(second['id'], dict(request_id='correction', revision=1, values={'weight': 79}))
    current = insights.snapshot()
    assert current['fingerprint'] != initial['fingerprint']
    assert current['rows'][0]['change'] == {'weight': -1}
    assert current['rows'][0]['observations'][1]['revision'] == 2
    historical = insights.read_narrative(saved['slug'], version=1)
    assert historical['document']['snapshot'] == initial
    assert historical['document']['snapshot']['rows'][0]['observations'][0]['id'] == first['id']
    assert len(store.history(second['id'])) == 2
    with pytest.raises(MeasurementError, match='Source records changed') as error:
        insights.save({'fingerprint': initial['fingerprint'], 'note': 'stale'})
    assert error.value.status == 409


def test_lab_series_separate_units_and_link_immutable_original(home):
    receipt = labs(home)
    rows = insights.snapshot()['rows']
    assert len(rows) == 2
    by_unit = {row['unit']: row for row in rows}
    assert by_unit['ng/mL']['change'] == {'value': 5}
    assert by_unit['nmol/L']['change'] is None
    assert len(by_unit['ng/mL']['observations']) == 2
    assert by_unit['ng/mL']['observations'][0]['artifact'] == receipt['records'][0]['artifact']
    original = insights.artifacts(home).get(receipt['artifact']['slug'])
    assert original.readonly
    assert original.version == 1
    assert receipt['records'][0]['id'] == by_unit['ng/mL']['observations'][0]['id']


def test_intervention_missing_days_are_not_treated_as_skipped(home):
    store = InterventionStore(home)
    today = datetime.now(timezone.utc).date().isoformat()
    plan = store.create_plan(dict(request_id='plan', name='Walk', instructions='User plan', kind='activity', source='human', timezone='UTC', start_date=today, end_date=None, weekdays=list(range(7))))
    initial = insights.snapshot()['rows'][0]
    assert initial['summary']['unrecorded_days'] == 1
    assert initial['summary']['skipped_days'] == 0
    assert initial['summary']['completion_rate'] is None
    row = store.record(plan['id'], dict(request_id='record', date=today, status='completed', observed_at=datetime.now(timezone.utc).isoformat(), notes='Done'))
    result = insights.snapshot()['rows'][0]
    assert result['summary']['completed_days'] == 1
    assert result['summary']['unrecorded_days'] == 0
    assert result['observations'][0]['id'] == row['id']
    assert result['revision'] == plan['revision']


def test_artifact_versions_conflicts_and_foreign_artifact_protection(home):
    weight(home)
    data = insights.snapshot()
    saved = insights.save({'fingerprint': data['fingerprint'], 'note': 'First'})
    second = insights.save({'fingerprint': data['fingerprint'], 'note': 'Second', 'slug': saved['slug'], 'version': 1})
    assert insights.save({'fingerprint': data['fingerprint'], 'note': 'First'})['slug'] == saved['slug']
    assert second['version'] == 2
    assert second['versions'] == [1, 2]
    assert insights.read_narrative(saved['slug'], 1)['document']['note'] == 'First'
    assert insights.read_narrative(saved['slug'], 2)['document']['note'] == 'Second'
    assert second['document']['authored_by'] == 'user'
    with pytest.raises(MeasurementError, match='Narrative changed'):
        insights.save({'fingerprint': data['fingerprint'], 'note': 'Lost update', 'slug': saved['slug'], 'version': 1})
    unrelated = insights.artifacts(home).create(name='Other', content='{}', kind='json')
    with pytest.raises(MeasurementError, match='not found'):
        insights.save({'fingerprint': data['fingerprint'], 'note': 'Overwrite', 'slug': unrelated.slug, 'version': 1})
    assert insights.artifacts(home).get(unrelated.slug).content == '{}'
    assert len(insights.view()['narratives']) == 1
    assert insights.artifacts(home).list_versions(saved['slug']) == [1, 2]


@pytest.mark.parametrize('payload', [{'note': 22}, {'note': 'x' * 8001}, {'unrecognized': 1}, []])
def test_invalid_narratives_never_create_artifacts(home, payload):
    with pytest.raises(ValueError):
        insights.save(payload)
    assert insights.artifacts(home).list() == []


@pytest.mark.asyncio
async def test_real_authenticated_http_and_native_read_same_projection(home):
    weight(home)
    app = web.Application(middlewares=[token_auth_middleware(port=8000)])
    register(app)
    async with TestClient(TestServer(app)) as client:
        denied = await client.get(PREFIX)
        assert denied.status == 403
        headers = {'Cookie': 'gideon_token_8000=' + generate_token('dashboard:insights')}
        response = await client.get(PREFIX, headers=headers)
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        data = await response.json()
        tool = await create_provider().invoke('platform_personal_scorecard', {})
        assert tool.success
        assert json.loads(tool.output)['fingerprint'] == data['fingerprint']
        saved = await client.post(PREFIX, headers=headers, json={'fingerprint': data['fingerprint'], 'note': 'HTTP saved'})
        assert saved.status == 200
        document = await saved.json()
        historical = await client.get(PREFIX, headers=headers, params={'slug': document['slug'], 'version': 1})
        assert (await historical.json())['document']['note'] == 'HTTP saved'
        stale = await client.post(PREFIX, headers=headers, json={'fingerprint': 'old', 'note': 'invalid'})
        assert stale.status == 409
        missing = await client.get(PREFIX, headers=headers, params={'slug': 'missing'})
        assert missing.status == 404
        invalid = await client.get(PREFIX, headers=headers, params={'slug': document['slug'], 'version': 'bad'})
        assert invalid.status == 400
    invalid_tool = await create_provider().invoke('platform_personal_scorecard', {'private_traits': True})
    assert not invalid_tool.success
