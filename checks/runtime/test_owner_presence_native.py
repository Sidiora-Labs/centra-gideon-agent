"""Fresh owner admission through actual HTTP handlers and durable session records."""
import json
import time
from types import SimpleNamespace
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard import token_auth, session_store
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.owner_presence import presence_proof, PRESENCE_WINDOW_SECS
from gideon.security.auth import credentials
from gideon.security.auth.modes import AuthMode

PASSWORD = 'correct-horse-battery-staple'

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import gideon.core.config.loader as loader
    monkeypatch.setattr(loader, 'config_dir', lambda: tmp_path)
    monkeypatch.setattr(credentials, 'config_dir', lambda: tmp_path)
    monkeypatch.setattr(session_store, 'config_dir', lambda: tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'auth': {'login_enabled': True}}))
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth.reset_lockouts()
    yield
    token_auth.revoke_all_sessions()
    auth.reset_lockouts()

def app():
    a=web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
    a['port']=10000
    a['allowed_origins']={'http://localhost:10000'}
    a['auth_cfg']=SimpleNamespace(mode=AuthMode.LOCAL_TOKEN)
    a.router.add_post('/api/auth/confirm', auth.api_auth_confirm)
    a.router.add_post('/api/auth/password', auth.api_auth_set_password)
    a.router.add_post('/api/auth/enroll/start', auth.api_auth_enroll_start)
    return a

def headers(token):
    return {'Authorization':f'Bearer {token}','Origin':'http://localhost:10000'}

def age(token, seconds):
    records=session_store.load_session_records()
    records[token_auth.token_nonce(token)].minted_at=time.time()-seconds
    session_store.save_session_records(records)

@pytest.mark.asyncio
async def test_old_session_is_live_but_cannot_mint_and_confirmation_rotates_it():
    credentials.set_password('owner', PASSWORD)
    old=token_auth.generate_token('owner')
    age(old, PRESENCE_WINDOW_SECS+30)
    async with TestClient(TestServer(app())) as c:
        refused=await c.post('/api/auth/enroll/start',json={},headers=headers(old))
        assert refused.status==401
        body=await refused.json()
        assert body['error']['code']=='fresh_sign_in_required'
        assert body['error']['detail']['password'] is True
        assert 'X-Auth-Required' not in refused.headers
        assert token_auth.validate_token(old,use_session_exp=True)[0]
        wrong=await c.post('/api/auth/confirm',json={'password':'wrong'},headers=headers(old))
        assert wrong.status==401
        assert token_auth.validate_token(old,use_session_exp=True)[0]
        good=await c.post('/api/auth/confirm',json={'password':PASSWORD},headers=headers(old))
        assert good.status==200
        new=good.cookies['gideon_token_10000'].value
        assert not token_auth.validate_token(old,use_session_exp=True)[0]
        assert token_auth.validate_token(new,use_session_exp=True)[0]
        minted=await c.post('/api/auth/enroll/start',json={},headers=headers(new))
        assert minted.status==200

@pytest.mark.asyncio
async def test_password_change_requires_current_proof_even_recent_session():
    credentials.set_password('owner', PASSWORD)
    token=token_auth.generate_token('owner')
    async with TestClient(TestServer(app())) as c:
        body={'username':'owner','password':'new-password-long-enough'}
        missing=await c.post('/api/auth/password',json=body,headers=headers(token))
        assert missing.status==400
        assert credentials.verify_password('owner',PASSWORD)
        wrong=await c.post('/api/auth/password',json={**body,'current_password':'wrong'},headers=headers(token))
        assert wrong.status==401
        assert auth._FAILURES
        right=await c.post('/api/auth/password',json={**body,'current_password':PASSWORD},headers=headers(token))
        assert right.status==200
        assert credentials.verify_password('owner',body['password'])
        assert not auth._FAILURES

