import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, CookieJar, web
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.workspace.capabilities.workspace.projects import ProjectService
from gideon.workspace.capabilities.workspace.store import ConflictError
from test_workspace import repository


class Projects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prior = os.environ.get('GIDEON_HOME')
        os.environ['GIDEON_HOME'] = str(self.root)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.service = ProjectService(self.root / 'state', allowed_roots=[self.workspace])
        self.payload = {'name':'Actual project','parent':str(self.workspace),'directory':'new-app','template':'python-http','request_id':uuid4().hex}

    def tearDown(self):
        if self.prior is None:
            os.environ.pop('GIDEON_HOME',None)
        else:
            os.environ['GIDEON_HOME'] = self.prior
        self.temp.cleanup()

    def test_register_reuses_canonical_project_source_and_reload(self):
        repo = repository(self.workspace / 'existing')
        (repo / 'package.json').write_text(json.dumps({'scripts':{'dev':'vite --port 6025','build':'node build.js'}}))
        result = self.service.register({'name':'Existing','workspace':str(repo),'request_id':'register'})
        row = result['project']
        self.assertTrue(row['id'].startswith('p-'))
        self.assertEqual(HierarchyStore().get_project(row['id']).workspace_dir,str(repo))
        self.assertEqual(result['detection']['types'],['node'])
        self.assertEqual(result['detection']['ports'],[6025])
        self.assertTrue(result['detection']['has_git'])
        self.assertEqual(result['detection']['commands']['dev'],'vite --port 6025')
        reopened = ProjectService(self.root / 'state',allowed_roots=[self.workspace])
        self.assertEqual(reopened.get(row['id']),result)
        self.assertEqual(reopened.list(),[result])
        self.assertEqual(reopened.register({'name':'Existing','workspace':str(repo),'request_id':'register'}),result)
        self.assertEqual(len([p for p in HierarchyStore().list_projects() if p.workspace_dir==str(repo)]),1)

    def test_detection_never_executes_declared_commands(self):
        marker = self.workspace / 'executed'
        (self.workspace / 'package.json').write_text(json.dumps({'scripts':{'prepare':f'touch {marker}','start':'node main.js --port=65535'}}))
        (self.workspace / 'pyproject.toml').write_text('[project]\nname="example"\n')
        (self.workspace / 'Cargo.toml').write_text('[package]\nname="example"\n')
        (self.workspace / 'Package.swift').write_text('import PackageDescription\n')
        (self.workspace / 'App.xcodeproj').mkdir()
        detected = self.service.detect(str(self.workspace))
        self.assertFalse(marker.exists())
        self.assertEqual(detected['types'],['node','python','rust','swift','xcode'])
        self.assertEqual(detected['ports'],[65535])
        self.assertEqual(set(detected['source_files']),{'package.json','pyproject.toml','Cargo.toml','Package.swift','App.xcodeproj'})
        self.assertIn('touch',detected['commands']['prepare'])
        self.assertFalse(detected['directory_entries_truncated'])

    def test_malformed_oversized_and_symlink_metadata(self):
        metadata = self.workspace / 'package.json'
        metadata.write_text('not-json')
        with self.assertRaises(ValueError):
            self.service.detect(str(self.workspace))
        metadata.write_text(' ' * 262145)
        with self.assertRaises(ValueError):
            self.service.detect(str(self.workspace))
        metadata.unlink()
        foreign = self.root / 'foreign.json'
        foreign.write_text('{}')
        metadata.symlink_to(foreign)
        with self.assertRaises(ValueError):
            self.service.detect(str(self.workspace))
        metadata.unlink()
        metadata.write_text(json.dumps({'scripts':{'dev':42}}))
        with self.assertRaises(ValueError):
            self.service.detect(str(self.workspace))
        metadata.write_text(json.dumps({'scripts':[]}))
        with self.assertRaises(ValueError):
            self.service.detect(str(self.workspace))
        metadata.write_text('[]')
        with self.assertRaises(ValueError):
            self.service.detect(str(self.workspace))

    def test_directory_discovery_is_bounded(self):
        for index in range(260):
            (self.workspace / f'entry-{index}').mkdir()
        result = self.service.detect(str(self.workspace))
        self.assertTrue(result['directory_entries_truncated'])
        self.assertEqual(result['types'],[])
        self.assertEqual(result['commands'],{})

    def test_scaffold_is_runnable_and_receipt_is_durable(self):
        result = self.service.scaffold(self.payload)
        folder = Path(result['project']['workspace_dir'])
        self.assertTrue((folder / 'app.py').is_file())
        self.assertTrue((folder / 'pyproject.toml').is_file())
        self.assertEqual(result['detection']['types'],['python'])
        self.assertEqual(result['detection']['commands']['start'],'python3 app.py')
        self.assertEqual(self.service.scaffold(self.payload),result)
        with sqlite3.connect(self.service.db_path) as db:
            self.assertEqual(db.execute('SELECT project_id FROM operations').fetchone()[0],result['project']['id'])
        self.assertEqual(self.service.db_path.stat().st_mode & 0o777,0o600)
        self.assertEqual(HierarchyStore().get_project(result['project']['id']).name,'Actual project')

    def test_existing_directory_and_name_conflicts_preserve_files(self):
        target = self.workspace / 'new-app'
        target.mkdir()
        file = target / 'app.py'
        file.write_text('user content')
        with self.assertRaises(ConflictError):
            self.service.scaffold(self.payload)
        self.assertEqual(file.read_text(),'user content')
        elsewhere = self.workspace / 'elsewhere'
        elsewhere.mkdir()
        existing = HierarchyStore().create_project('Actual project',workspace_dir=str(elsewhere))
        with self.assertRaises(ConflictError):
            self.service.scaffold({**self.payload,'directory':'different','request_id':'name-conflict'})
        self.assertFalse((self.workspace / 'different').exists())
        self.assertEqual(HierarchyStore().get_project(existing.id).workspace_dir,str(elsewhere))

    def test_replay_conflict_does_not_change_template(self):
        result = self.service.scaffold(self.payload)
        with self.assertRaises(ConflictError):
            self.service.scaffold({**self.payload,'template':'node-http'})
        folder = Path(result['project']['workspace_dir'])
        self.assertFalse((folder / 'server.mjs').exists())
        self.assertEqual(len(self.service.list()),1)

    def test_concurrent_retry_has_single_canonical_project(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _:self.service.scaffold(self.payload),range(4)))
        self.assertEqual(len({r['project']['id'] for r in rows}),1)
        self.assertEqual(len(self.service.list()),1)
        self.assertEqual(len(list((self.workspace / 'new-app').glob('app.py'))),1)

    def test_pending_owned_operation_resumes_without_overwrite(self):
        result = self.service.scaffold(self.payload)
        with sqlite3.connect(self.service.db_path) as db:
            db.execute('UPDATE operations SET project_id=NULL')
        self.assertEqual(self.service.scaffold(self.payload),result)
        folder = Path(result['project']['workspace_dir'])
        (folder / 'app.py').write_text('deliberate edit')
        with sqlite3.connect(self.service.db_path) as db:
            db.execute('UPDATE operations SET project_id=NULL')
        with self.assertRaises(ConflictError):
            self.service.scaffold(self.payload)
        self.assertEqual((folder / 'app.py').read_text(),'deliberate edit')

    def test_workspace_and_template_validation(self):
        for patch in ({'directory':'../escape'},{'directory':'/absolute'},{'template':'untrusted-url'},{'name':''},{'env':'secret'}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):
                self.service.scaffold({**self.payload,**patch})
        with self.assertRaises(ValueError):
            self.service.detect(str(self.root))
        with self.assertRaises(ValueError):
            self.service.detect('relative')
        with self.assertRaises(FileNotFoundError):
            self.service.detect(str(self.workspace / 'missing'))
        link = self.workspace / 'outside'
        link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):
            self.service.detect(str(link))
        self.assertFalse((self.root / 'escape').exists())


