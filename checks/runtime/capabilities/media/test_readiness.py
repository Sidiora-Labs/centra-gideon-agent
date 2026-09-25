import json
import os
import subprocess
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.integrations.image_gen.openai_provider import OpenAIImageProvider
from gideon.integrations.image_gen.provider import ImageGenModel
from gideon.integrations.video_gen.provider import VideoGenModel
from gideon.integrations.media_catalogs import MediaCatalog, MediaModel, register_media_catalog, unregister_media_catalogs
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.readiness import MediaReadiness, describe_model, probe
from gideon.workspace.capabilities.media.readiness_http import register_readiness
from gideon.workspace.capabilities.media.sketches import SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.mark.asyncio
async def test_unconfigured_and_unregistered_are_distinct():
    row = await probe('image_gen', '', None)
    assert row['status'] == 'unconfigured'
    assert row['inference_verified'] is False
    assert row['available'] is False
    assert row['selection'] == ''
    assert row['model'] is None
    assert row['management_available'] is False
    assert 'Select a model' in row['reason']
    missing = await probe('video_gen', 'missing:clip', None)
    assert missing['status'] == 'provider_missing'
    assert missing['selection'] == 'missing:clip'
    assert missing['available'] is False
    assert missing['inference_verified'] is False
    assert missing['model'] is None


@pytest.mark.asyncio
async def test_actual_openai_adapter_without_credential_is_unavailable():
    provider = OpenAIImageProvider(provider_name='media-no-key', endpoint='http://127.0.0.1:1', api_key='')
    row = await probe('image_gen', 'media-no-key:image', provider)
    assert row['status'] == 'unavailable'
    assert row['available'] is False
    assert row['inference_verified'] is False
    assert row['model'] is None
    assert row['management_available'] is False
    assert '127.0.0.1' not in json.dumps(row)
    assert 'api_key' not in json.dumps(row)


@pytest.mark.asyncio
async def test_actual_adapter_catalog_success_is_not_credential_or_generation_proof():
    kind = 'media-readiness-local-catalog'
    register_media_catalog('image_gen', kind, MediaCatalog(models=(MediaModel('local-catalog-model', extra={'sizes': ['512x512'], 'supports_edit': True}),)))
    try:
        provider = OpenAIImageProvider(provider_name='media-catalog', provider_type=kind, endpoint='http://127.0.0.1:1', api_key='not-a-valid-network-credential')
        row = await probe('image_gen', 'media-catalog:local-catalog-model', provider)
        assert row['status'] == 'configured'
        assert row['available'] is True
        assert row['inference_verified'] is False
        assert row['model']['name'] == 'local-catalog-model'
        assert row['model']['sizes'] == ['512x512']
        assert row['model']['supports_edit'] is True
        assert row['model']['downloaded'] is True
        assert 'not been verified' in row['reason']
        assert 'not-a-valid-network-credential' not in json.dumps(row)
        assert '127.0.0.1' not in json.dumps(row)
        missing = await probe('image_gen', 'media-catalog:other', provider)
        assert missing['status'] == 'model_missing'
        assert missing['inference_verified'] is False
        assert missing['model'] is None
    finally:
        unregister_media_catalogs(kind)


def test_real_image_model_metadata_distinguishes_download_and_management():
    row = dict(available=True, inference_verified=False, management_available=True)
    model = ImageGenModel(name='image:v2', downloaded=False, sizes=['256x256'], supports_edit=False)
    result = describe_model(row, 'image_gen', 'local:image:v2', [model])
    assert result['status'] == 'not_downloaded'
    assert result['management_available'] is True
    assert result['available'] is True
    assert result['inference_verified'] is False
    assert result['model']['downloaded'] is False
    assert result['model']['name'] == 'image:v2'
    assert result['model']['sizes'] == ['256x256']
    assert result['model']['supports_edit'] is False
    assert 'aspect_ratios' not in result['model']