@pytest.mark.asyncio
async def test_enrolled_factor_is_not_replaced_by_session_freshness(monkeypatch):
    credentials.set_password('owner',PASSWORD)
    monkeypatch.setenv(credentials.TOTP_SECRET_KEY,'JBSWY3DPEHPK3PXP')
    token=token_auth.generate_token('owner')
    async with TestClient(TestServer(app())) as c:
        body={'password':'new-password-long-enough','current_password':PASSWORD}
        absent=await c.post('/api/auth/password',json=body,headers=headers(token))
        assert absent.status==401
        assert (await absent.json())['error']['code']=='auth_totp_required'
        assert not auth._FAILURES
        from gideon.security.auth import totp
        monkeypatch.setattr(totp,'verify_code',lambda secret,code: code=='123456')
        good=await c.post('/api/auth/password',json={**body,'totp':'123456'},headers=headers(token))
        assert good.status==200

@pytest.mark.asyncio
async def test_app_session_never_proves_owner_even_with_valid_password():
    credentials.set_password('owner',PASSWORD)
    token=token_auth.generate_token('owner',app='example')
    async with TestClient(TestServer(app())) as c:
        refused=await c.post('/api/auth/confirm',json={'password':PASSWORD},headers=headers(token))
        assert refused.status==403
        assert credentials.verify_password('owner',PASSWORD)

class Request(dict):
    headers={}
    remote='127.0.0.1'
    app={'auth_cfg':SimpleNamespace(mode=AuthMode.NONE)}

@pytest.mark.parametrize('record',[{'app':'example'},{'_session_work_proof':object()}])
def test_auth_none_does_not_promote_app_or_agent_work(record):
    assert presence_proof(Request(record))==''

def test_explicit_auth_none_is_deliberate_owner_policy():
    assert presence_proof(Request())=='auth_off'

@pytest.mark.parametrize('seconds',[PRESENCE_WINDOW_SECS+1,-60])
def test_only_persisted_recent_non_future_session_proves_owner(seconds):
    token=token_auth.generate_token('owner')
    age(token,seconds)
    r=Request(user='owner',session_nonce=token_auth.token_nonce(token))
    r.app={'auth_cfg':SimpleNamespace(mode=AuthMode.LOCAL_TOKEN)}
    assert presence_proof(r)==''

@pytest.mark.asyncio
@pytest.mark.parametrize('route,body',[
    ('/api/devices/pair/start',{}),
    ('/api/channels/slack/owner/pairing',{}),
    ('/api/external-access/clients',{'label':'new','surfaces':['mcp']}),
    ('/api/secrets',{'name':'GIDEON_AUTH_MODE','value':'none'}),
    ('/api/config/gideon',{'path':'auth.session_ttl','value':'90d','confirm':True}),
])
async def test_actual_sensitive_consumers_refuse_old_live_owner_before_write(route,body,monkeypatch):
    from gideon.interfaces.dashboard.handlers import devices, channel_owner, external_access, secrets, core
    monkeypatch.setattr(channel_owner,'_supports_owner_pairing',lambda provider:True)
    a=app()
    a.router.add_post('/api/devices/pair/start',devices.api_devices_pair_start)
    a.router.add_post('/api/channels/{provider}/owner/pairing',channel_owner.api_channel_owner_pair)
    a.router.add_post('/api/external-access/clients',external_access.api_external_access_client)
    a.router.add_post('/api/secrets',secrets.api_secrets_put)
    a.router.add_post('/api/config/gideon',core.api_gideon_config_patch)
    token=token_auth.generate_token('owner');age(token,PRESENCE_WINDOW_SECS+30)
    async with TestClient(TestServer(a)) as c:
        response=await c.post(route,json=body,headers=headers(token))
        assert response.status==401,await response.text()
        assert (await response.json())['error']['code']=='fresh_sign_in_required'
        assert token_auth.validate_token(token,use_session_exp=True)[0]

@pytest.mark.asyncio
async def test_auth_tightening_does_not_require_freshness():
    from gideon.interfaces.dashboard.handlers import core
    a=app();a.router.add_post('/api/config/gideon',core.api_gideon_config_patch)
    token=token_auth.generate_token('owner');age(token,PRESENCE_WINDOW_SECS+30)
    async with TestClient(TestServer(a)) as c:
        response=await c.post('/api/config/gideon',json={'path':'auth.session_ttl','value':'1h'},headers=headers(token))
        assert response.status==200,await response.text()

def test_ready_proof_is_server_recorded_and_loopback_only():
    token=token_auth.generate_token('owner',issuer='ready');age(token,3600)
    r=Request(user='owner',session_nonce=token_auth.token_nonce(token));r.app={'auth_cfg':SimpleNamespace(mode=AuthMode.LOCAL_TOKEN)}
    assert presence_proof(r)=='started_by'
    r.remote='192.0.2.1';assert presence_proof(r)==''

