import json
import sys
import textwrap

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.integrations import mcp_client, mcp_discovery
from gideon.integrations.tool_providers.registry import ConfiguredMcpToolProvider
from gideon.interfaces.dashboard.handlers.mcp_trust import api_mcp_server_read_only_trust, read_only_trust_of
from gideon.security import mcp_grants, mcp_read_only_trust as trust
from gideon.security.approval_answer import OWNER, Principal


def state(description='Reads one value', *, additional=False):
    tools = [{'name':'lookup', 'description':description, 'inputSchema':{'type':'object','properties':{}}, 'annotations':{'readOnlyHint':True}}]
    if additional:
        tools.append({'name':'new_tool','description':'New capability','inputSchema':{'type':'object','properties':{}},'annotations':{'readOnlyHint':True}})
    return tools


@pytest.fixture
async def endpoint(tmp_path, monkeypatch):
    home = tmp_path / 'home'; home.mkdir()
    monkeypatch.setenv('GIDEON_HOME', str(home))
    monkeypatch.setenv('HOME', str(home))
    inventory = tmp_path / 'inventory.json'; inventory.write_text(json.dumps(state()))
    calls = tmp_path / 'calls.txt'
    script = tmp_path / 'server.py'
    script.write_text(textwrap.dedent('''
        import asyncio, json, sys
        from pathlib import Path
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
        app = Server("local-review-fixture")
        @app.list_tools()
        async def tools():
            return [Tool(**item) for item in json.loads(Path(sys.argv[1]).read_text())]
        @app.call_tool()
        async def call(name, arguments):
            with Path(sys.argv[2]).open('a') as out:
                out.write(name + '\\n')
            return [TextContent(type='text', text='local value')]
        async def main():
            async with stdio_server() as (read, write):
                await app.run(read, write, app.create_initialization_options())
        asyncio.run(main())
    '''))
    spec = {'command':sys.executable,'args':[str(script),str(inventory),str(calls)]}
    (home / 'mcp.json').write_text(json.dumps({'mcpServers':{'local-review':spec}}))
    server = next(s for s in mcp_discovery.list_servers() if s.name == 'local-review')
    mcp_grants.give(server, Principal(OWNER, 'fixture-owner'))
    monkeypatch.setattr(mcp_client, '_registry', None)
    registry = mcp_client.get_mcp_client_registry()
    connection = registry.get('local-review')
    assert connection is not None
    tools = await connection.list_tools()
    assert len(tools) == 1
    from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
    app = web.Application(middlewares=[token_auth_middleware(port=0)])
    owner_token = generate_token("fixture-owner", kind="browser")

    from gideon.interfaces.dashboard.handlers.mcp import api_mcp_servers
    app.router.add_get('/servers', api_mcp_servers)
    app.router.add_post('/trust/{name}', api_mcp_server_read_only_trust)
    app.router.add_delete('/trust/{name}', api_mcp_server_read_only_trust)
    from gideon.interfaces.dashboard.handlers.tools import api_tool_invoke
    app.router.add_post('/invoke', api_tool_invoke)
    client = TestClient(TestServer(app), headers={"Authorization": f"Bearer {owner_token}"}); await client.start_server()
    yield client, registry, connection, inventory, calls, home
    await client.close()
    for connected in list(registry._conns.values()):
        await connected.shutdown()
    await connection.shutdown()


async def review_now():
    server = next(s for s in mcp_discovery.list_servers() if s.name == 'local-review')
    return read_only_trust_of(server)


def body(review):
    return {'tools':review['listed'],'configurationRevision':review['configurationRevision'],'catalogRevision':review['catalogRevision'],'confirm':True}


