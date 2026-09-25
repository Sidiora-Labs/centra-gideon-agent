import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, CookieJar, web
from gideon.workspace.capabilities.workspace.processes import ProcessRegistry, close_registry, get_registry
from gideon.workspace.capabilities.workspace.store import ConflictError, SnapshotStore
from test_workspace import repository


async def settled(registry, process_id):
    for _ in range(200):
        record = registry.get(process_id)
        if record['status'] not in ('running', 'starting'):
            return record
        await asyncio.sleep(.02)
    raise AssertionError('Real process did not reach a terminal state')


async def output(registry, process_id, expected):
    for _ in range(200):
        text = registry.logs(process_id)['text']
        if expected in text:
            return text
        await asyncio.sleep(.02)
    raise AssertionError(f'Expected actual output missing: {expected}')


class Processes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'project'
        self.repo.mkdir()
        self.registry = ProcessRegistry(self.root / 'state', allowed_roots=[self.root])
        self.payload = {'project_id':'actual-app', 'workspace':str(self.repo), 'command':'printf ready; exec sleep 60', 'request_id':uuid4().hex}

    async def asyncTearDown(self):
        await self.registry.close()
        self.temp.cleanup()

    async def test_launch_logs_owned_stop_and_reload(self):
        row = await self.registry.start(self.payload)
        self.assertEqual(row['status'], 'running')
        self.assertEqual(row['project_id'], 'actual-app')
        self.assertEqual(row['workspace'], str(self.repo))
        self.assertIsNone(row['exit_code'])
        self.assertIsNotNone(row['started_at'])
        self.assertIsNone(row['ended_at'])
        self.assertNotIn('pid', row)
        self.assertNotIn('env', row)
        self.assertIn('ready', await output(self.registry, row['id'], 'ready'))
        handle = self.registry.handles[row['id']]
        self.assertIsNone(handle.returncode)
        self.assertEqual(self.registry.get(row['id']), row)
        stopped = await self.registry.stop(row['id'], row['revision'])
        self.assertEqual(stopped['status'], 'stopped')
        self.assertIsNotNone(stopped['exit_code'])
        self.assertIsNotNone(stopped['ended_at'])
        self.assertGreater(stopped['revision'], row['revision'])
        self.assertIsNotNone(handle.returncode)
        self.assertNotIn(row['id'], self.registry.handles)
        self.assertIn('ready', self.registry.logs(row['id'])['text'])
        await self.registry.close()
        self.registry = ProcessRegistry(self.root / 'state', allowed_roots=[self.root])
        self.assertEqual(self.registry.get(row['id']), stopped)
        self.assertEqual(self.registry.list(), [stopped])

    async def test_natural_exit_and_nonzero_exit(self):
        good = await self.registry.start({**self.payload, 'command':'printf completed'})
        result = await settled(self.registry, good['id'])
        self.assertEqual(result['status'], 'exited')
        self.assertEqual(result['exit_code'], 0)
        self.assertIsNotNone(result['ended_at'])
        self.assertEqual(self.registry.logs(good['id'])['text'], 'completed')
        bad = await self.registry.start({**self.payload, 'request_id':'bad-exit', 'command':'printf failure >&2; exit 7'})
        failed = await settled(self.registry, bad['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['exit_code'], 7)
        self.assertEqual(self.registry.logs(bad['id'])['text'], 'failure')
        self.assertEqual(await self.registry.stop(bad['id'], failed['revision']), failed)

    async def test_start_retry_and_conflicting_request_id(self):
        one, two = await asyncio.gather(self.registry.start(self.payload), self.registry.start(self.payload))
        self.assertEqual(one['id'], two['id'])
        self.assertEqual(len(self.registry.handles), 1)
        self.assertEqual(len(self.registry.list()), 1)
        with self.assertRaises(ConflictError):
            await self.registry.start({**self.payload, 'command':'printf different'})
        self.assertEqual(self.registry.get(one['id'])['command'], self.payload['command'])
        self.assertEqual(await self.registry.start(self.payload), one)

    async def test_stale_stop_cannot_signal_process(self):
        row = await self.registry.start(self.payload)
        handle = self.registry.handles[row['id']]
        with self.assertRaises(ConflictError):
            await self.registry.stop(row['id'], row['revision'] - 1)
        self.assertIsNone(handle.returncode)
        self.assertEqual(self.registry.get(row['id'])['status'], 'running')
        with self.assertRaises(ConflictError):
            await self.registry.stop(row['id'], True)
        self.assertIsNone(handle.returncode)
        with self.assertRaises(FileNotFoundError):
            await self.registry.stop(str(handle.pid), row['revision'])
        self.assertIsNone(handle.returncode)

    async def test_command_screen_and_strict_launch_shape(self):
        invalid = [{'command':'cat ~/.ssh/id_rsa'}, {'env':{'TOKEN':'secret'}}, {'pid':1}, {'workspace':'relative'}, {'request_id':''}, {'command':'\x00'}, {'command':7}]
        for patch in invalid:
            with self.subTest(patch=patch), self.assertRaises((ValueError, PermissionError)):
                await self.registry.start({**self.payload, **patch})
        self.assertEqual(self.registry.list(), [])
        self.assertEqual(self.registry.handles, {})

    async def test_workspace_escape_missing_and_symlink(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other)
            with self.assertRaises(ValueError):
                await self.registry.start({**self.payload, 'workspace':str(outside)})
            link = self.root / 'escape'
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                await self.registry.start({**self.payload, 'workspace':str(link)})
        with self.assertRaises(FileNotFoundError):
            await self.registry.start({**self.payload, 'workspace':str(self.root / 'missing')})
        self.assertEqual(self.registry.list(), [])

    async def test_environment_and_real_working_directory(self):
        old = os.environ.get('OPENAI_API_KEY')
        os.environ['OPENAI_API_KEY'] = 'workspace-private-key'
        try:
            command = 'pwd; if [ -n "$OPENAI_API_KEY" ]; then printf leaked; else printf filtered; fi'
            row = await self.registry.start({**self.payload, 'command':command})
            await settled(self.registry, row['id'])
        finally:
            if old is None:
                os.environ.pop('OPENAI_API_KEY')
            else:
                os.environ['OPENAI_API_KEY'] = old
        text = self.registry.logs(row['id'])['text']
        self.assertIn(str(self.repo), text)
        self.assertIn('filtered', text)
        self.assertNotIn('leaked', text)
        self.assertNotIn('workspace-private-key', text)

    async def test_tail_bound_and_log_validation(self):
        command = "printf '%070000d' 0; printf tail-end"
        row = await self.registry.start({**self.payload, 'command':command})
        await settled(self.registry, row['id'])
        text = self.registry.logs(row['id'])['text']
        self.assertEqual(len(text), 65536)
        self.assertTrue(text.endswith('tail-end'))
        self.assertEqual(self.registry.logs(row['id'], limit=8)['text'], 'tail-end')
        for size in (0, 65537, True, '3'):
            with self.assertRaises(ValueError):
                self.registry.logs(row['id'], limit=size)
        with self.assertRaises(FileNotFoundError):
            self.registry.logs('unregistered')

    async def test_exclusive_home_and_separate_home(self):
        with self.assertRaises(ConflictError):
            ProcessRegistry(self.root / 'state', allowed_roots=[self.root])
        other = ProcessRegistry(self.root / 'other', allowed_roots=[self.root])
        try:
            row = await self.registry.start(self.payload)
            self.assertEqual(other.list(), [])
            with self.assertRaises(FileNotFoundError):
                other.get(row['id'])
            with self.assertRaises(FileNotFoundError):
                await other.stop(row['id'], row['revision'])
            self.assertIsNone(self.registry.handles[row['id']].returncode)
        finally:
            await other.close()
        self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)

    async def test_close_terminates_owned_process_group(self):
        row = await self.registry.start({**self.payload, 'command':'sleep 60 & child=$!; printf "%s" "$child"; wait'})
        for _ in range(100):
            text = self.registry.logs(row['id'])['text']
            if text.strip().isdigit():
                break
            await asyncio.sleep(.02)
        self.assertTrue(text.strip().isdigit())
        child = int(text.strip())
        handle = self.registry.handles[row['id']]
        await self.registry.close()
        self.assertIsNotNone(handle.returncode)
        self.assertEqual(self.registry.get(row['id'])['status'], 'stopped')
        stat = Path(f'/proc/{child}/stat')
        self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z')
        await self.registry.close()
        with self.assertRaises(ConflictError):
            await self.registry.start({**self.payload, 'request_id':'after-close'})

    async def test_real_registry_crash_preserves_unowned_process(self):
        worker = Path(__file__).with_name('process_worker.py')
        proc = await asyncio.create_subprocess_exec(sys.executable, str(worker), str(self.root / 'crash'), str(self.repo), stdout=asyncio.subprocess.PIPE)
        data = json.loads(await asyncio.wait_for(proc.stdout.readline(), 15))
        try:
            proc.kill()
            await proc.wait()
            recovered = ProcessRegistry(self.root / 'crash', allowed_roots=[self.root])
            try:
                row = recovered.get(data['id'])
                self.assertEqual(row['status'], 'interrupted')
                self.assertIsNone(row['exit_code'])
                self.assertIsNotNone(row['ended_at'])
                os.kill(data['pid'], 0)
                self.assertEqual(await recovered.stop(row['id'], row['revision']), row)
                os.kill(data['pid'], 0)
                self.assertEqual(recovered.handles, {})
            finally:
                await recovered.close()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            try:
                os.killpg(data['pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def test_history_pagination(self):
        ids = []
        for index in range(3):
            row = await self.registry.start({**self.payload, 'command':'true', 'request_id':f'page-{index}'})
            await settled(self.registry, row['id'])
            ids.append(row['id'])
        self.assertEqual([x['id'] for x in self.registry.list(limit=2)], list(reversed(ids))[:2])
        self.assertEqual([x['id'] for x in self.registry.list(offset=2)], [ids[0]])
        self.assertEqual(self.registry.list(offset=3), [])
        for options in ({'offset':-1}, {'limit':101}, {'limit':0}, {'offset':False}):
            with self.assertRaises(ValueError):
                self.registry.list(**options)


class ProcessHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prior = os.environ.get('GIDEON_HOME')
        os.environ['GIDEON_HOME'] = str(self.root)
        self.repo = repository(self.root / 'repo')
        from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, reset_secret_cache
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        reset_secret_cache()
        self.app = web.Application(middlewares=[token_auth_middleware()])
        register(self.app)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/workspace/processes'
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True))
        reply = await self.client.get(self.url + '?token=' + generate_token('process-owner'))
        self.assertEqual(reply.status, 200, await reply.text())
        self.payload = {'project_id':'http-process', 'workspace':str(self.repo), 'command':'printf http-ready; exec sleep 60', 'request_id':uuid4().hex}

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        if self.prior is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = self.prior
        self.temp.cleanup()

    async def test_http_lifecycle_and_auth(self):
        async with ClientSession() as anonymous:
            response = await anonymous.post(self.url, json=self.payload)
            self.assertIn(response.status, (401, 403))
        response = await self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status, 200, await response.text())
        row = await response.json()
        registry = get_registry(self.root / 'capabilities' / 'workspace', allowed_roots=[self.root])
        await output(registry, row['id'], 'http-ready')
        response = await self.client.get(self.url + '/' + row['id'] + '/logs')
        self.assertEqual((await response.json())['text'], 'http-ready')
        response = await self.client.get(self.url + '/' + row['id'])
        self.assertEqual((await response.json())['status'], 'running')
        response = await self.client.post(self.url + '/' + row['id'] + '/stop', json={'revision':0})
        self.assertEqual(response.status, 409)
        response = await self.client.post(self.url + '/' + row['id'] + '/stop', json={'revision':row['revision']})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['status'], 'stopped')
        response = await self.client.post(self.url, json={**self.payload, 'request_id':'denied', 'command':'cat ~/.ssh/id_rsa'})
        self.assertEqual(response.status, 403)
        response = await self.client.get(self.url)
        self.assertEqual(len(await response.json()), 1)

    async def test_actual_native_tool_consumer_and_metadata(self):
        from gideon.workspace.capabilities.workspace.tools import create_provider
        provider = create_provider()
        definitions = {tool.name:tool for tool in await provider.list_tools()}
        self.assertTrue(definitions['workspace_process_start'].requires_approval)
        self.assertEqual(definitions['workspace_process_start'].risk_level.value, 'destructive')
        self.assertFalse(definitions['workspace_process_logs'].requires_approval)
        started = await provider.invoke('workspace_process_start', self.payload)
        self.assertTrue(started.success, started.error)
        row = json.loads(started.output)
        response = await self.client.get(self.url + '/' + row['id'])
        self.assertEqual((await response.json())['id'], row['id'])
        registry = get_registry(self.root / 'capabilities' / 'workspace', allowed_roots=[self.root])
        await output(registry, row['id'], 'http-ready')
        logs = await provider.invoke('workspace_process_logs', {'id':row['id']})
        self.assertTrue(logs.success)
        self.assertIn('http-ready', json.loads(logs.output)['text'])
        stopped = await provider.invoke('workspace_process_stop', {'id':row['id'], 'revision':row['revision']})
        self.assertEqual(json.loads(stopped.output)['status'], 'stopped')
        denied = await provider.invoke('workspace_process_start', {**self.payload, 'env':{}})
        self.assertFalse(denied.success)
        unknown = await provider.invoke('workspace_unknown', {})
        self.assertFalse(unknown.success)
        store = SnapshotStore(self.root / 'capabilities' / 'workspace', allowed_roots=[self.root])
        snapshot = store.capture({'project_id':'tool-project','workspace':str(self.repo),'request_id':'snapshot'}, terminal_ids=[],task_ids=[])
        listed = await provider.invoke('workspace_snapshots', {})
        self.assertEqual(json.loads(listed.output), [snapshot])
        fetched = await provider.invoke('workspace_snapshot_get', {'id':snapshot['id']})
        self.assertEqual(json.loads(fetched.output), snapshot)
        deleted = await provider.invoke('workspace_snapshot_delete', {'id':snapshot['id'], 'revision':1})
        self.assertTrue(json.loads(deleted.output)['deleted'])
        missing = await provider.invoke('workspace_snapshot_get', {'id':snapshot['id']})
        self.assertFalse(missing.success)


if __name__ == '__main__':
    unittest.main()
