"""Native local HTTP deployment authority, isolation and revocation."""
import json
import logging
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.server import _apply_response_policies, _GatewayAccessLogger
from gideon.workspace.artifacts import registry
from gideon.workspace.artifacts.deploy import ArtifactDeployStore, SERVE_HEADERS
from gideon.workspace.artifacts.handlers import register_artifact_routes
from gideon.workspace.artifacts.native import NativeArtifactProvider


@pytest.mark.asyncio
async def test_native_deployment_http_isolation_rotation_and_revocation(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    provider = NativeArtifactProvider(tmp_path / 'artifacts')
    previous = registry._providers.get('native')
    registry.register_provider(provider)
    token_auth.use_ephemeral_secret(b'artifact-origin-boundary')
    token_auth.revoke_all_sessions()
    token = token_auth.generate_token('artifact-owner', kind='desktop')
    owner = {'Authorization': f'Bearer {token}'}
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=0)])
    app['state'] = None
    app['port'] = 0
    app['allowed_origins'] = set()
    register_artifact_routes(app)
    async def prepare(request, response):
        _apply_response_policies(request, response)
    app.on_response_prepare.append(prepare)
    caplog.set_level(logging.INFO, logger="aiohttp.access")
    server = TestServer(app)
    await server.start_server(access_log_class=_GatewayAccessLogger, access_log=logging.getLogger("aiohttp.access"))
    client = TestClient(server)
    await client.start_server()
    try:
        artifact = provider.create(name='Isolated', slug='isolated', kind='html', content='<script>window.test=true</script>')
        response = await client.post('/api/artifacts/isolated/deploy', json={}, headers=owner)
        assert response.status == 200, await response.text()
        deployment = (await response.json())['deployment']
        assert set(deployment) == {'slug', 'entry', 'created_at', 'url'}
        url = deployment['url']
        capability = url.strip('/').split('/')[-1]
        assert len(capability) == 43
        for path, expected in [(url,200),(url.rstrip('/'),308),(url+'missing.js',404),(url.replace(capability,'x'*43),404),('/artifacts/serve/isolated/',403)]:
            response = await client.get(path, allow_redirects=False)
            assert response.status == expected
            for key, value in SERVE_HEADERS.items():
                assert response.headers[key] == value
        response = await client.get(url)
        assert response.headers['Access-Control-Allow-Origin'] == '*'
        assert 'Access-Control-Allow-Credentials' not in response.headers
        assert await response.text() == artifact.content
        assets = ArtifactDeployStore(provider.root).files_root('isolated')
        assets.mkdir()
        (assets/'module.js').write_text('export default 1')
        response = await client.get(url+'module.js', headers={'Origin':'null'})
        assert response.status == 200 and response.headers['Access-Control-Allow-Origin'] == '*'
        outside = tmp_path / 'outside.js'
        outside.write_text('OUTSIDE_SECRET')
        (assets/'linked.js').symlink_to(outside)
        for suffix in ('linked.js', '%2e%2e/outside.js', 'missing.js', 'nested%5cfile'):
            response = await client.get(url+suffix)
            assert response.status in (403,404)
            assert 'OUTSIDE_SECRET' not in await response.text()
        assert (await client.get('/api/artifacts/isolated')).status == 403
        assert (await client.get('/api/artifacts/isolated', headers=owner)).status == 200
        assert (await client.post(url)).status != 200
        response = await client.post('/api/artifacts/isolated/deploy', json={}, headers=owner)
        newer = (await response.json())['deployment']['url']
        assert newer != url and (await client.get(url)).status == 404
        (assets/'linked.js').unlink()
        fingerprint = provider.state_fingerprint('isolated')
        assert not provider.delete_if_state('isolated','0'*64)
        assert (await client.get(newer)).status == 200
        assert provider.delete_if_state('isolated',fingerprint)
        assert (await client.get(newer)).status == 404
        provider.create(name='Reused',slug='isolated',kind='html',content='fresh')
        assert ArtifactDeployStore(provider.root).get('isolated') is None
        assert (await client.get(newer)).status == 404
        stale = ArtifactDeployStore(provider.root).deploy('abandoned')
        provider.create(name='Abandoned',slug='abandoned',kind='html',content='new')
        assert (await client.get(stale.url)).status == 404
        response = await client.post('/api/artifacts/isolated/deploy', json={}, headers=owner)
        teardown_url = (await response.json())['deployment']['url']
        version = provider.get('isolated').version
        response = await client.delete('/api/artifacts/isolated/deploy', headers=owner)
        assert response.status == 200 and (await response.json())['removed']
        assert (await client.get(teardown_url)).status == 404
        assert provider.get('isolated').version == version
        orphan = provider.create(name='Delete',slug='delete',kind='html',content='delete')
        old = ArtifactDeployStore(provider.root).deploy(orphan.slug)
        assert provider.delete(orphan.slug)
        assert (await client.get(old.url)).status == 404
        assert capability not in caplog.text
        assert '/api/artifacts/isolated' in caplog.text
        records=json.loads(ArtifactDeployStore(provider.root).path.read_text())
        assert all('capability' in record and 'url' not in record for record in records)
    finally:
        await client.close()
        registry.unregister_provider('native')
        if previous is not None:
            registry.register_provider(previous)
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()
