import json
from pathlib import Path

import pytest
from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.registry import ProviderRegistry, ToolTypeHandler
from gideon.integrations.tool_providers.registry import get_provider
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret, use_persistent_secret


ROOT = Path(__file__).parents[4]
MANIFEST = ROOT / 'runtime/gideon/extensions/apps/native/gideon-privacy-broker-spokeo/app.json'


@pytest.mark.asyncio
async def test_parent_signed_route_and_native_provider_are_wired_without_broker_writes(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    registry = ProviderRegistry(); registry.register_type_handler('tool', ToolTypeHandler())
    registry.register(AppManifest.from_json_file(MANIFEST), enabled=True)
    try:
        provider = get_provider('gideon-privacy-broker-spokeo')
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert set(definitions) == {'privacy_broker_spokeo_scan', 'privacy_broker_spokeo_prepare', 'privacy_broker_spokeo_submit', 'privacy_broker_spokeo_verify'}
        assert all(tool.requires_approval for tool in definitions.values())
        missing = await provider.invoke('privacy_broker_spokeo_scan', {'case_id': 'missing', 'request_id': 'scan', 'revision': 1,
            'first_name': 'Jane', 'last_name': 'Doe', 'state': 'CA'})
        assert missing.success is False and missing.metadata['status'] == 404

        use_ephemeral_secret()
        try:
            app = web.Application(middlewares=[token_auth_middleware()]); app.router.add_get('/session', lambda request: web.json_response({'ok': True})); register(app, tmp_path)
            route = '/api/capabilities/wellbeing/privacy/broker-cases/missing/providers/spokeo/scan'
            async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
                assert (await client.post(route, json={})).status in (401, 403)
                assert (await client.get('/session?token=' + generate_token('privacy-owner'))).status == 200
                response = await client.post(route, json={'request_id': 'http', 'revision': 1, 'first_name': 'Jane', 'last_name': 'Doe', 'state': 'CA'})
                assert response.status == 404
        finally: use_persistent_secret()
    finally: registry.deregister('gideon-privacy-broker-spokeo')
