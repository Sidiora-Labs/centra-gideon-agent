import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
from gideon.automation.triggers import arm, claims
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.registry import register_trigger_store, unregister_trigger_store
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.triggers.service import to_iso
from gideon.automation.workflows import defs
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.interfaces.dashboard.handlers.capabilities_forecast import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_ephemeral_secret
from gideon.workspace.capabilities.platform import cadence, forecast, maintenance
from gideon.workspace.capabilities.platform.tools import create_provider

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()
PREFIX = '/api/capabilities/platform/forecast'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    return tmp_path / 'home'


def add(identifier='clock-one', spec=None, **values):
    trigger = Trigger(id=identifier, name=identifier, kind='clock', spec=spec or {'kind': 'interval', 'interval_secs': 60}, workflow={'ref': 'audit-workflow'}, **values)
    TriggerStore().upsert(trigger)
    assert TriggerStore().get(identifier).ok
    return trigger


@pytest.mark.asyncio
async def test_actual_clock_times_and_no_schedule_or_claim_mutations(home):
    value = add(next_fire_at=to_iso(NOW + 30))
    before = (home / 'triggers.json').read_bytes()
    result = await forecast.view(now=NOW, horizon=180)
    assert [event['at'] for event in result['events']] == [NOW + 30, NOW + 90, NOW + 150]
    assert all(event['trigger_id'] == value.id for event in result['events'])
    assert all(event['conditional'] is True for event in result['events'])
    assert all(event['duration_seconds'] is None for event in result['events'])
    assert all(event['edit_url'] == '#/triggers?open=clock-one' for event in result['events'])
    assert result['events'][0]['admission_now']['allowed'] is True
    assert 'future state' in result['qualification']
    assert result['truncated'] is False
    assert (home / 'triggers.json').read_bytes() == before
    assert claims.read_claim(value.id, now=NOW, base_dir=home) is None
    assert TriggerStore().get(value.id).trigger.run_count == 0


@pytest.mark.asyncio
async def test_cron_timezone_skip_dates_once_and_expiry_use_real_clock_rules(home):
    cron = add('cron', {'kind': 'cron', 'expr': '0 15 * * *', 'timezone': 'Europe/Berlin'})
    skipped = add('skipped', {'kind': 'cron', 'expr': '0 15 * * *', 'timezone': 'Europe/Berlin', 'skip_dates': ['2026-10-01']})
    once = add('once', {'kind': 'at', 'at': NOW + 120})
    expired = add('expired', expires_at=to_iso(NOW + 30))
    result = await forecast.view(now=NOW, horizon=86400)
    cron_events = [row for row in result['events'] if row['trigger_id'] == cron.id]
    assert cron_events[0]['at'] == arm.next_fire(cron, now=NOW)
    assert cron_events[0]['at'] == NOW + 3600
    assert not [row for row in result['events'] if row['trigger_id'] == skipped.id]
    assert [row['at'] for row in result['events'] if row['trigger_id'] == once.id] == [NOW + 120]
    assert not [row for row in result['events'] if row['trigger_id'] == expired.id]
    assert TriggerStore().get(once.id).trigger.enabled


@pytest.mark.asyncio
async def test_actual_registered_store_and_native_rows_merge_without_writes(home, tmp_path):
    add('native')
    external = TriggerStore(tmp_path / 'other-store')
    provider_trigger = Trigger(id='provider:clock', name='Provider clock', kind='clock', spec={'kind': 'interval', 'interval_secs': 90}, workflow={'ref': 'audit-workflow'})
    external.upsert(provider_trigger)
    before = (tmp_path / 'other-store/triggers.json').read_bytes()
    register_trigger_store('real-other-store', external)
    try:
        result = await forecast.view(now=NOW, horizon=180)
        assert {row['trigger_id'] for row in result['events']} == {'native', 'provider:clock'}
        assert [row['at'] for row in result['events'] if row['trigger_id'] == 'provider:clock'] == [NOW + 90, NOW + 180]
        assert [row for row in result['events'] if row['trigger_id'] == 'provider:clock'][0]['edit_url'] == '#/triggers?open=provider%3Aclock'
        assert (tmp_path / 'other-store/triggers.json').read_bytes() == before
        assert TriggerStore().get('provider:clock') is None
    finally:
        unregister_trigger_store('real-other-store')


