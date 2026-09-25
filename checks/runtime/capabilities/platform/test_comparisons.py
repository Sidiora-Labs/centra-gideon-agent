import json
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.core.config.loader import config_dir
from gideon.assurance.evals.judge_bench import TableRow, write_bench_artifacts
from gideon.interfaces.dashboard.handlers.capabilities_comparisons import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.workspace.capabilities.platform.comparisons import view
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/comparisons'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()


def application():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    return app


async def authenticate(client):
    response = await client.get(PREFIX, params={'token': generate_token('comparison-owner')})
    assert response.status == 200
    return await response.json()


def imported(**changes):
    return {'model': 'documented-model-a', 'corpus': 'public-corpus-v1', 'metric': 'accuracy-percent', 'score': 75.5,
            'source': 'https://example.org/report', 'observed_at': '2026-09-01', 'methodology': 'User supplied source record; values are test data, not a model evaluation.', **changes}


def publish_table(identifier, agreement=None):
    row = TableRow(rubric_class='conversion', tier='small', samples=1, agreement=agreement, scored_cells=0, verifier_absent=2,
                   protocol_errors=1, separation=None, flip_rate=None, swapped_fixtures=0, false_passes=0, false_rejects=0,
                   forbidden_missed=0, cost_usd=None, wall_secs=0.125, calls=2, adequate=False)
    write_bench_artifacts(identifier, [], [row], [])


@pytest.mark.asyncio
async def test_two_attributed_models_persist_compare_and_delete_without_changing_eval_artifacts():
    publish_table('judge-source-record')
    table_path = config_dir() / 'evals/matrices/judge-source-record/table.json'
    original_table = table_path.read_bytes()
    async with TestClient(TestServer(application())) as client:
        empty = await authenticate(client)
        assert empty['imports'] == []
        assert empty['benchmark']['origin'] == 'recorded_judge_benchmark'
        first = await client.post(PREFIX, json=imported())
        assert first.status == 200
        first_data = await first.json()
        assert len(first_data['imports']) == 1
        one = first_data['imports'][0]
        assert one['origin'] == 'imported'
        assert one['verification'] == 'user_attributed'
        assert one['usage_basis'] == 'not_provided'
        assert len(one['id']) == 32
        second = await client.post(PREFIX, json=imported(model='documented-model-b', score=81))
        assert second.status == 200
        pair = (await second.json())['imports']
        assert {row['model'] for row in pair} == {'documented-model-a', 'documented-model-b'}
        assert all(row['corpus'] == 'public-corpus-v1' for row in pair)
        assert all(row['source'] == 'https://example.org/report' for row in pair)
        assert all(row['methodology'].startswith('User supplied') for row in pair)
        assert pair[0]['observed_at'] == '2026-09-01'
        assert pair[0]['score'] == 75.5
        assert pair[1]['score'] == 81
    async with TestClient(TestServer(application())) as restarted:
        persisted = await authenticate(restarted)
        assert persisted['imports'] == pair
        response = await restarted.delete(PREFIX + '/' + one['id'])
        assert response.status == 200
        remaining = (await response.json())['imports']
        assert len(remaining) == 1
        assert remaining[0]['model'] == 'documented-model-b'
        repeated = await restarted.delete(PREFIX + '/' + one['id'])
        assert repeated.status == 404
        assert (await repeated.json())['error'] == 'Unknown imported observation'
        assert (await authenticate(restarted))['imports'] == remaining
        assert table_path.read_bytes() == original_table


@pytest.mark.asyncio
async def test_recorded_table_history_selection_and_unknown_metrics_native_tool():
    publish_table('judge-old-record', 0.5)
    publish_table('judge-new-record')
    async with TestClient(TestServer(application())) as client:
        data = await authenticate(client)
        assert set(data['runs']) == {'judge-old-record', 'judge-new-record'}
        selected = await client.get(PREFIX, params={'run': 'judge-new-record'})
        assert selected.status == 200
        value = await selected.json()
        assert value['selected_run'] == 'judge-new-record'
        assert value['benchmark']['served_model'] is None
        assert value['benchmark']['usage_basis'] == 'not_recorded_in_table'
        row = value['benchmark']['rows'][0]
        assert row['agreement'] is None
        assert row['cost_usd'] is None
        assert row['wall_secs'] == 0.12
        assert row['verifier_absent'] == 2
        assert row['protocol_errors'] == 1
        assert row['tier'] == 'small'
        assert 'fixture_home' not in json.dumps(value)
        assert str(config_dir()) not in json.dumps(value)
        absent = await client.get(PREFIX, params={'run': '../../outside'})
        assert absent.status == 404
        assert not (config_dir() / 'outside').exists()
        provider = create_provider()
        definitions = await provider.list_tools()
        definition = next(tool for tool in definitions if tool.name == 'platform_model_comparisons')
        assert not definition.requires_approval
        result = await provider.invoke(definition.name, {'run_id': 'judge-new-record'})
        assert result.success
        assert json.loads(result.output) == value
        failure = await provider.invoke(definition.name, {'run_id': 'missing'})
        assert not failure.success
        invalid = await provider.invoke(definition.name, {'execute': True})
        assert not invalid.success
        manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
        assert definition.name in manifest['provider']['capabilities']


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'source': 'file:///etc/passwd'}, {'source': 'javascript:alert(1)'}, {'source': 'https://user:pass@example.org'},
    {'observed_at': '2026-99-01'}, {'model': ''}, {'model': 'x' * 501}, {'methodology': 'x' * 2001},
    {'score': True}, {'score': '75'}, {'score': None}, {'corpus': ['one']}, {'metric': ' '},
])
async def test_invalid_source_and_observation_fields_preserve_store(changes):
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        accepted = await client.post(PREFIX, json=imported())
        assert accepted.status == 200
        path = config_dir() / 'evals/model-comparisons.json'
        before = path.read_bytes()
        rejected = await client.post(PREFIX, json=imported(**changes))
        assert rejected.status == 400
        assert 'error' in await rejected.json()
        assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_corrupt_store_fails_closed_and_auth_rejects_app_scope():
    async with TestClient(TestServer(application())) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        assert (await client.post(PREFIX, json=imported())).status in {401, 403}
        app = await client.get(PREFIX, params={'token': generate_token('owner', app='third-party')})
        assert app.status in {401, 403}
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        path = config_dir() / 'evals/model-comparisons.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{broken')
        failed = await client.post(PREFIX, json=imported())
        assert failed.status == 503
        assert path.read_text() == '{broken'
        read = await client.get(PREFIX)
        assert read.status == 503
        path.write_text('[]')
        assert (await client.get(PREFIX)).status == 200
        assert view()['benchmark'] is None