@pytest.mark.asyncio
async def test_real_sdk_review_binds_tools_and_blocks_changed_invocation(endpoint):
    client, registry, connection, inventory, calls, home = endpoint
    provider = ConfiguredMcpToolProvider('local-review')
    before = await provider.list_tools()
    assert before[0].risk_level.value == 'caution'
    assert before[0].requires_approval
    refused = await provider.invoke(before[0].name, {})
    assert not refused.success and refused.metadata['effect_state'] == 'not_started'
    assert not calls.exists()
    review = await review_now()
    from gideon.interfaces.dashboard.token_auth import generate_token
    app_token = generate_token('fixture-app', app='fixture-app', kind='cli')
    denied = await client.post('/trust/local-review', headers={'Authorization': f'Bearer {app_token}'}, json=body(review))
    assert denied.status == 403
    assert not trust.holds_trust('local-review')
    response = await client.post('/trust/local-review',json=body(review))
    assert response.status == 200, await response.text()
    definitions = await provider.list_tools()
    offered = definitions[0]
    assert offered.risk_level.value == 'safe'
    assert not offered.requires_approval
    result = await provider.invoke(offered.name, {}, expected_definition=offered.mcp_definition_digest, expected_configuration=offered.mcp_configuration_revision, expected_read_authority=True)
    assert result.success
    assert calls.read_text().splitlines() == ['lookup']
    inventory.write_text(json.dumps(state('Now changes data', additional=True)))
    result = await provider.invoke(offered.name, {}, expected_definition=offered.mcp_definition_digest, expected_configuration=offered.mcp_configuration_revision, expected_read_authority=True)
    assert not result.success
    assert result.metadata['effect_state'] == 'not_started'
    assert calls.read_text().splitlines() == ['lookup']
    fresh = await provider.list_tools()
    assert all(tool.risk_level.value == 'caution' and tool.requires_approval for tool in fresh)
    changed = await review_now()
    assert changed['added'] == ['new_tool']
    assert changed['changed'][0]['parts'] == ['description']
    old = await client.post('/trust/local-review',json=body(review))
    assert old.status == 409
    assert not trust.believes('local-review', (await registry.get('local-review').list_tools())[0])
    current = await client.post('/trust/local-review',json=body(changed))
    assert current.status == 200, await current.text()
    assert all(not tool.requires_approval for tool in await provider.list_tools())
    removed = await client.delete('/trust/local-review')
    assert removed.status == 200
    assert all(tool.requires_approval for tool in await provider.list_tools())
    grant = home / 'grants/mcp_read_only.json'
    assert grant.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_config_change_and_damaged_record_never_believe_labels(endpoint):
    client, registry, connection, inventory, calls, home = endpoint
    review = await review_now()
    assert (await client.post('/trust/local-review', json=body(review))).status == 200
    tool = (await registry.get('local-review').list_tools())[0]
    assert trust.believes('local-review',tool)
    grant = home / 'grants/mcp_read_only.json'
    grant.write_text('{')
    assert not trust.believes('local-review',tool)
    response = await client.post('/trust/local-review',json=body(await review_now()))
    assert response.status == 409
    assert grant.read_text() == '{'
    config = json.loads((home / 'mcp.json').read_text())
    config['mcpServers']['local-review']['args'].append('changed-configuration')
    (home / 'mcp.json').write_text(json.dumps(config))
    assert not trust.believes('local-review',tool)
    stale = await client.post('/trust/local-review',json=body(review))
    assert stale.status == 409
    assert not calls.exists()


@pytest.mark.asyncio
async def test_untrusted_description_change_notifies_once(endpoint):
    client, registry, connection, inventory, calls, home = endpoint
    notices = []
    def notice(server, tools):
        notices.append((server, tools))
    trust.subscribe(notice)
    try:
        inventory.write_text(json.dumps(state('Changed descriptive text')))
        await connection.list_tools()
        await connection.list_tools()
        assert notices == [('local-review', ('lookup',))]
    finally:
        trust.unsubscribe(notice)


@pytest.mark.asyncio
async def test_native_try_it_uses_canonical_offered_stamp(endpoint, monkeypatch):
    client, registry, connection, inventory, calls, home = endpoint
    from gideon.integrations.tool_providers import registry as providers
    provider = next(item for item in providers.list_providers() if isinstance(item, ConfiguredMcpToolProvider) and item.server == 'local-review')
    monkeypatch.setattr(providers, 'list_providers', lambda: [provider])
    response = await client.post('/invoke', json={'tool':'mcp/local-review/lookup','arguments':{}})
    assert response.status == 200, await response.text()
    assert (await response.json())['ok'] is True
    assert calls.read_text().splitlines() == ['lookup']


@pytest.mark.asyncio
async def test_display_masked_inventory_review_uses_raw_sdk_digest(endpoint, monkeypatch):
    client, registry, connection, inventory, calls, home = endpoint
    from gideon.interfaces.dashboard.handlers import mcp as handler
    from gideon.security.security import redact_for_display
    import time
    secret = "sk-" + "a" * 48
    description = "Local credential example " + secret
    assert secret not in redact_for_display(description)
    inventory.write_text(json.dumps(state(description)))
    await connection.list_tools()
    server = next(s for s in mcp_discovery.list_servers() if s.name == 'local-review')
    # Populate the dashboard's existing cache with the real SDK-discovered inventory.
    monkeypatch.setattr(handler, '_mcp_probe_cache', [server.to_dict()])
    monkeypatch.setattr(handler, '_mcp_probe_ts', time.time())
    monkeypatch.setattr(handler, '_mcp_probe_in_progress', False)
    response = await client.get('/servers')
    assert response.status == 200
    displayed = await response.text()
    assert secret not in displayed
    row = next(item for item in json.loads(displayed) if item['name'] == 'local-review')
    review = row['readOnlyTrust']
    raw = (await connection.list_tools())[0]
    assert review['listed']['lookup'] == trust.digest(raw)
    assert review['listed']['lookup'] != trust.digest(row['tools'][0])
    allowed = await client.post('/trust/local-review', json=body(review))
    assert allowed.status == 200, await allowed.text()
    provider = ConfiguredMcpToolProvider('local-review')
    offered = (await provider.list_tools())[0]
    assert not offered.requires_approval
    inventory.write_text(json.dumps(state("Local credential example sk-" + "b" * 48)))
    stale = await client.post('/trust/local-review', json=body(review))
    assert stale.status == 409
    refused = await provider.invoke(offered.name, {}, expected_definition=offered.mcp_definition_digest, expected_configuration=offered.mcp_configuration_revision, expected_read_authority=True)
    assert not refused.success and refused.metadata['effect_state'] == 'not_started'
    assert not calls.exists()
