import asyncio
import hashlib
import json
import io
import wave
from pathlib import Path

import pytest
from aiohttp import web, FormData
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_capture import register
from gideon.workspace.capabilities.knowledge.capture import CaptureInbox
from gideon.workspace.uploads import content_intake as intake
from gideon.extensions.providers.use_cases import save_active_models, save_use_case_settings
from gideon.integrations.stt.registry import register_provider, unregister_provider
from gideon.integrations.stt.openai_provider import OpenAISttProvider

ROOT='/api/capabilities/knowledge/captures'

def recording():
    stream=io.BytesIO()
    with wave.open(stream,'wb') as clip:
        clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(8000)
        clip.writeframes(bytes(1600))
    return stream.getvalue()

def form(data, extra=False):
    body=FormData()
    body.add_field('audio',data,filename='recording.wav',content_type='audio/wav')
    if extra: body.add_field('unexpected','value')
    return body

def app_for(store):
    state=ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store=store
    app=web.Application(middlewares=[token_auth_middleware()])
    app['state']=state
    register(app)
    return app


@pytest.mark.asyncio
async def test_owner_http_exemption_and_external_service_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    store=KnowledgeStore(str(tmp_path/'knowledge.db'))
    inbox=CaptureInbox(store)
    try:
        with pytest.raises(intake.IntakeRefused) as exc:
            await inbox.create('external-capture','rm -rf /\n')
        assert exc.value.status == 422 and inbox.list()['total'] == 0
        original=await inbox.create('external-safe-capture','Actual captured words')
        before=inbox.get(original['id'])
        with pytest.raises(intake.IntakeRefused):
            await inbox.route(original['id'],dict(request_id='external-bad-route',revision=1,
                destination='note',title='Reviewed',content='rm -rf /\n'))
        assert inbox.get(original['id']) == before
        assert store.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0
        async with TestClient(TestServer(app_for(store))) as client:
            client.session.headers['Authorization']='Bearer '+generate_token('capture-owner')
            response=await client.post(ROOT,json={'request_id':'owner-explicit-capture','text':'rm -rf /\n'})
            assert response.status == 200, await response.text()
            assert (await response.json())['text'] == 'rm -rf /\n'
            response=await client.post(ROOT,json={'request_id':'invalid-owner-claim','text':'rm -rf /\n','owner':True})
            assert response.status == 400
    finally: store.close()


@pytest.mark.asyncio
async def test_actual_http_audio_refusal_extra_field_and_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    save_use_case_settings('stt',{'enabled':False})
    store=KnowledgeStore(str(tmp_path/'knowledge.db'))
    try:
        app=app_for(store)
        async with TestClient(TestServer(app)) as client:
            client.session.headers['Authorization']='Bearer '+generate_token('capture-owner')
            headers={'X-Capture-Request-ID':'http-audio-capture'}
            bad=await client.post(ROOT+'/audio',data=form(b'rm -rf /\n'),headers=headers)
            assert bad.status == 422 and (await bad.json())['error']['code'] == 'upload_content_refused'
            assert app['capability_capture_inbox'].list()['total'] == 0
            assert not list((tmp_path/'files').glob('*'))
            extra=await client.post(ROOT+'/audio',data=form(recording(),True),headers=headers)
            assert extra.status == 400
            assert not list((tmp_path/'files').glob('*'))
            accepted=await client.post(ROOT+'/audio',data=form(recording()),headers=headers)
            assert accepted.status == 200, await accepted.text()
            voice=await accepted.json()
            saved=store.get_item(voice['audio_item_id'])
            assert Path(saved['file_path']).read_bytes() == recording()
            response=await client.post(ROOT+'/'+voice['id']+'/transcribe')
            assert response.status == 200, await response.text()
            result=await response.json()
            assert result['status'] == 'transcription_unavailable' and result['transcript'] is None
            assert result['audio_sha256'] == hashlib.sha256(recording()).hexdigest()
    finally: store.close()


@pytest.mark.asyncio
async def test_real_openai_adapter_transcript_scan_and_owned_cancellation(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    staging=tmp_path/'staging'; staging.mkdir()
    monkeypatch.setattr(tempfile,'tempdir',str(staging))
    received=[]; answer={'text':'Accepted actual transport transcript'}
    entered=asyncio.Event(); release=asyncio.Event()
    async def speech(request):
        fields={}
        reader=await request.multipart()
        while field := await reader.next():
            fields[field.name]=bytes(await field.read())
        received.append(fields)
        if answer.get('hold'):
            entered.set(); await release.wait()
        return web.json_response({'text':answer['text']})
    transport=web.Application(); transport.router.add_post('/v1/audio/transcriptions',speech)
    store=KnowledgeStore(str(tmp_path/'knowledge.db'))
    inbox=CaptureInbox(store)
    try:
        async with TestServer(transport) as server:
            provider=OpenAISttProvider(provider_name='capture-local-stt',endpoint=str(server.make_url('/v1')),api_key='local-transport-test')
            home=tmp_path/'home'
            home.mkdir(exist_ok=True)
            (home/'config.json').write_text(json.dumps({'providers':[{'name':'capture-local-stt','type':'openai','options':{'endpoint':str(server.make_url('/v1')),'api_key':'local-transport-test'}}]}))
            register_provider(provider)
            save_active_models({'stt':['capture-local-stt:whisper-1']})
            save_use_case_settings('stt',{'enabled':True})
            voice=await inbox.save_audio('actual-asr-capture',recording(),'recording.wav','audio/wav')
            result=await inbox.transcribe(voice['id'])
            assert result['transcript'] == answer['text']
            assert received[-1]['file'] == recording()
            assert received[-1]['model'] == b'whisper-1'
            answer['text']='rm -rf /\n'
            unsafe=await inbox.save_audio('actual-asr-refusal',recording(),'recording.wav','audio/wav')
            with pytest.raises(intake.IntakeRefused) as refused:
                await inbox.transcribe(unsafe['id'])
            assert refused.value.status == 422
            stored=inbox.get(unsafe['id'])
            assert stored['transcript'] is None and stored['status'] == 'transcription_failed'
            assert Path(store.get_item(unsafe['audio_item_id'])['file_path']).read_bytes() == recording()
            answer.update(text='Joined provider result',hold=True)
            pending=await inbox.save_audio('actual-asr-cancel',recording(),'recording.wav','audio/wav')
            task=asyncio.create_task(inbox.transcribe(pending['id']))
            await asyncio.wait_for(entered.wait(),10)
            task.cancel(); await asyncio.sleep(.02)
            assert not task.done() and list(staging.iterdir())
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
            assert not list(staging.iterdir())
            assert inbox.get(pending['id'])['transcript'] is None
    finally:
        release.set(); unregister_provider('capture-local-stt'); store.close()
