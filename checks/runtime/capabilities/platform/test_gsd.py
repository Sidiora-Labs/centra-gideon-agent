import hashlib
import json
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.core.config.loader import config_dir
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.interfaces.dashboard.handlers.capabilities_gsd import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/gsd'


@pytest.fixture
def planning(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    workspace = tmp_path / 'workspace'
    root = workspace / '.planning'
    phase = root / 'phases/01-editor'
    phase.mkdir(parents=True)
    (root / 'PROJECT.md').write_text('# Editor project\nOriginal planning content.\n')
    (root / 'ROADMAP.md').write_text('# Roadmap\n- Phase 01: editor\n')
    (root / 'STATE.md').write_text('---\nphase: 01\n---\nPending planning.\n')
    (phase / '01-PLAN.md').write_text('# Plan\nBuild editor.\n')
    (phase / '01-SUMMARY.md').write_text('# Summary\nArtifact presence is not execution verification.\n')
    (root / 'private.txt').write_text('not a supported planning document')
    project = HierarchyStore().create_project('GSD project', workspace_dir=str(workspace))
    return project, root


def application():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    return app


async def authenticate(client):
    response = await client.get(PREFIX, params={'token': generate_token('gsd-owner')})
    assert response.status == 200
    return await response.json()


@pytest.mark.asyncio
async def test_real_planning_read_edit_sha_conflict_and_restart(planning):
    project, root = planning
    async with TestClient(TestServer(application())) as client:
        projects = await authenticate(client)
        assert projects['projects'] == [{'id': project.id, 'name': project.name}]
        detail = await client.get(PREFIX + '/' + project.id, params={'document': 'STATE.md'})
        assert detail.status == 200
        value = await detail.json()
        assert 'private.txt' not in value['documents']
        assert value['phases'] == [{'id': '01-editor', 'plans': ['phases/01-editor/01-PLAN.md'], 'summaries': ['phases/01-editor/01-SUMMARY.md']}]
        observed = value['document']
        original = (root / 'STATE.md').read_bytes()
        assert observed['sha256'] == hashlib.sha256(original).hexdigest()
        assert observed['content'] == original.decode()
        changed = await client.put(PREFIX + '/' + project.id, json={'document': 'STATE.md', 'sha256': observed['sha256'], 'content': 'Phase 01 is ready for an explicit request.\n'})
        assert changed.status == 200
        current = (await changed.json())['document']
        assert current['content'] == (root / 'STATE.md').read_text()
        assert current['sha256'] != observed['sha256']
        stale = await client.put(PREFIX + '/' + project.id, json={'document': 'STATE.md', 'sha256': observed['sha256'], 'content': 'stale overwrite'})
        assert stale.status == 409
        assert 'reload' in (await stale.json())['error']
        assert (root / 'STATE.md').read_text() == current['content']
        assert (root / 'PROJECT.md').read_text().startswith('# Editor project')
        assert not (root.parent / '.git').exists()
        assert HierarchyStore().get_project(project.id).agent_instructions_template == ''
    async with TestClient(TestServer(application())) as restarted:
        await authenticate(restarted)
        response = await restarted.get(PREFIX + '/' + project.id, params={'document': 'STATE.md'})
        assert (await response.json())['document'] == current


@pytest.mark.asyncio
async def test_phase_actions_create_real_open_native_tasks_once_and_native_consumer(planning):
    project, root = planning
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        created_ids = []
        for action in ('plan', 'execute', 'verify'):
            result = await client.post(f'{PREFIX}/{project.id}/phase', json={'phase': '01-editor', 'action': action})
            assert result.status == 200, await result.text()
            body = await result.json()
            assert body['created'] is True
            assert body['execution'] == 'not_started_by_this_request'
            task = await NativeTaskProvider().get_task(body['task']['id'])
            assert task is not None
            assert task.status.value == 'open'
            assert task.author == 'user:gsd-owner'
            assert task.project == 'GSD project'
            assert task.title == f'GSD {action}: 01-editor'
            assert task.agent_instructions_template == ''
            assert 'not completed execution' in task.description
            created_ids.append(task.id)
            repeated = await client.post(f'{PREFIX}/{project.id}/phase', json={'phase': '01-editor', 'action': action})
            replay = await repeated.json()
            assert replay['created'] is False
            assert replay['task']['id'] == task.id
        task_lists = HierarchyStore().list_task_lists(project_id=project.id)
        assert [task_list.name for task_list in task_lists] == ['GSD']
        tasks, total = await NativeTaskProvider().list_tasks(project='GSD project')
        assert total == 3
        assert {task.id for task in tasks} == set(created_ids)
        provider = create_provider()
        document = await provider.invoke('platform_gsd_project', {'project_id': project.id, 'document': 'PROJECT.md'})
        assert document.success
        assert json.loads(document.output)['document']['content'] == (root / 'PROJECT.md').read_text()
        definitions = await provider.list_tools()
        tool = next(tool for tool in definitions if tool.name == 'platform_gsd_phase_task')
        assert tool.requires_approval
        token = set_current_session_key('agent:gsd-worker')
        try:
            native = await provider.invoke(tool.name, {'project_id': project.id, 'phase': '01-editor', 'action': 'verify'})
            assert native.success
            assert json.loads(native.output)['created'] is False
            assert json.loads(native.output)['task']['id'] in created_ids
        finally:
            reset_current_session_key(token)
        manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
        assert tool.name in manifest['provider']['capabilities']
        assert 'platform_gsd_project' in manifest['provider']['capabilities']


@pytest.mark.asyncio
@pytest.mark.parametrize('document', ['../outside.md', '/etc/passwd', 'private.txt', 'phases/../private.md', 'phases/01-editor/../../../outside.md'])
async def test_unsupported_paths_are_rejected_for_read_and_write(planning, document):
    project, root = planning
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        read = await client.get(PREFIX + '/' + project.id, params={'document': document})
        assert read.status == 400
        write = await client.put(PREFIX + '/' + project.id, json={'document': document, 'content': 'overwrite', 'sha256': 'x'})
        assert write.status == 400
        assert (root / 'private.txt').read_text() == 'not a supported planning document'
        assert (root / 'STATE.md').read_text().startswith('---')


@pytest.mark.asyncio
async def test_symlink_escape_size_auth_and_unknown_phase(planning):
    project, root = planning
    async with TestClient(TestServer(application())) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        response = await client.get(PREFIX, params={'token': generate_token('owner', app='other')})
        assert response.status in {401, 403}
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        unknown = await client.post(f'{PREFIX}/{project.id}/phase', json={'phase': 'missing', 'action': 'plan'})
        assert unknown.status == 404
        bad_action = await client.post(f'{PREFIX}/{project.id}/phase', json={'phase': '01-editor', 'action': 'arbitrary'})
        assert bad_action.status == 400
        oversized = await client.put(PREFIX + '/' + project.id, json={'document': 'STATE.md', 'content': 'x' * 262145, 'sha256': 'x'})
        assert oversized.status == 413
        outside = root.parent.parent / 'outside.md'
        outside.write_text('outside unchanged')
        (root / 'CONCERNS.md').symlink_to(outside)
        escaped = await client.get(PREFIX + '/' + project.id, params={'document': 'CONCERNS.md'})
        assert escaped.status == 400
        assert outside.read_text() == 'outside unchanged'
        (root / 'CONCERNS.md').unlink()
        (root / 'STATE.md').write_bytes(b'\xff')
        invalid = await client.get(PREFIX + '/' + project.id)
        assert invalid.status == 400
        tasks, total = await NativeTaskProvider().list_tasks(project='GSD project')
        assert total == 0
        assert tasks == []


@pytest.mark.asyncio
async def test_no_workspace_or_planning_and_missing_native_actor(planning):
    project, root = planning
    unbound = HierarchyStore().create_project('Unbound project')
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        assert (await client.get(PREFIX + '/' + unbound.id)).status == 409
        assert (await client.get(PREFIX + '/p-missing')).status == 404
        provider = create_provider()
        token = set_current_session_key('')
        try:
            denied = await provider.invoke('platform_gsd_phase_task', {'project_id': project.id, 'phase': '01-editor', 'action': 'plan'})
            assert not denied.success
        finally:
            reset_current_session_key(token)
        root.rename(root.with_name('.planning-offline'))
        assert (await client.get(PREFIX + '/' + project.id)).status == 409
