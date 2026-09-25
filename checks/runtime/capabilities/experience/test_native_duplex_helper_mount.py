import base64
import hashlib
from pathlib import Path

from aiohttp.test_utils import TestClient,TestServer

from gideon.workspace.capabilities.experience.native_duplex_helper import NativeDuplexExecutor
from gideon.workspace.capabilities.workspace.native_helper import create_app

TOKEN='mounted-duplex-token-'+('x'*32)
AUTH={'Authorization':'Bearer '+TOKEN}


async def test_duplex_routes_and_capability_are_absent_without_explicit_opt_in(tmp_path):
    app=create_app('paired-mac',TOKEN)
    async with TestClient(TestServer(app)) as client:
        identity=await client.get('/v1/identity',headers=AUTH)
        assert identity.status==200
        assert (await identity.json())['capabilities']==['terminal.read']
        assert (await client.post('/v1/voice/duplex/capture',headers=AUTH,json={'session_id':'one','operation_id':'one','capture_seconds':1})).status==404
        assert (await client.post('/v1/voice/duplex/play',headers=AUTH,json={})).status==404
    executor=NativeDuplexExecutor(root=tmp_path,platform='linux',xcrun=None)
    try:
        create_app('paired-mac',TOKEN,duplex_executor=executor)
        raise AssertionError('executor was accepted without duplex opt-in')
    except ValueError as error:
        assert 'explicit opt-in' in str(error)


async def test_enabled_helper_advertises_and_authenticates_fixed_routes(tmp_path):
    executor=NativeDuplexExecutor(root=tmp_path,platform='linux',xcrun=None)
    app=create_app('paired-mac',TOKEN,duplex_audio=True,duplex_executor=executor)
    async with TestClient(TestServer(app)) as client:
        denied=await client.get('/v1/identity')
        assert denied.status==401
        identity=await client.get('/v1/identity',headers=AUTH)
        assert identity.status==200
        assert await identity.json()=={'device_id':'paired-mac','protocol':1,'capabilities':['terminal.read','voice.duplex']}
        denied_capture=await client.post('/v1/voice/duplex/capture',json={'session_id':'session','operation_id':'capture-one','capture_seconds':1})
        assert denied_capture.status==401
        capture=await client.post('/v1/voice/duplex/capture',headers=AUTH,json={'session_id':'session','operation_id':'capture-one','capture_seconds':1})
        assert capture.status==503
        assert 'requires macOS' in (await capture.json())['error']
        shell=await client.post('/v1/voice/duplex/shell',headers=AUTH,json={'command':'touch forbidden'})
        assert shell.status==404
        assert not (tmp_path/'forbidden').exists()


async def test_enabled_playback_rejects_bad_digest_before_device_dispatch(tmp_path):
    executor=NativeDuplexExecutor(root=tmp_path,platform='darwin',xcrun='/usr/bin/xcrun')
    app=create_app('paired-mac',TOKEN,duplex_audio=True,duplex_executor=executor)
    audio=b'RIFF'+b'\x00'*4+b'WAVE'+b'\x00'*40
    body={'session_id':'session','operation_id':'play-one','mime':'audio/wav','audio':base64.b64encode(audio).decode(),'sha256':'0'*64}
    async with TestClient(TestServer(app)) as client:
        response=await client.post('/v1/voice/duplex/play',headers=AUTH,json=body)
        assert response.status==400
        assert 'digest' in (await response.json())['error']
        assert not any(executor.root.glob('*.wav'))
        assert hashlib.sha256(audio).hexdigest()!=body['sha256']


def test_swift_source_is_declared_as_wheel_package_data():
    project=Path(__file__).parents[4]/'pyproject.toml'
    source=project.read_text()
    assert '"gideon.workspace.capabilities.experience.assets" = ["*.swift"]' in source
    assert (project.parent/'runtime/gideon/workspace/capabilities/experience/assets/native_duplex.swift').is_file()