@pytest.mark.asyncio
async def test_disabled_event_and_parked_current_admission_and_expired_retry(home):
    add('disabled', enabled=False)
    parked = add('parked', state='parked', park_retry_after=NOW + 120)
    recovered = add('recovered', state='parked', park_retry_after=NOW - 1)
    event = Trigger(id='manual', name='Manual', kind='manual', workflow={'ref': 'audit-workflow'})
    TriggerStore().upsert(event)
    result = await forecast.view(now=NOW, horizon=120)
    assert {row['trigger_id'] for row in result['events']} == {recovered.id}
    reasons = {row['trigger_id']: row['reason'] for row in result['excluded']}
    assert reasons['disabled'] == 'disabled_or_active'
    assert reasons[parked.id] == 'disabled_or_parked'
    assert reasons[event.id] == 'event_driven'
    assert next(row for row in result['excluded'] if row['trigger_id'] == event.id)['next_at'] is None
    assert TriggerStore().get(recovered.id).trigger.state == 'parked'
    assert TriggerStore().get(recovered.id).trigger.park_retry_after == NOW - 1


@pytest.mark.asyncio
async def test_actual_rate_gate_and_cadence_are_reflected_without_running(home):
    value = add(gates={'max_runs_per_hour': 1})
    journal = ExecutionJournal(home)
    for index in range(5):
        await journal.append(ExecutionRecord(run_id=str(index), job_id=value.id, trigger='failed', status='failure', started_at=NOW - 10 + index, finished_at=NOW - 10 + index))
    cadence.mutate(value.id, {'revision': 0, 'enabled': True, 'task_class': 'audit'})
    before = (home / 'cron-history' / (value.id + '.jsonl')).read_bytes()
    result = await forecast.view(now=NOW, horizon=600)
    assert [row['at'] for row in result['events']] == [NOW + 240, NOW + 480]
    assert all(row['cadence_reason'] == 'low_execution_success' for row in result['events'])
    assert all(row['admission_now']['allowed'] is False for row in result['events'])
    assert all(row['admission_now']['gate'] == 'rate' for row in result['events'])
    assert (home / 'cron-history' / (value.id + '.jsonl')).read_bytes() == before
    assert TriggerStore().get(value.id).trigger.last_run_id == ''


@pytest.mark.asyncio
async def test_real_maintenance_dependency_times_stay_unknown(home, tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    project = HierarchyStore().create_project('Dependency project', workspace_dir=str(workspace))
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    await provider.save_def(name='code-project', root={'kind': 'transform', 'id': 'actual', 'config': {'expr': 'done'}})
    watchdog = WorkflowWatchdog()
    try:
        row = await maintenance.create({'project_id': project.id, 'verify_command': 'python -m pytest', 'guard_command': 'git diff --check'}, 'user:owner', watchdog)
        issue = await NativeTaskProvider().create_task(title='Pending maintenance finding', task_list_id=row['task_list_id'])
        before = maintenance._path(row['id']).read_bytes()
        result = await forecast.view(now=NOW, horizon=180)
        assert result['dependencies'] == [{'maintenance_id': row['id'], 'project_id': project.id, 'status': 'running', 'child_id': None, 'pending_issues': 1, 'issue_count_complete': True, 'next_at': None}]
        assert result['events'] == []
        assert (await NativeTaskProvider().get_task(issue.id)).status.value == 'open'
        assert maintenance._path(row['id']).read_bytes() == before
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_bounded_forecast_and_invalid_horizon(home):
    add(spec={'kind': 'interval', 'interval_secs': 1})
    result = await forecast.view(now=NOW, horizon=3600)
    assert len(result['events']) == 20
    assert result['truncated'] is True
    assert result['events'][0]['at'] == NOW + 1
    assert result['events'][-1]['at'] == NOW + 20
    for invalid in (0, 59, 86401, True, '3600'):
        with pytest.raises(ValueError):
            await forecast.view(now=NOW, horizon=invalid)


@pytest.mark.asyncio
async def test_signed_http_and_native_forecast_are_read_only(home):
    add()
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    before = (home / 'triggers.json').read_bytes()
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        response = await client.get(PREFIX, params={'token': generate_token('owner')})
        assert response.status == 200
        assert (await response.json())['events']
        response = await client.get(PREFIX, params={'horizon': '180'})
        assert response.status == 200
        assert (await response.json())['horizon'] == 180
        assert (await client.get(PREFIX, params={'horizon': 'invalid'})).status == 400
        assert (await client.post(PREFIX, json={})).status == 405
        provider = create_provider()
        native = await provider.invoke('platform_schedule_forecast', {'horizon': 120})
        assert native.success
        assert json.loads(native.output)['horizon'] == 120
        assert len(json.loads(native.output)['events']) == 2
        tool = next(item for item in await provider.list_tools() if item.name == 'platform_schedule_forecast')
        assert not tool.requires_approval
        manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
        assert tool.name in manifest['provider']['capabilities']
        assert (home / 'triggers.json').read_bytes() == before