def test_real_video_model_metadata_preserves_duration_and_aspect_ratios():
    row = dict(available=True, inference_verified=False, management_available=False)
    model = VideoGenModel(name='clip', aspect_ratios=['16:9', '9:16'], max_duration_s=8)
    result = describe_model(row, 'video_gen', 'remote:clip', [model])
    assert result['status'] == 'configured'
    assert result['management_available'] is False
    assert result['model']['aspect_ratios'] == ['16:9', '9:16']
    assert result['model']['max_duration_s'] == 8
    assert result['model']['downloaded'] is True
    assert 'sizes' not in result['model']
    assert 'supports_edit' not in result['model']
    assert result['inference_verified'] is False
    missing = describe_model(row, 'video_gen', 'remote:unknown', [model])
    assert missing['status'] == 'model_missing'


@pytest.mark.asyncio
async def test_durable_observation_records_real_probe_then_reopens(tmp_path):
    service = MediaReadiness(tmp_path / 'readiness.sqlite3')
    assert service.get() == dict(observed_at=None, items=[], inference_verified=False)
    row = await probe('image_gen', '', None)
    saved = service.record([row])
    assert saved['observed_at']
    assert saved['items'] == [row]
    assert saved['inference_verified'] is False
    reopened = MediaReadiness(service.path)
    assert reopened.get() == saved
    next_row = await probe('video_gen', 'unregistered:video', None)
    updated = reopened.record([next_row])
    assert updated['observed_at'] >= saved['observed_at']
    assert service.get() == updated
    assert service.get()['items'][0]['status'] == 'provider_missing'


@pytest.mark.asyncio
async def test_real_http_cached_observation_rejects_caller_scope(tmp_path):
    service = MediaReadiness(tmp_path / 'readiness.sqlite3')
    saved = service.record([await probe('image_gen', '', None)])
    app = web.Application()
    register_readiness(app, service)
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/api/capabilities/media/readiness')
        assert response.status == 200
        assert await response.json() == saved
        for query in ('home=x', 'provider=x', 'runtime=x', 'refresh=true'):
            response = await client.get('/api/capabilities/media/readiness?' + query)
            assert response.status == 400
        for body in ({'home': '/tmp'}, {'model': 'x'}, [], None):
            response = await client.post('/api/capabilities/media/readiness', json=body)
            assert response.status == 400
        response = await client.post('/api/capabilities/media/readiness', data='{', headers={'Content-Type': 'application/json'})
        assert response.status == 400
        assert service.get() == saved


@pytest.mark.asyncio
async def test_native_readiness_tool_reads_same_observation_without_overrides(tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / 'artifacts')
    sketches = SketchStore(tmp_path / 'sketches.sqlite3', artifacts)
    provider = MediaToolProvider(sketches, MediaLibrary(artifacts))
    saved = provider.readiness.record([await probe('video_gen', '', None)])
    result = await provider.invoke('media_readiness_get', {})
    assert result.success
    assert json.loads(result.output) == saved
    for name in ('media_readiness_get', 'media_readiness_refresh'):
        result = await provider.invoke(name, {'runtime': 'other'})
        assert not result.success
        assert provider.readiness.get() == saved
    definitions = {tool.name: tool for tool in await provider.list_tools()}
    assert definitions['media_readiness_get'].requires_approval is False
    assert definitions['media_readiness_refresh'].parameters['additionalProperties'] is False


def test_isolated_real_registry_refresh_does_not_invent_configured_models(tmp_path):
    code = '''import asyncio,json,sys
from pathlib import Path
from gideon.workspace.capabilities.media.readiness import MediaReadiness
service=MediaReadiness(Path(sys.argv[1])/'readiness.sqlite3')
print(json.dumps(asyncio.run(service.refresh())))
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], env=dict(os.environ, GIDEON_HOME=str(tmp_path)), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert len(observation['items']) == 2
    assert {row['capability'] for row in observation['items']} == {'image_gen', 'video_gen'}
    assert all(row['status'] == 'unconfigured' for row in observation['items'])
    assert all(row['available'] is False for row in observation['items'])
    assert all(row['inference_verified'] is False for row in observation['items'])
    assert MediaReadiness(tmp_path / 'readiness.sqlite3').get() == observation
