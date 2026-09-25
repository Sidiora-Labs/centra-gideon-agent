import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from PIL import Image
from aiohttp import ClientSession,CookieJar,WSMsgType,web
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.integrations.llm.registry import ProviderEntry,get_default_registry
from gideon.workspace.artifacts.registry import register_provider,get_provider
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.workspace.provider_terminal import profiles,prepare,upload,validate_image,cleanup
from test_workspace import repository


CODEX=shutil.which('codex') or '/root/.devin-server/extensions/openai.chatgpt-26.908.40401-linux-x64/bin/linux-x86_64/codex'


def png():
    stream=io.BytesIO();Image.new('RGB',(12,9),'orange').save(stream,format='PNG')
    return stream.getvalue()


class ProviderTerminal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.saved=dict(os.environ)
        for key in list(os.environ):
            if any(term in key.upper() for term in ('TOKEN','KEY','SECRET','CODEX','OPENAI')):os.environ.pop(key,None)
        os.environ.update(GIDEON_HOME=str(self.root),HOME=str(self.root),CODEX_HOME=str(self.root/'codex'))
        (self.root/'codex').mkdir(mode=0o700)
        self.repo=repository(self.root/'repo')
        self.artifacts=get_provider();register_provider(NativeArtifactProvider(self.root/'artifacts'))
        self.registry=get_default_registry()
        self.registry.register_entry(ProviderEntry(name='workspace-codex-test',type='acp_agent',model='',options={'dialect':'codex','requires_executable':{'path':CODEX}}))
        (self.root/'config.json').write_text(json.dumps({'dashboard':{'terminal':{'enabled':True,'persist':False,'cwd':str(self.repo)}}}))
        from gideon.interfaces.dashboard.handlers import terminal
        terminal._enabled_cache[:]=[True,0]
        self.terminal=terminal
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import token_auth_middleware,generate_token,reset_secret_cache
        reset_secret_cache()
        app=web.Application(middlewares=[token_auth_middleware()]);register(app)
        self.state=ConsoleState(ConversationDirectory(AppConfig()),time.monotonic())
        app['state']=self.state
        app.router.add_post('/api/terminal/sessions',terminal.api_terminal_create)
        app.router.add_delete('/api/terminal/sessions/{session_id}',terminal.api_terminal_delete)
        app.router.add_get('/api/ws/terminal/{session_id}',terminal.api_terminal_ws)
        self.runner=web.AppRunner(app);await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        self.client=ClientSession(cookie_jar=CookieJar(unsafe=True))
        await self.client.get(self.url+'/api/capabilities/workspace?token='+generate_token('provider-owner'))

    async def asyncTearDown(self):
        for session in list(self.state._terminal_sessions.values()):
            if session:await self.terminal._kill_session(session)
        self.terminal._pending_provider_argv.clear();self.terminal._pending_cwd.clear()
        await self.client.close();await self.runner.cleanup()
        self.registry.unregister_entry('workspace-codex-test')
        self.registry.unregister_entry('unsupported-test')
        register_provider(self.artifacts)
        os.environ.clear();os.environ.update(self.saved)
        self.temp.cleanup()

    async def test_profiles_native_tool_and_real_executable(self):
        self.assertTrue(Path(CODEX).is_file())
        row=next(p for p in profiles() if p['id']=='workspace-codex-test')
        self.assertTrue(row['available']);self.assertTrue(row['image_supported'])
        self.assertNotIn('path',row)
        self.registry.register_entry(ProviderEntry(name='unsupported-test',type='acp_agent',model='',options={'dialect':'unknown','requires_executable':{'path':CODEX}}))
        row=next(p for p in profiles() if p['id']=='unsupported-test')
        self.assertFalse(row['available'])
        with self.assertRaises(ValueError):prepare('a'*12,'unsupported-test')
        with self.assertRaises(ValueError):prepare('a'*12,'not-configured')
        from gideon.workspace.capabilities.workspace.tools import create_provider
        result=await create_provider().invoke('workspace_provider_terminal_profiles',{})
        self.assertTrue(result.success)
        self.assertIn('workspace-codex-test',result.output)

    async def test_canonical_artifact_bytes_and_session_owned_copy(self):
        data=png();record=upload(data,'image-owner')
        self.assertEqual(record['mime'],'image/png')
        self.assertEqual(record['bytes'],len(data))
        image={'artifact_id':record['artifact_id'],'version':record['version']}
        argv=prepare('a'*12,'workspace-codex-test',image)
        self.assertEqual(argv[:2],[str(Path(CODEX).resolve()),'--image'])
        path=Path(argv[2]);self.assertEqual(path.read_bytes(),data)
        self.assertTrue(path.is_relative_to(self.root))
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(get_provider().raw_bytes(record['artifact_id'])[0],data)
        other=prepare('b'*12,'workspace-codex-test',image)
        self.assertNotEqual(other[2],argv[2])
        cleanup('a'*12)
        self.assertFalse(path.exists());self.assertTrue(Path(other[2]).exists())
        self.assertIsNotNone(get_provider().get(record['artifact_id']))
        cleanup('b'*12)
        with self.assertRaises(ValueError):prepare('../escape','workspace-codex-test',image)

    async def test_decode_and_version_validation(self):
        for data in (b'',b'not-an-image',b'<svg/>',b'a'*8388609):
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):validate_image(data)
        for image in ({},{'artifact_id':'x','version':0},{'artifact_id':'x','version':True},{'artifact_id':'x','version':1,'path':'/etc/passwd'}):
            with self.subTest(image=image):
                with self.assertRaises(ValueError):prepare('a'*12,'workspace-codex-test',image)
        with self.assertRaises(FileNotFoundError):prepare('a'*12,'workspace-codex-test',{'artifact_id':'missing','version':1})
        response=await self.client.post(self.url+'/api/capabilities/workspace/provider-terminals/images',data=b'not an image')
        self.assertEqual(response.status,400)
        response=await self.client.post(self.url+'/api/terminal/sessions',json={'image':{'artifact_id':'x','version':1}})
        self.assertEqual(response.status,400)

    async def test_pending_session_cancel_and_capacity(self):
        sessions=[]
        for _ in range(3):
            response=await self.client.post(self.url+'/api/terminal/sessions',json={'provider_id':'workspace-codex-test','cwd':str(self.repo)})
            self.assertEqual(response.status,200)
            sessions.append((await response.json())['session_id'])
        response=await self.client.post(self.url+'/api/terminal/sessions',json={'provider_id':'workspace-codex-test'})
        self.assertEqual(response.status,429)
        response=await self.client.delete(self.url+'/api/terminal/sessions/'+sessions[0])
        self.assertEqual(response.status,200)
        self.assertNotIn(sessions[0],self.terminal._pending_provider_argv)
        response=await self.client.post(self.url+'/api/terminal/sessions',json={'provider_id':'workspace-codex-test'})
        self.assertEqual(response.status,200)

    async def test_real_cli_receives_image_in_existing_websocket_pty(self):
        response=await self.client.post(self.url+'/api/capabilities/workspace/provider-terminals/images',data=png())
        self.assertEqual(response.status,200);image=await response.json()
        response=await self.client.post(self.url+'/api/terminal/sessions',json={'provider_id':'workspace-codex-test','cwd':str(self.repo),'image':{key:image[key] for key in ('artifact_id','version')}})
        self.assertEqual(response.status,200);identity=(await response.json())['session_id']
        ws=await self.client.ws_connect(self.url+'/api/ws/terminal/'+identity)
        try:
            output=b''
            for _ in range(40):
                message=await asyncio.wait_for(ws.receive(),10)
                if message.type==WSMsgType.BINARY:
                    output+=message.data
                    if b'\x1b[6n' in message.data:await ws.send_bytes(b'\x1b[1;1R')
                    if any(marker in output for marker in (b'Welcome to',b'Sign in',b'sign in',b'ChatGPT')):break
                elif message.type in (WSMsgType.CLOSED,WSMsgType.ERROR):break
            self.assertTrue(any(marker in output for marker in (b'Welcome to',b'Sign in',b'sign in',b'ChatGPT')),output[-3000:])
            self.assertNotIn(b'Error finding codex home',output)
            await asyncio.sleep(.3)
            session=self.state._terminal_sessions[identity]
            self.assertEqual(session.cwd,str(self.repo))
            self.assertIsNone(session.proc.returncode)
            self.assertNotIn(identity,self.terminal._pending_provider_argv)
            command=Path(f'/proc/{session.proc.pid}/cmdline').read_bytes()
            self.assertIn(b'--image',command)
            self.assertIn(identity.encode(),command)
            image_path=self.root/'capabilities/workspace/terminal-images'/identity/'image.png'
            self.assertEqual(image_path.read_bytes(),png())
            response=await self.client.delete(self.url+'/api/terminal/sessions/'+identity)
            self.assertEqual(response.status,200)
            self.assertIsNotNone(session.proc.returncode)
            self.assertFalse(image_path.exists())
            self.assertIsNotNone(get_provider().get(image['artifact_id']))
        finally:await ws.close()
