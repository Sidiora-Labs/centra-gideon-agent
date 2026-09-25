import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from aiohttp import ClientSession, CookieJar, web
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.workspace.capabilities.workspace.git_ops import GitService
from gideon.workspace.capabilities.workspace.store import ConflictError
from test_workspace import repository


def git(path,*args):
    return subprocess.check_output(['git','-C',str(path),*args],stderr=subprocess.DEVNULL,text=True).strip()


class GitOperations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = os.environ.get('GIDEON_HOME')
        os.environ['GIDEON_HOME'] = str(self.root)
        self.repo = repository(self.root / 'repo')
        self.hierarchy = HierarchyStore()
        self.project = self.hierarchy.create_project(name='Git project',workspace_dir=str(self.repo))
        self.service = GitService(self.root / 'state',allowed_roots=[self.root],hierarchy=self.hierarchy)
        self.head = git(self.repo,'rev-parse','HEAD')

    async def asyncTearDown(self):
        if self.old is None:
            os.environ.pop('GIDEON_HOME',None)
        else:
            os.environ['GIDEON_HOME'] = self.old
        self.temp.cleanup()

    def payload(self,operation='create_branch',target='feature/local',request_id='operation'):
        return {'project_id':self.project.id,'operation':operation,'target':target,'expected_head':self.head,'request_id':request_id}

    async def test_canonical_inspect_create_switch_and_reload_receipt(self):
        initial = await self.service.inspect(self.project.id)
        self.assertEqual(initial['branch'],'main')
        self.assertEqual(initial['head'],self.head)
        self.assertFalse(initial['dirty'])
        self.assertEqual(initial['submodules'],[])
        receipt = await self.service.mutate(self.payload())
        self.assertEqual(receipt['status'],'succeeded')
        self.assertEqual(receipt['head_before'],self.head)
        self.assertEqual(receipt['head_after'],self.head)
        self.assertEqual(git(self.repo,'branch','--show-current'),'feature/local')
        reloaded = GitService(self.root/'state',allowed_roots=[self.root])
        self.assertEqual(await reloaded.mutate(self.payload()),receipt)
        self.assertEqual(reloaded.list(self.project.id),[receipt])
        switched = await reloaded.mutate(self.payload('switch_branch','main','switch'))
        self.assertEqual(switched['status'],'succeeded')
        self.assertEqual(git(self.repo,'branch','--show-current'),'main')
        self.assertEqual(len(reloaded.list(self.project.id)),2)
        self.assertEqual((self.repo/'note.txt').read_text(),'original\n')

    async def test_dirty_and_stale_refused_without_side_effect(self):
        (self.repo/'note.txt').write_text('edited\n')
        self.assertTrue((await self.service.inspect(self.project.id))['dirty'])
        with self.assertRaises(ConflictError):
            await self.service.mutate(self.payload())
        self.assertEqual(self.service.list(self.project.id),[])
        self.assertEqual(git(self.repo,'branch','--show-current'),'main')
        git(self.repo,'checkout','--','note.txt')
        stale = self.payload();stale['expected_head']='0'*40
        with self.assertRaises(ConflictError):
            await self.service.mutate(stale)
        self.assertEqual(git(self.repo,'branch','--list','feature/local'),'')
        self.assertEqual(self.service.list(self.project.id),[])

    async def test_request_conflict_and_failed_retry_are_honest(self):
        await self.service.mutate(self.payload())
        with self.assertRaises(ConflictError):
            await self.service.mutate(self.payload(target='other'))
        bad = self.payload('switch_branch','missing','failed')
        with self.assertRaises(ValueError):
            await self.service.mutate(bad)
        failed = self.service.list(self.project.id)[0]
        self.assertEqual(failed['status'],'failed')
        self.assertIsNone(failed['head_after'])
        with self.assertRaises(ConflictError):
            await self.service.mutate(bad)
        self.assertEqual(git(self.repo,'branch','--show-current'),'feature/local')
        self.assertEqual(len(self.service.list(self.project.id)),2)

    async def test_invalid_and_unknown_inputs(self):
        for changes in ({'operation':'push'},{'target':'--force'},{'target':'bad name'},{'expected_head':'main'},{'project_id':'missing'}):
            with self.subTest(changes=changes):
                with self.assertRaises((ValueError,FileNotFoundError)):
                    await self.service.mutate({**self.payload(),**changes})
        for payload in ({}, {**self.payload(),'argv':['push']}, {**self.payload(),'target':True}):
            with self.assertRaises(ValueError):
                await self.service.mutate(payload)
        with self.assertRaises(FileNotFoundError):
            await self.service.inspect('missing')
        with self.assertRaises(FileNotFoundError):
            self.service.list('missing')
        self.assertEqual(self.service.list(self.project.id),[])

    async def test_fsmonitor_hooks_and_inherited_git_environment_do_not_execute(self):
        marker = self.root/'executed'
        hook = self.root/'hook'
        hook.write_text('#!/bin/sh\ntouch '+str(marker)+'\n');hook.chmod(0o700)
        git(self.repo,'config','core.fsmonitor',str(hook))
        hooks=self.repo/'.git'/'hooks';(hooks/'post-checkout').write_text(hook.read_text());(hooks/'post-checkout').chmod(0o700)
        old = os.environ.get('GIT_DIR');os.environ['GIT_DIR']='/nonexistent-host-repo'
        try:
            await self.service.inspect(self.project.id)
            await self.service.mutate(self.payload())
        finally:
            if old is None:os.environ.pop('GIT_DIR',None)
            else:os.environ['GIT_DIR']=old
        self.assertFalse(marker.exists())
        self.assertEqual(git(self.repo,'branch','--show-current'),'feature/local')

    async def test_filters_config_includes_and_nested_projects_refused(self):
        git(self.repo,'config','filter.secret.smudge','touch /tmp/not-run')
        with self.assertRaisesRegex(ValueError,'filters'):
            await self.service.inspect(self.project.id)
        git(self.repo,'config','--remove-section','filter.secret')
        config=self.root/'included';config.write_text('[core]\n\tfsmonitor=false\n')
        git(self.repo,'config','include.path',str(config))
        with self.assertRaisesRegex(ValueError,'includes'):
            await self.service.inspect(self.project.id)
        git(self.repo,'config','--remove-section','include')
        nested=self.repo/'nested';nested.mkdir()
        project=self.hierarchy.create_project(name='Nested',workspace_dir=str(nested))
        with self.assertRaisesRegex(ValueError,'exact repository root'):
            await self.service.inspect(project.id)

    async def test_concurrent_operations_do_not_corrupt_history(self):
        results=await asyncio.gather(self.service.mutate(self.payload()),self.service.mutate(self.payload(target='competing',request_id='second')),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertEqual(sum(isinstance(x,ConflictError) for x in results),1)
        self.assertEqual(len(self.service.list(self.project.id)),1)
        self.assertEqual((await self.service.inspect(self.project.id))['head'],self.head)

    def submodule(self):
        remote=repository(self.root/'remote')
        pinned=git(remote,'rev-parse','HEAD')
        (remote/'note.txt').write_text('later\n');git(remote,'add','note.txt');git(remote,'commit','-m','later')
        later=git(remote,'rev-parse','HEAD')
        git(self.repo,'-c','protocol.file.allow=always','submodule','add',str(remote),'vendor/library')
        child=self.repo/'vendor/library'
        git(child,'checkout','--detach',pinned)
        git(self.repo,'add','.gitmodules','vendor/library');git(self.repo,'commit','-m','Pin library')
        self.head=git(self.repo,'rev-parse','HEAD')
        return child,pinned,later

    async def test_initialized_submodule_restores_pinned_cached_commit(self):
        child,pinned,later=self.submodule()
        git(child,'checkout','--detach',later)
        state=await self.service.inspect(self.project.id)
        self.assertFalse(state['dirty'])
        self.assertEqual(state['submodules'][0],{'path':'vendor/library','head':later,'expected_head':pinned,'initialized':True,'dirty':False})
        receipt=await self.service.mutate(self.payload('submodule_update','vendor/library'))
        self.assertEqual(receipt['status'],'succeeded')
        self.assertEqual(git(child,'rev-parse','HEAD'),pinned)
        self.assertEqual((child/'note.txt').read_text(),'original\n')
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),self.head)
        self.assertEqual(await self.service.mutate(self.payload('submodule_update','vendor/library')),receipt)

    async def test_dirty_unknown_and_uninitialized_submodules_refused(self):
        child,pinned,later=self.submodule()
        (child/'note.txt').write_text('customer changes\n')
        with self.assertRaises(ConflictError):
            await self.service.mutate(self.payload('submodule_update','vendor/library'))
        self.assertEqual((child/'note.txt').read_text(),'customer changes\n')
        git(child,'checkout','--','note.txt')
        with self.assertRaises(ValueError):
            await self.service.mutate(self.payload('submodule_update','../outside'))
        git(self.repo,'submodule','deinit','-f','--','vendor/library')
        self.assertFalse((await self.service.inspect(self.project.id))['submodules'][0]['initialized'])
        with self.assertRaisesRegex(ValueError,'initialized'):
            await self.service.mutate(self.payload('submodule_update','vendor/library'))
        self.assertEqual(self.service.list(self.project.id),[])
        self.assertFalse((child/'.git').exists())


