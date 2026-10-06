"""Argument credential ownership using the real native store and SDK transport."""
import hashlib
import json
import secrets
import sys
import pytest
from gideon.core.config import credentials
from gideon.extensions.providers import mcp_instances as instances
from gideon.integrations.mcp_argument_secrets import credential_values,sealed_arguments,sealed_address
from gideon.integrations.mcp_client import McpServerConn
from gideon.security import mcp_grants
from gideon.security.approval_answer import Principal,OWNER

@pytest.fixture
def home(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    return tmp_path/'home'

def candidate(name,spec):return {**spec,'name':name,'source':'mcp.json'}


def test_definite_slots_owner_storage_and_ambiguous_packages(home):
    value=secrets.token_hex(18)
    package='project-'+secrets.token_hex(18)
    spec={'command':'runner','args':['--api-key='+value,'--token',value,'-H','Authorization: Bearer '+value,package], 'url':''}
    stored=instances.store_server_credentials('one',spec)
    assert value not in json.dumps(stored)
    assert stored['args'][-1]==package
    assert len(credential_values(stored))==3
    for ref in credential_values(stored).values():assert instances.mcp_owner('one').owns(ref[len('{{secret:'):-2])
    resolved=instances.resolve_server_credentials('one',stored)
    assert resolved['args']==spec['args']
    assert sealed_arguments(stored['args'])==sealed_arguments(spec['args'])
    with pytest.raises(ValueError,match='another server'):
        instances.store_server_credentials('other',stored)
    assert instances.store_server_credentials('one',stored,previous=stored)==stored


def test_url_query_path_slots_missing_home_and_native_address_restrictions(home,monkeypatch,tmp_path):
    value='ghp_'+secrets.token_hex(20)
    spec={'url':f'https://localhost/mcp/{value}?token={value}&workspace=plain'}
    stored=instances.store_server_credentials('remote',spec)
    assert value not in stored['url'] and 'workspace=plain' in stored['url']
    assert instances.resolve_server_credentials('remote',stored)['url']==spec['url']
    assert sealed_address(stored['url'])==sealed_address(spec['url'])
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'other-home'))
    with pytest.raises(ValueError,match='credential unavailable') as error:
        instances.resolve_server_credentials('remote',stored)
    assert value not in str(error.value)
    for address in ('https://user:password@localhost/mcp','https://localhost/mcp#secret'):
        with pytest.raises(ValueError,match='userinfo and fragments'):
            instances.store_server_credentials('remote',{'url':address})


def test_canonical_plaintext_migration_carries_only_exact_prior_grant(home):
    value=secrets.token_hex(18)
    raw={'command':'runner','args':['--api-key',value], 'poolable':True}
    before=candidate('legacy',raw)
    # Recreate the prior native grant's exact original execution shape.
    legacy={**mcp_grants.definition(before),'args':raw['args'],'url':''}
    mcp_grants.BOOK.give(mcp_grants.key(before),json.dumps(legacy,ensure_ascii=False,sort_keys=True,separators=(',',':')),principal='owner:test')
    instances._save({'mcpServers':{'legacy':raw,'unapproved':raw}})
    loaded=instances._load()['mcpServers']
    assert value not in instances._mcp_json_path().read_text()
    assert mcp_grants.allowed(candidate('legacy',loaded['legacy']))
    assert not mcp_grants.allowed(candidate('unapproved',loaded['unapproved']))
    assert not mcp_grants.carry_over(before,candidate('legacy',{**loaded['legacy'],'args':['different-command-input']}))
    assert instances._load()['mcpServers']==loaded


def test_edit_retains_all_namespace_references_then_remove_purges(home):
    value=secrets.token_hex(18)
    stored=instances.store_server_credentials('edit',{'command':'runner','args':['--api-key',value],'env':{'API_TOKEN':value},'headers':{'Authorization':'Bearer '+value}})
    instances._save({'mcpServers':{'edit':stored}})
    refs=set(credential_values(stored).values())
    assert instances.update_instance('edit',enabled=False)
    current=instances._load()['mcpServers']['edit']
    assert set(credential_values(current).values())==refs
    assert instances.resolve_server_credentials('edit',current)['args'][-1]==value
    assert instances.delete_instance('edit')
    for ref in refs:assert not credentials.get_secret_value(ref[len('{{secret:'):-2])

