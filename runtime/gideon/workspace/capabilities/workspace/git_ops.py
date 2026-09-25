"""Canonical project Git operations with cached-only submodule updates."""
import asyncio
import fcntl
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.core.cancellation import terminate_and_reap
from gideon.security.sandbox import PROFILE_TOOL, build_child_env, create_subprocess_limited, wrap_argv
from .projects import ProjectService
from .store import ConflictError


class GitService:
    def __init__(self, root, *, allowed_roots, hierarchy=None):
        self.projects = ProjectService(root, allowed_roots=allowed_roots, hierarchy=hierarchy)
        self.root = Path(root)
        self.db_path = self.root / 'git-operations.sqlite3'
        with sqlite3.connect(self.db_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, input TEXT, payload TEXT)')
        os.chmod(self.db_path, 0o600)

    async def _git(self, path, *args):
        env = {k:v for k,v in build_child_env(site='workspace-git').items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
        argv, disposable = wrap_argv(['git','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-c','protocol.allow=never','-c','submodule.recurse=false',*args])
        proc = None
        try:
            proc = await create_subprocess_limited(*argv, profile=PROFILE_TOOL, cwd=str(path), env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            async def collect():
                output = bytearray()
                while chunk := await proc.stdout.read(8192):
                    output.extend(chunk)
                    if len(output) > 262144:
                        raise ValueError('Git output exceeds 256 KiB')
                await proc.wait()
                return bytes(output).decode('utf-8','replace')
            output = await asyncio.wait_for(collect(), 15)
            if proc.returncode:
                raise ValueError('Git operation failed; check repository state and cached objects')
            return output
        finally:
            if proc is not None and proc.returncode is None:
                await terminate_and_reap(proc)
            if disposable:
                Path(disposable).unlink(missing_ok=True)

    async def _repo(self, project_id):
        project = self.projects.hierarchy.get_project(project_id)
        if project is None:
            raise FileNotFoundError('Project not found')
        path = self.projects._path(project.workspace_dir)
        top = Path((await self._git(path,'rev-parse','--show-toplevel')).strip()).resolve()
        if top != path:
            raise ValueError('Project must identify an exact repository root')
        await self._check_git_dir(path)
        return path

    async def _check_git_dir(self, path):
        for arg in ('--absolute-git-dir','--git-common-dir'):
            raw = (await self._git(path,'rev-parse',arg)).strip()
            directory = (path / raw).resolve()
            if not any(directory.is_relative_to(root) for root in self.projects.allowed_roots):
                raise ValueError('Git metadata outside allowed roots')
        names = (await self._git(path,'config','--local','--name-only','--list')).lower().splitlines()
        if any(name.startswith(('filter.','include.','includeif.')) for name in names):
            raise ValueError('Repository filters and config includes are not supported')

    async def inspect(self, project_id):
        path = await self._repo(project_id)
        head = (await self._git(path,'rev-parse','HEAD')).strip()
        branch = (await self._git(path,'branch','--show-current')).strip()
        status = await self._git(path,'status','--porcelain=v1','--untracked-files=normal','--ignore-submodules=all')
        submodules = []
        for item in (await self._git(path,'ls-files','--stage','-z')).split('\0'):
            if not item.startswith('160000 '):
                continue
            meta, name = item.split('\t',1)
            if len(submodules) >= 100:
                raise ValueError('Maximum 100 submodules supported')
            child = path / name
            resolved = child.resolve()
            if child.is_symlink() or not resolved.is_relative_to(path):
                raise ValueError('Submodule path escapes repository')
            initialized = (child / '.git').exists()
            row = {'path':name,'expected_head':meta.split()[1],'head':None,'initialized':initialized,'dirty':False}
            if initialized:
                await self._check_git_dir(child)
                childtop = Path((await self._git(child,'rev-parse','--show-toplevel')).strip()).resolve()
                if childtop != resolved:
                    raise ValueError('Invalid initialized submodule root')
                row['head'] = (await self._git(child,'rev-parse','HEAD')).strip()
                row['dirty'] = bool(await self._git(child,'status','--porcelain=v1','--untracked-files=normal'))
            submodules.append(row)
        return {'project_id':project_id,'head':head,'branch':branch,'status':status,'dirty':bool(status) or any(x['dirty'] for x in submodules),'submodules':submodules}

    def list(self, project_id):
        if self.projects.hierarchy.get_project(project_id) is None:
            raise FileNotFoundError('Project not found')
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute('SELECT payload FROM operations ORDER BY rowid DESC LIMIT 1000').fetchall()
        return [value for row in rows if (value := json.loads(row[0]))['project_id'] == project_id][:100]

    async def mutate(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'project_id','operation','target','expected_head','request_id'}:
            raise ValueError('Invalid Git operation fields')
        if any(not isinstance(v,str) or not v or len(v)>256 or '\x00' in v for v in payload.values()):
            raise ValueError('Invalid Git operation input')
        operation = payload['operation']
        if operation not in ('create_branch','switch_branch','submodule_update'):
            raise ValueError('Unsupported Git operation')
        if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',payload['expected_head']):
            raise ValueError('Expected full Git revision')
        encoded = json.dumps(payload,sort_keys=True)
        with (self.root / 'git-operations.lock').open('a') as lock, sqlite3.connect(self.db_path) as db:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ConflictError('Another Git operation is in progress') from None
            previous = db.execute('SELECT input,payload FROM operations WHERE request_id=?',(payload['request_id'],)).fetchone()
            if previous:
                if previous[0] != encoded:
                    raise ConflictError('Request ID already used for another operation')
                record = json.loads(previous[1])
                if record['status'] != 'succeeded':
                    raise ConflictError('Prior attempt incomplete or failed; inspect before a new request')
                return record
            path = await self._repo(payload['project_id'])
            state = await self.inspect(payload['project_id'])
            if state['head'] != payload['expected_head'] or state['dirty']:
                raise ConflictError('Repository revision changed or working tree is dirty')
            target = payload['target']
            if operation == 'submodule_update':
                child = next((x for x in state['submodules'] if x['path']==target),None)
                if child is None or not child['initialized']:
                    raise ValueError('Select an initialized submodule')
                args = ('-C',str(path / target),'checkout','--detach',child['expected_head'])
            else:
                if target.startswith('-') or '@{' in target:
                    raise ValueError('Invalid branch name')
                await self._git(path,'check-ref-format','--branch',target)
                args = ('switch','-c',target) if operation == 'create_branch' else ('switch','--no-guess',target)
            record = {'id':uuid4().hex,**payload,'status':'started','head_before':state['head'],'head_after':None,'created_at':datetime.now(timezone.utc).isoformat()}
            db.execute('INSERT INTO operations VALUES(?,?,?,?)',(record['id'],payload['request_id'],encoded,json.dumps(record)))
            db.commit()
            try:
                await self._git(path,*args)
                record.update(status='succeeded',head_after=(await self._git(path,'rev-parse','HEAD')).strip())
            except BaseException:
                record['status'] = 'failed'
                raise
            finally:
                db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(record),record['id']))
                db.commit()
            return record