def test_an_old_desktop_label_is_not_ready_proof():
    token=token_auth.generate_token('owner',kind='desktop');age(token,3600)
    r=Request(user='owner',session_nonce=token_auth.token_nonce(token));r.app={'auth_cfg':SimpleNamespace(mode=AuthMode.LOCAL_TOKEN)}
    assert presence_proof(r)==''

@pytest.mark.asyncio
async def test_first_password_setup_needs_recent_presence_but_not_an_old_password():
    token=token_auth.generate_token('owner');age(token,3600)
    async with TestClient(TestServer(app())) as c:
        old=await c.post('/api/auth/password',json={'password':PASSWORD},headers=headers(token))
        assert old.status==401
        fresh=token_auth.generate_token('owner')
        done=await c.post('/api/auth/password',json={'password':PASSWORD},headers=headers(fresh))
        assert done.status==200
        assert credentials.verify_password('owner',PASSWORD)

@pytest.mark.asyncio
async def test_invalid_pasted_link_does_not_end_existing_cookie_session():
    credentials.set_password('owner',PASSWORD)
    token=token_auth.generate_token('owner')
    a=app();a.router.add_get('/api/auth/session',auth.api_auth_session)
    async with TestClient(TestServer(a)) as c:
        c.session.cookie_jar.update_cookies({'gideon_token_10000':token})
        failed=await c.get('/api/auth/session?token=invalid')
        assert failed.status in {401,403}
        assert 'gideon_token_10000' not in failed.cookies
        assert token_auth.validate_token(token,use_session_exp=True)[0]
        still=await c.get('/api/auth/session')
        assert still.status==200

@pytest.mark.asyncio
async def test_old_owner_can_revoke_integration_token_without_presence(monkeypatch):
    from gideon.interfaces.dashboard.handlers import external_access
    from gideon.integrations.inbound import clients
    removed=[]
    monkeypatch.setattr(clients,'revoke_client',lambda key: removed.append(key) or True)
    a=app();a.router.add_delete('/api/external-access/clients/{client_id}',external_access.api_external_access_client)
    token=token_auth.generate_token('owner');age(token,3600)
    async with TestClient(TestServer(a)) as c:
        response=await c.delete('/api/external-access/clients/example',headers=headers(token))
        assert response.status==200
        assert removed==['example']

@pytest.mark.asyncio
async def test_old_owner_can_cancel_channel_pairing_without_presence(monkeypatch):
    from gideon.interfaces.dashboard.handlers import channel_owner
    from gideon.integrations import channel_trust
    monkeypatch.setattr(channel_owner,'_supports_owner_pairing',lambda provider:True)
    monkeypatch.setattr(channel_trust,'cancel_owner_pairing',lambda provider:True)
    a=app();a.router.add_delete('/api/channels/{provider}/owner/pairing',channel_owner.api_channel_owner_pair_cancel)
    token=token_auth.generate_token('owner');age(token,3600)
    async with TestClient(TestServer(a)) as c:
        response=await c.delete('/api/channels/slack/owner/pairing',headers=headers(token))
        assert response.status==200

@pytest.mark.parametrize('key',['GIDEON_AUTH_MODE','GIDEON_BYPASS_LOCAL_NETWORKS','GIDEON_BIND_HOST','GIDEON_CORS_ORIGINS','GIDEON_LOGIN_USER','GIDEON_LOGIN_PASSWORD','GIDEON_TOTP_SECRET','GIDEON_OWNER_ID','GIDEON_OWNER_ID_SLACK','GIDEON_INBOUND_MCP_TOKEN'])
def test_actual_sign_in_environment_names_are_guarded(key):
    from gideon.security.secrets_vault import is_sign_in_key
    assert is_sign_in_key(key)

def test_unknown_principal_cannot_borrow_a_session_record():
    token=token_auth.generate_token('owner')
    r=Request(session_nonce=token_auth.token_nonce(token));r.app={'auth_cfg':SimpleNamespace(mode=AuthMode.LOCAL_TOKEN)}
    assert presence_proof(r)==''