class GitHttp(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = GitOperations.asyncSetUp
    asyncTearDown = GitOperations.asyncTearDown
    payload = GitOperations.payload

    async def test_authenticated_http_and_native_share_receipts(self):
        from gideon.interfaces.dashboard.token_auth import token_auth_middleware,generate_token,reset_secret_cache
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.workspace.capabilities.workspace.tools import create_provider
        reset_secret_cache()
        app=web.Application(middlewares=[token_auth_middleware()]);register(app)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        base=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/workspace/git'
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                response=await client.get(base+'/'+self.project.id+'?token='+generate_token('git-owner'))
                self.assertEqual(response.status,200,await response.text())
                self.assertEqual((await response.json())['head'],self.head)
                response=await client.post(base+'/operations',json=self.payload())
                self.assertEqual(response.status,200,await response.text())
                receipt=await response.json()
                provider=create_provider()
                result=await provider.invoke('workspace_git_history',{'project_id':self.project.id})
                self.assertTrue(result.success,result.error)
                self.assertEqual(json.loads(result.output),[receipt])
                definitions={t.name:t for t in await provider.list_tools()}
                self.assertTrue(definitions['workspace_git_mutate'].requires_approval)
                self.assertFalse(definitions['workspace_git_inspect'].requires_approval)
                result=await provider.invoke('workspace_git_mutate',self.payload('switch_branch','main','native'))
                self.assertTrue(result.success,result.error)
                self.assertEqual(git(self.repo,'branch','--show-current'),'main')
                response=await client.get(base+'/'+self.project.id+'/operations')
                self.assertEqual(len(await response.json()),2)
                response=await client.post(base+'/operations',json={**self.payload(),'expected_head':'0'*40,'request_id':'stale'})
                self.assertEqual(response.status,409)
            async with ClientSession() as anonymous:
                response=await anonymous.post(base+'/operations',json=self.payload())
                self.assertIn(response.status,(401,403))
        finally:
            await runner.cleanup()
