"""Durable process records with exclusive ownership of live subprocess handles."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.core.cancellation import terminate_and_reap
from gideon.security.sandbox import PROFILE_TOOL, build_child_env, create_subprocess_limited, wrap_argv
from gideon.security.security import is_sensitive_bash_command, redact_credentials
from .store import ConflictError


class ProcessRegistry:
    def __init__(self, root, *, allowed_roots):
        self.root = Path(root)
        self.allowed_roots = [Path(p).resolve() for p in allowed_roots]
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.root / 'processes.lock').open('a')
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise ConflictError('Another process registry owns this home') from None
        self.path = self.root / 'processes.sqlite3'
        self.closed = False
        self.handles = {}
        self.monitors = {}
        self.stopping = set()
        self.mutex = asyncio.Lock()
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS processes(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, input TEXT NOT NULL, payload TEXT NOT NULL, log TEXT NOT NULL DEFAULT "")')
            for row in db.execute('SELECT id,payload FROM processes').fetchall():
                record = json.loads(row[1])
                if record['status'] in ('starting', 'running'):
                    record.update(status='interrupted', revision=record['revision'] + 1, ended_at=datetime.now(timezone.utc).isoformat())
                    db.execute('UPDATE processes SET payload=? WHERE id=?', (json.dumps(record), row[0]))
        os.chmod(self.path, 0o600)

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def _input(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'project_id', 'workspace', 'command', 'request_id'}:
            raise ValueError('Expected project_id, workspace, command and request_id only')
        for key, limit in (('project_id', 256), ('workspace', 4096), ('command', 8192), ('request_id', 256)):
            value = payload[key]
            if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
                raise ValueError(f'Invalid {key}')
        path = Path(payload['workspace'])
        if not path.is_absolute():
            raise ValueError('Workspace must be absolute')
        path = path.resolve(strict=True)
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK) or not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError('Workspace outside allowed roots or inaccessible')
        denial = is_sensitive_bash_command(payload['command'])
        if denial:
            raise PermissionError(denial)
        return {**payload, 'workspace': str(path)}

    def _update(self, process_id, **fields):
        record = self.get(process_id)
        record.update(fields, revision=record['revision'] + 1)
        with self._db() as db:
            db.execute('UPDATE processes SET payload=? WHERE id=?', (json.dumps(record), process_id))
        return record

    async def start(self, payload):
        normalized = self._input(payload)
        encoded = json.dumps(normalized, sort_keys=True)
        async with self.mutex:
            if self.closed:
                raise ConflictError('Process registry is closed')
            with self._db() as db:
                row = db.execute('SELECT input,payload FROM processes WHERE request_id=?', (normalized['request_id'],)).fetchone()
                if row:
                    if row[0] != encoded:
                        raise ConflictError('Request ID already used for another launch')
                    return json.loads(row[1])
                if len(self.handles) >= 16:
                    raise ConflictError('Maximum 16 managed processes are already running')
                if db.execute('SELECT COUNT(*) FROM processes').fetchone()[0] >= 1000:
                    raise ConflictError('Process history capacity reached')
                record = {**normalized, 'id': uuid4().hex, 'status': 'starting', 'revision': 1,
                          'exit_code': None, 'started_at': None, 'ended_at': None, 'captured_at': datetime.now(timezone.utc).isoformat()}
                db.execute('INSERT INTO processes(id,request_id,input,payload) VALUES(?,?,?,?)',
                           (record['id'], normalized['request_id'], encoded, json.dumps(record)))
            disposable = None
            proc = None
            try:
                argv, disposable = wrap_argv(['/bin/sh', '-c', normalized['command']])
                proc = await create_subprocess_limited(*argv, profile=PROFILE_TOOL, cwd=normalized['workspace'],
                    env=build_child_env(site='workspace-process'), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
                self.handles[record['id']] = proc
                running = self._update(record['id'], status='running', started_at=datetime.now(timezone.utc).isoformat())
                self.monitors[record['id']] = asyncio.create_task(self._watch(record['id'], proc, disposable))
                return running
            except BaseException:
                if proc is not None:
                    await terminate_and_reap(proc)
                if disposable:
                    Path(disposable).unlink(missing_ok=True)
                self.handles.pop(record['id'], None)
                self._update(record['id'], status='failed', ended_at=datetime.now(timezone.utc).isoformat())
                raise

    async def _watch(self, process_id, proc, disposable):
        try:
            while chunk := await proc.stdout.read(65536):
                text, _ = redact_credentials(chunk.decode('utf-8', 'replace'))
                with self._db() as db:
                    previous = db.execute('SELECT log FROM processes WHERE id=?', (process_id,)).fetchone()[0]
                    db.execute('UPDATE processes SET log=? WHERE id=?', ((previous + text)[-65536:], process_id))
            code = await proc.wait()
            status = 'stopped' if process_id in self.stopping else 'exited' if code == 0 else 'failed'
            self._update(process_id, status=status, exit_code=code, ended_at=datetime.now(timezone.utc).isoformat())
        except BaseException:
            await terminate_and_reap(proc)
            self._update(process_id, status='interrupted', exit_code=proc.returncode, ended_at=datetime.now(timezone.utc).isoformat())
            raise
        finally:
            self.handles.pop(process_id, None)
            self.stopping.discard(process_id)
            if disposable:
                Path(disposable).unlink(missing_ok=True)

    def get(self, process_id):
        with self._db() as db:
            row = db.execute('SELECT payload FROM processes WHERE id=?', (process_id,)).fetchone()
        if row is None:
            raise FileNotFoundError('Managed process not found')
        return json.loads(row[0])

    def list(self, *, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid process pagination')
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM processes ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset))]

    def logs(self, process_id, *, limit=65536):
        if type(limit) is not int or not 1 <= limit <= 65536:
            raise ValueError('Invalid log limit')
        self.get(process_id)
        with self._db() as db:
            text = db.execute('SELECT log FROM processes WHERE id=?', (process_id,)).fetchone()[0]
        return {'text': text[-limit:]}

    async def stop(self, process_id, revision):
        async with self.mutex:
            record = self.get(process_id)
            if type(revision) is not int or revision != record['revision']:
                raise ConflictError('Managed process revision changed')
            proc = self.handles.get(process_id)
            if proc is None:
                return record
            self.stopping.add(process_id)
            await terminate_and_reap(proc)
            await self.monitors[process_id]
            return self.get(process_id)

    async def close(self):
        async with self.mutex:
            if self.closed:
                return
            self.closed = True
            for process_id, proc in list(self.handles.items()):
                self.stopping.add(process_id)
                await terminate_and_reap(proc)
            if self.monitors:
                await asyncio.gather(*self.monitors.values(), return_exceptions=True)
            fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            self.lock_file.close()


_registries = {}

def get_registry(root, *, allowed_roots):
    key = str(Path(root).resolve())
    existing = _registries.get(key)
    if existing is None or existing.closed:
        existing = ProcessRegistry(root, allowed_roots=allowed_roots)
        _registries[key] = existing
    return existing


async def close_registry(root):
    existing = _registries.pop(str(Path(root).resolve()), None)
    if existing is not None:
        await existing.close()
