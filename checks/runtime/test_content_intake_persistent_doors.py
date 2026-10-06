import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.artifacts import registry, handlers as artifacts
from gideon.interfaces.dashboard.handlers import durability
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token
from gideon.engine.tasks.handlers import register_task_routes
from gideon.automation.workflows import store, filedrop
from gideon.automation.workflows.models import WorkflowRun, RunStatus
from gideon.automation.workflows.handlers import register_workflow_routes


def form(data, name='input.zip', mime='application/zip'):
    body = FormData()
    body.add_field('file', data, filename=name, content_type=mime)
    return body


@pytest.mark.asyncio
async def test_actual_archive_staging_refusal_and_durability_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    staging = tmp_path / 'staging'
    staging.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(staging))
    app = web.Application(middlewares=[token_auth_middleware()])
    register_task_routes(app)
    app.router.add_post('/api/durability/import', durability.api_durability_import)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('home/MANIFEST.json', json.dumps({'version':2, 'members':[]}))
    async with TestClient(TestServer(app)) as client:
        client.session.headers['Authorization'] = 'Bearer ' + generate_token('intake-owner')
        for route in ('/api/projects/import', '/api/durability/import'):
            response = await client.post(route, data=form(b'rm -rf / --no-preserve-root\n'))
            assert response.status == 422, await response.text()
            assert (await response.json())['error']['code'] == 'upload_content_refused'
            assert not list(staging.iterdir())
        response = await client.post('/api/durability/import', data=form(stream.getvalue()))
        assert response.status == 200, await response.text()
        result = await response.json()
        assert result['ok'] and not result['applied'] and not result['manifest']['verified']
        assert not list(staging.iterdir())
        response = await client.post('/api/durability/import', data=form(b'Ordinary invalid ZIP.'))
        assert response.status == 400 and (await response.json())['error']['code'] == 'invalid_archive'
        assert not list(staging.iterdir())


@pytest.mark.asyncio
async def test_actual_artifact_raw_refusal_leaves_revision_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    provider = NativeArtifactProvider(root=tmp_path / 'artifacts')
    art = provider.create_binary(name='Image', data=b'Original bytes.', mime='image/png', kind='image')
    app = web.Application(middlewares=[token_auth_middleware()])
    app['state'] = SimpleNamespace(_restricted_keys=set(), _sessions={})
    artifacts.register_artifact_routes(app)
    with patch.object(registry, 'get_provider', return_value=provider):
        async with TestClient(TestServer(app)) as client:
            client.session.headers['Authorization'] = 'Bearer ' + generate_token('intake-owner')
            response = await client.put(f'/api/artifacts/{art.slug}/raw', data=b'rm -rf /\n',
                headers={'Content-Type':'image/png', 'If-Match':'1'})
            assert response.status == 422, await response.text()
            assert provider.get(art.slug).version == 1 and provider.raw_bytes(art.slug)[0] == b'Original bytes.'
            response = await client.put(f'/api/artifacts/{art.slug}/raw', data=b'Approved image bytes.',
                headers={'Content-Type':'image/png', 'If-Match':'1'})
            assert response.status == 200, await response.text()
            assert provider.get(art.slug).version == 2 and provider.raw_bytes(art.slug)[0] == b'Approved image bytes.'


@pytest.mark.asyncio
async def test_actual_workflow_drop_refusal_and_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    run = WorkflowRun(id=store.new_run_id(), workflow_name='intake-probe', status=RunStatus.RUNNING)
    store.create(run)
    store.write_spec(run.id, {'root':{'kind':'sequence','id':'main'},'file_drop':True})
    app = web.Application(middlewares=[token_auth_middleware()])
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        client.session.headers['Authorization'] = 'Bearer ' + generate_token('intake-owner')
        route = f'/api/workflows/runs/{run.id}/drop'
        response = await client.post(route + '?confirm=true', data=form(b'rm -rf /\n','bad.txt','text/plain'))
        assert response.status == 422, await response.text()
        assert not filedrop.read_manifest(run.id)
        response = await client.post(route, data=form(b'Ordinary reference.','good.txt','text/plain'))
        assert response.status == 428, await response.text()
        assert not filedrop.read_manifest(run.id)
        response = await client.post(route + '?confirm=true', data=form(b'Ordinary reference.','good.txt','text/plain'))
        assert response.status == 200, await response.text()
        assert (filedrop.drop_dir(run.id) / 'good.txt').read_bytes() == b'Ordinary reference.'
        assert len(filedrop.read_manifest(run.id)) == 1


@pytest.mark.asyncio
async def test_durability_cancel_keeps_staging_until_real_reader_finishes(tmp_path, monkeypatch):
    import threading
    from gideon.workspace import portability
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    staging = tmp_path / 'staging'
    staging.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(staging))
    entered, release = threading.Event(), threading.Event()
    seen = {}
    real = portability.validate_import_zip
    def delayed(path):
        seen['path'] = path
        entered.set()
        release.wait(5)
        seen['reader_result'] = real(path)
        return seen['reader_result']
    monkeypatch.setattr(portability, 'validate_import_zip', delayed)
    async def handler(request):
        seen['task'] = asyncio.current_task()
        return await durability.api_durability_import(request)
    app = web.Application(middlewares=[token_auth_middleware()])
    app.router.add_post('/api/durability/import', handler)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('home/MANIFEST.json', json.dumps({'version':2,'members':[]}))
    async with TestClient(TestServer(app)) as client:
        client.session.headers['Authorization'] = 'Bearer ' + generate_token('intake-owner')
        request = asyncio.create_task(client.post('/api/durability/import', data=form(stream.getvalue())))
        assert await asyncio.to_thread(entered.wait, 10)
        seen['task'].cancel()
        await asyncio.sleep(.02)
        assert seen['path'].exists() and not seen['task'].done()
        release.set()
        try:
            await request
        except Exception:
            pass  # The canceled server request must not return a successful import.
        else:
            raise AssertionError('Canceled handler returned a response')
        assert seen['reader_result'][0] is True
        assert not seen['path'].exists() and not list(staging.iterdir())
