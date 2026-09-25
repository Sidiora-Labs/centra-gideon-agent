import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.automation.workflows import defs, service, store
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.interfaces.dashboard.handlers.capabilities_maintenance import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_ephemeral_secret
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.platform import maintenance as m
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/maintenance'


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'original.txt').write_text('Original project remains the source of truth')
    return HierarchyStore().create_project('Maintenance project', workspace_dir=str(workspace))


async def definition(name='code-project', wait=False):
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    return await provider.save_def(name=name, inputs={key: {'required': True} for key in ('task', 'cwd', 'verify_command', 'guard_command')}, root={'kind': 'wait' if wait else 'transform', 'id': 'actual', 'config': {'duration_secs': 300} if wait else {'expr': 'done'}})


def body(project, **extras):
    return {'project_id': project.id, 'verify_command': 'python -m pytest', 'guard_command': 'git diff --check', **extras}


async def settle(row, watchdog):
    row = await m.advance(row['id'], watchdog)
    if row['child_id']:
        controller = watchdog.controller(row['child_id'])
        if controller:
            await controller.run_to_completion(timeout=5)
    return row


@pytest.mark.asyncio
async def test_complete_seven_real_workflows_and_persisted_order(project):
    await definition()
    watchdog = WorkflowWatchdog()
    try:
        row = await m.create(body(project), 'user:owner', watchdog)
        assert row['status'] == 'running'
        assert row['stage'] == 0
        assert row['history'] == []
        for _ in range(8):
            row = await settle(row, watchdog)
        assert row['status'] == 'complete'
        assert [entry['stage'] for entry in row['history']] == list(m.AUDITS)
        assert all(entry['status'] == 'complete' for entry in row['history'])
        runs, count = store.list_runs(project_id=project.id)
        assert count == 7
        assert len({child.id for child in runs}) == 7
        for child in runs:
            assert child.status.value == 'complete'
            assert child.inputs['cwd'] == project.workspace_dir
            assert child.inputs['verify_command'] == 'python -m pytest'
            assert child.inputs['guard_command'] == 'git diff --check'
            assert child.inputs['maintenance_key'].startswith('maintenance:')
            assert 'Maintenance issues list' in child.inputs['task']
        assert json.loads(m._path(row['id']).read_text()) == row
        assert m.view()['runs'] == [row]
        assert m.view()['projects'] == [{'id': project.id, 'name': project.name}]
        assert (Path(project.workspace_dir) / 'original.txt').read_text().startswith('Original')
        assert (await m.advance(row['id'], watchdog)) == row
        assert store.list_runs(project_id=project.id)[1] == 7
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_real_issue_drain_no_progress_and_resume_after_actual_cancellation(project):
    await definition()
    watchdog = WorkflowWatchdog()
    provider = NativeTaskProvider()
    try:
        row = await m.create(body(project), 'user:owner', watchdog)
        issue = await provider.create_task(title='Actual maintenance issue', task_list_id=row['task_list_id'])
        row = await settle(row, watchdog)
        for _ in range(8):
            row = await settle(row, watchdog)
            if row['status'] == 'failed':
                break
        assert row['status'] == 'failed'
        assert row['stage'] == 1
        assert row['no_progress'] == 3
        assert 'no progress' in row['error']
        assert (await provider.get_task(issue.id)).status.value == 'open'
        assert [item['stage'] for item in row['history']] == [m.AUDITS[0], 'drain', 'drain', 'drain']
        from gideon.engine.tasks.models import TaskStatus
        await provider.update_task(issue.id, status=TaskStatus.CANCELLED)
        row = await m.control(row['id'], 'resume', watchdog)
        for _ in range(8):
            row = await settle(row, watchdog)
        assert row['status'] == 'complete'
        assert len(row['history']) == 10
        assert (await provider.get_task(issue.id)).status.value == 'cancelled'
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_launch_write_crash_recovers_existing_real_child(project):
    await definition()
    watchdog = WorkflowWatchdog()
    try:
        row = await m.create(body(project), 'user:owner', watchdog)
        before = dict(row)
        launched = await settle(row, watchdog)
        assert launched['child_id']
        m._save(before)
        recovered = await m.advance(row['id'], watchdog)
        assert recovered['history'][0]['child_id'] == launched['child_id']
        assert len(recovered['history']) == 1
        assert recovered['stage'] == 2
        assert store.list_runs(project_id=project.id)[1] == 2
        assert m._key(before) != m._key(recovered)
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_cancel_sticky_wait_child_then_resume_new_attempt(project):
    await definition(wait=True)
    watchdog = WorkflowWatchdog()
    try:
        row = await m.create(body(project), 'user:owner', watchdog)
        row = await m.advance(row['id'], watchdog)
        original = row['child_id']
        assert watchdog.controller(original)
        cancelled = await m.control(row['id'], 'cancel', watchdog)
        assert cancelled['status'] == 'cancelling'
        with pytest.raises(ValueError, match='current state'):
            await m.control(row['id'], 'resume', watchdog)
        await watchdog.controller(original).run_to_completion(timeout=5)
        cancelled = await m.advance(row['id'], watchdog)
        assert cancelled['status'] == 'cancelled'
        assert store.get(original).status.value == 'cancelled'
        assert cancelled['stage'] == 0
        resumed = await m.control(row['id'], 'resume', watchdog)
        assert resumed['status'] == 'running'
        assert resumed['child_id'] != original
        assert store.list_runs(project_id=project.id)[1] == 2
        await m.control(row['id'], 'cancel', watchdog)
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_definition_pin_and_workspace_binding_fail_before_new_launch(project):
    await definition()
    watchdog = WorkflowWatchdog()
    try:
        row = await m.create(body(project), 'user:owner', watchdog)
        await definition(wait=True)
        failed = await m.advance(row['id'], watchdog)
        assert failed['status'] == 'failed'
        assert 'definition changed' in failed['error']
        assert store.list_runs(project_id=project.id)[1] == 0
        row = await m.create(body(project), 'user:owner', watchdog)
        Path(project.workspace_dir).rename(Path(project.workspace_dir).with_name('offline'))
        failed = await m.advance(row['id'], watchdog)
        assert failed['status'] == 'failed'
        assert 'workspace binding' in failed['error']
        assert store.list_runs(project_id=project.id)[1] == 0
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_validation_missing_workflow_duplicate_and_cancel_before_launch(project):
    watchdog = WorkflowWatchdog()
    try:
        with pytest.raises(ValueError, match='not installed'):
            await m.create(body(project, workflow='absent'), 'owner', watchdog)
        await definition()
        for invalid in ({}, body(project, verify_command=''), body(project, guard_command=''), body(project, extra='value')):
            with pytest.raises(ValueError):
                await m.create(invalid, 'owner', watchdog)
        with pytest.raises(ValueError, match='Authenticated'):
            await m.create(body(project), '', watchdog)
        with pytest.raises(ValueError, match='supervisor'):
            await m.create(body(project), 'owner', None)
        row = await m.create(body(project), 'owner', watchdog)
        with pytest.raises(ValueError, match='already has active'):
            await m.create(body(project), 'owner', watchdog)
        result = await m.control(row['id'], 'cancel', watchdog)
        assert result['status'] == 'cancelled'
        assert result['history'] == []
        assert store.list_runs(project_id=project.id)[1] == 0
        with pytest.raises(ValueError):
            m.get('../../private')
        assert len(m.view()['runs']) == 1
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_real_signed_http_and_native_status_control(project):
    await definition(wait=True)
    watchdog = WorkflowWatchdog()
    app = web.Application(middlewares=[token_auth_middleware()])
    app['state'] = SimpleNamespace(workflows=watchdog)
    register(app)
    try:
        async with TestClient(TestServer(app)) as client:
            assert (await client.get(PREFIX)).status in {401, 403}
            response = await client.get(PREFIX, params={'token': generate_token('maintenance-owner')})
            assert response.status == 200
            assert (await response.json())['audits'] == list(m.AUDITS)
            response = await client.post(PREFIX, json=body(project))
            assert response.status == 200
            row = await response.json()
            assert row['actor'] == 'user:maintenance-owner'
            assert (await client.post(PREFIX, json=body(project))).status == 409
            provider = create_provider()
            read = await provider.invoke('platform_maintenance', {})
            assert read.success
            assert json.loads(read.output)['runs'][0]['id'] == row['id']
            token = set_current_session_key('')
            try:
                denied = await provider.invoke('platform_maintenance_control', {'id': row['id'], 'action': 'cancel'})
                assert not denied.success
            finally:
                reset_current_session_key(token)
            token = set_current_session_key('agent:owner')
            try:
                controlled = await provider.invoke('platform_maintenance_control', {'id': row['id'], 'action': 'cancel'})
                assert controlled.success
                assert json.loads(controlled.output)['status'] in {'cancelled', 'cancelling'}
            finally:
                reset_current_session_key(token)
            tool = next(item for item in await provider.list_tools() if item.name == 'platform_maintenance_control')
            assert tool.requires_approval
            manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
            assert tool.name in manifest['provider']['capabilities']
            assert (await client.post(PREFIX + '/' + row['id'], json={'action': 'unsupported'})).status == 409
            assert (await client.post(PREFIX + '/' + row['id'], json={'action': 'cancel', 'actor': 'forged'})).status == 409
    finally:
        await watchdog.stop()