class RunningTemplates(unittest.IsolatedAsyncioTestCase):
    async def test_generated_python_and_node_serve_real_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = os.environ.get('GIDEON_HOME')
            os.environ['GIDEON_HOME'] = directory
            try:
                service = ProjectService(root / 'state',allowed_roots=[root])
                catalog = service.templates()
                self.assertEqual({x['id'] for x in catalog},{'python-http','node-http'})
                for template in catalog:
                    self.assertTrue(template['available'],template['requires'])
                    result = service.scaffold({'name':template['name'],'parent':directory,'directory':template['id'],'template':template['id'],'request_id':template['id']})
                    with socket.socket() as reservation:
                        reservation.bind(('127.0.0.1',0))
                        port = reservation.getsockname()[1]
                    proc = await asyncio.create_subprocess_exec(template['requires'],template['entrypoint'],cwd=result['project']['workspace_dir'],env={**os.environ,'PORT':str(port)},stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                    try:
                        async with ClientSession() as client:
                            for attempt in range(100):
                                try:
                                    response = await client.get(f'http://127.0.0.1:{port}/health')
                                    break
                                except OSError:
                                    if attempt==99:
                                        raise
                                    await asyncio.sleep(.02)
                            self.assertEqual(response.status,200)
                            self.assertEqual(await response.json(),{'status':'ok'})
                            missing = await client.get(f'http://127.0.0.1:{port}/unknown')
                            self.assertEqual(missing.status,404)
                            self.assertIsNone(proc.returncode)
                    finally:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(),5)
            finally:
                if old is None:
                    os.environ.pop('GIDEON_HOME',None)
                else:
                    os.environ['GIDEON_HOME'] = old


class ProjectHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prior = os.environ.get('GIDEON_HOME')
        os.environ['GIDEON_HOME'] = str(self.root)
        self.repo = repository(self.root / 'existing')
        from gideon.interfaces.dashboard.token_auth import token_auth_middleware,generate_token,reset_secret_cache
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        reset_secret_cache()
        app = web.Application(middlewares=[token_auth_middleware()])
        register(app)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner,'127.0.0.1',0)
        await site.start()
        self.url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/workspace/projects'
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True))
        response = await self.client.get(self.url+'?token='+generate_token('project-owner'))
        self.assertEqual(response.status,200,await response.text())

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        if self.prior is None:
            os.environ.pop('GIDEON_HOME',None)
        else:
            os.environ['GIDEON_HOME'] = self.prior
        self.temp.cleanup()

    async def test_http_and_native_share_canonical_project(self):
        response = await self.client.post(self.url+'/detect',json={'workspace':str(self.repo)})
        self.assertEqual(response.status,200)
        self.assertTrue((await response.json())['has_git'])
        response = await self.client.post(self.url,json={'name':'HTTP project','workspace':str(self.repo),'request_id':'http'})
        self.assertEqual(response.status,200,await response.text())
        project = (await response.json())['project']
        from gideon.workspace.capabilities.workspace.tools import create_provider
        provider = create_provider()
        listed = await provider.invoke('workspace_projects',{})
        self.assertTrue(listed.success,listed.error)
        self.assertEqual(json.loads(listed.output)[0]['project']['id'],project['id'])
        templates = await provider.invoke('workspace_project_templates',{})
        self.assertTrue(templates.success)
        self.assertEqual(len(json.loads(templates.output)),2)
        created = await provider.invoke('workspace_project_scaffold',{'name':'Tool project','parent':str(self.root),'directory':'tool-app','template':'node-http','request_id':'tool'})
        self.assertTrue(created.success,created.error)
        result = json.loads(created.output)
        self.assertEqual(HierarchyStore().get_project(result['project']['id']).workspace_dir,str(self.root/'tool-app'))
        response = await self.client.get(self.url+'/'+result['project']['id'])
        self.assertEqual((await response.json())['detection']['types'],['node'])

    async def test_auth_and_no_overwrite_errors(self):
        async with ClientSession() as anonymous:
            response = await anonymous.post(self.url+'/scaffold',json={})
            self.assertIn(response.status,(401,403))
        response = await self.client.post(self.url+'/scaffold',json={'name':'No overwrite','parent':str(self.root),'directory':'existing','template':'python-http','request_id':'conflict'})
        self.assertEqual(response.status,409)
        self.assertEqual((self.repo/'note.txt').read_text(),'original\n')
        response = await self.client.post(self.url+'/detect',json={'workspace':'/etc'})
        self.assertEqual(response.status,400)
        response = await self.client.post(self.url+'/scaffold',json={'name':'Unsafe','parent':str(self.root),'directory':'../escape','template':'python-http','request_id':'unsafe'})
        self.assertEqual(response.status,400)
        response = await self.client.get(self.url+'/missing')
        self.assertEqual(response.status,404)


if __name__=='__main__':
    unittest.main()