@pytest.mark.asyncio
async def test_sdk_start_probe_dispatch_receives_only_resolved_argument(home,tmp_path):
    from test_mcp_abandoned_queue import SERVER
    from gideon.integrations.mcp_discovery import list_servers,probe_server
    value=secrets.token_hex(18)
    script=tmp_path/'server.py';log=tmp_path/'received'
    script.write_text('import hashlib,os,sys\nassert hashlib.sha256(sys.argv[-1].encode()).hexdigest()==os.environ["EXPECTED_ARG_HASH"]\n'+SERVER)
    raw={'command':sys.executable,'args':['-u',str(script),str(log),'--api-key',value],'env':{'EXPECTED_ARG_HASH':hashlib.sha256(value.encode()).hexdigest()},'poolable':True}
    stored=instances.store_server_credentials('argument',raw)
    instances._save({'mcpServers':{'argument':stored}})
    mcp_grants.give(candidate('argument',stored),Principal(OWNER,'test-owner'))
    server=next(s for s in list_servers() if s.name=='argument')
    assert value not in json.dumps(server.to_dict())
    probed=await probe_server(server)
    assert probed.status=='ok',probed.error
    conn=McpServerConn('argument',stored)
    try:
        assert await conn.call_tool('record',{'value':'received'})==(True,'received')
    finally:
        await conn.shutdown()
    assert log.read_text().splitlines()==['received']


def test_masked_edit_and_foreign_copy_actual_consumers(home,tmp_path):
    import shlex
    from gideon.interfaces.dashboard.handlers.mcp import _mcp_auth_values,_set_scope_entry
    from gideon.integrations.mcp_secret_refs import safe_display_args
    value=secrets.token_hex(18)
    stored=instances.store_server_credentials('copy',{'command':'runner','args':['--api-key',value]})
    instances._save({'mcpServers':{'copy':stored}})
    mcp_grants.give(candidate('copy',stored),Principal(OWNER,'test-owner'))
    masked=safe_display_args(stored['args'])
    assert value not in str(masked) and '{{secret:' not in str(masked)
    assert instances._restore_args(shlex.join(masked),stored['args'])==stored['args']
    assert _mcp_auth_values(stored)==credential_values(stored)
    destination=tmp_path/'approved-copy.json'
    assert _set_scope_entry(destination,'copy',enabled=True,spec=stored)=='added'
    assert json.loads(destination.read_text())['mcpServers']['copy']['args'][-1]==value

@pytest.mark.asyncio
async def test_short_credential_split_across_child_stderr_never_reaches_status(home,tmp_path):
    value='privatevalue'
    script=tmp_path/'failure.py'
    script.write_text('import sys,time\nvalue=sys.argv[-1]\nsys.stderr.write("startup failed: "+value[:6]);sys.stderr.flush();time.sleep(.05)\nsys.stderr.write(value[6:]+"\\n");sys.stderr.flush();sys.exit(4)\n')
    raw={'command':sys.executable,'args':['-u',str(script),'--api-key',value]}
    stored=instances.store_server_credentials('failure',raw)
    instances._save({'mcpServers':{'failure':stored}})
    mcp_grants.give(candidate('failure',stored),Principal(OWNER,'test-owner'))
    conn=McpServerConn('failure',stored)
    try:
        assert not await conn.ensure_started()
        assert value not in conn.error and value not in conn._failure.detail
        assert 'credential]' in conn._failure.detail
        assert conn._stdio._pending_stderr==b''
    finally:
        await conn.shutdown()

@pytest.mark.asyncio
async def test_real_http_audit_never_contains_credential_path_or_query(home,monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from gideon.security.net import client as net_client
    from gideon.integrations.mcp_client import _remote_http_client_factory
    value='pathkey'+secrets.token_hex(18)
    app=web.Application()
    async def answer(request):return web.Response(text='received')
    app.router.add_get('/'+value,answer)
    server=TestServer(app);await server.start_server()
    endpoint=str(server.make_url('/'+value+'?api_key='+value))
    records=[]
    monkeypatch.setattr(net_client,'_audit',lambda url,policy,**kwargs:records.append((url,kwargs)))
    try:
        async with _remote_http_client_factory(endpoint=endpoint) as client:
            response=await client.get(endpoint)
            assert response.status_code==200 and response.text=='received'
        assert records and all(value not in str(row) for row in records)
    finally:
        await server.close()
