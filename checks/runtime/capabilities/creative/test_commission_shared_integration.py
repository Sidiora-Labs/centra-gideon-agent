import json
from pathlib import Path

import pytest
from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.triggers.store import TriggerStore
from gideon.engine.trigger_dispatch import TriggerAction
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.registry import ActionTypeHandler, ProviderRegistry, ToolTypeHandler
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.tool_providers.registry import get_provider
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret, use_persistent_secret
from gideon.workspace.capabilities.creative.commissions import TRIGGER_PREFIX
from gideon.workspace.capabilities.creative.store import IngredientStore
from gideon.workspace.capabilities.creative.works import WorkStore


ROOT = Path(__file__).parents[4]
MANIFEST = ROOT / 'runtime/gideon/extensions/apps/native/gideon-creative-commissions/app.json'


def request(work):
    return {'request_id': 'shared-commission', 'name': 'Scheduled treatment', 'target_ability': 'series',
        'brief': {'intent': 'Prepare the next exact treatment.', 'genre': '', 'category': '', 'style': '', 'constraints': {}, 'seed_refs': []},
        'cadence': {'kind': 'interval', 'seconds': 900, 'timezone': 'UTC'},
        'sources': [{'kind': 'work', 'id': work['id'], 'revision': work['revision']}], 'enabled': True, 'max_attempts': 2,
        'steps': [{'id': 'verify', 'title': 'Verify source', 'operation': 'source.verify', 'depends_on': []},
                  {'id': 'snapshot', 'title': 'Snapshot treatment', 'operation': 'treatment.snapshot', 'depends_on': ['verify']}]}


@pytest.mark.asyncio
async def test_parent_http_and_native_registry_dispatch_the_real_due_trigger(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    works = WorkStore(tmp_path)
    work = works.create({'request_id': 'shared-work', 'title': 'Shared source', 'kind': 'work', 'prompt': '', 'author_ref': None, 'universe_ref': None, 'active_draft_id': None})
    work = works.draft(work['id'], {'request_id': 'shared-draft', 'revision': work['revision'], 'text': 'Exact scheduled source.', 'note': 'canonical'})['work']

    registry = ProviderRegistry()
    registry.register_type_handler('tool', ToolTypeHandler())
    registry.register_type_handler('action', ActionTypeHandler())
    registry.register(AppManifest.from_json_file(MANIFEST), enabled=True)
    try:
        tools = get_provider('gideon-creative-commissions')
        assert tools is not None
        definitions = {item.name: item for item in await tools.list_tools()}
        assert definitions['creative_commission_list'].requires_approval is False
        assert all(definitions[name].requires_approval for name in definitions if name not in ('creative_commission_list', 'creative_commission_get'))
        created = await tools.invoke('creative_commission_create', {'payload': request(work)})
        assert created.success is True
        commission = json.loads(created.output)

        armed = TriggerStore(base_dir=tmp_path).get(TRIGGER_PREFIX + commission['id'])
        assert armed is not None
        action = TriggerAction.resolve(armed.trigger.workflow)
        assert action.name == 'creative-commission' and action.provider is not None
        due = await action.provider.execute(action.config, ActionContext(event='trigger.fired', payload={'scheduled_for': 1730000000}))
        assert due.success is True
        run = json.loads(due.stdout)
        assert run['status'] == 'completed' and run['trigger'] == 'schedule'
        assert run['outputs'][0]['content_hash']

        use_ephemeral_secret()
        try:
            app = web.Application(middlewares=[token_auth_middleware()])
            app[STORE] = IngredientStore(tmp_path)
            app.router.add_get('/session', lambda request: web.json_response({'authenticated': True}))
            register(app)
            async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
                assert (await client.get('/api/capabilities/creative/commissions')).status in (401, 403)
                assert (await client.get('/session?token=' + generate_token('commission-owner'))).status == 200
                response = await client.get('/api/capabilities/creative/commissions')
                assert response.status == 200
                assert (await response.json())['items'][0]['id'] == commission['id']
        finally:
            use_persistent_secret()
    finally:
        registry.deregister('gideon-creative-commissions')
