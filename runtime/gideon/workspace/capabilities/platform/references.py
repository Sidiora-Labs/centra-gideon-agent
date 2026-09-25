"""Explicit Git reference checks with durable reviewed cursors."""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import config_dir, workspace_root


class ReferenceError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _file():
    return config_dir() / 'reference-repositories.json'


def records():
    try:
        if _file().stat().st_size > 2_000_000:
            raise ValueError('oversized')
        rows = json.loads(_file().read_text())
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid')
        return rows
    except FileNotFoundError:
        return []
    except (ValueError, OSError):
        raise ReferenceError('Reference records are unreadable', 503)


def _write(rows):
    _file().parent.mkdir(parents=True, exist_ok=True)
    atomic_write(_file(), json.dumps(rows) + '\n')


def _repo(value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ReferenceError('A workspace-relative repository path is required')
    root = workspace_root().resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_dir():
        raise ReferenceError('Repository must be inside the workspace')
    return path


def _git(path, *args):
    try:
        result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise ReferenceError('Git operation unavailable or timed out', 503)
    if result.returncode:
        raise ReferenceError('Git operation failed; check repository and origin access', 409)
    return result.stdout.strip()


def _identity(path, branch):
    top = Path(_git(path, 'rev-parse', '--show-toplevel')).resolve()
    if top != path:
        raise ReferenceError('Select the repository root')
    git_dir = Path(_git(path, 'rev-parse', '--absolute-git-dir')).resolve()
    if not git_dir.is_relative_to(workspace_root().resolve()):
        raise ReferenceError('Git metadata must be inside the workspace')
    origin = _git(path, 'remote', 'get-url', 'origin')
    if origin.startswith(('ext::', '-')):
        raise ReferenceError('Unsupported Git remote transport')
    return hashlib.sha256(json.dumps([str(top), origin, branch]).encode()).hexdigest()


def add(body):
    if not isinstance(body, dict) or set(body) != {'name', 'path', 'branch'}:
        raise ReferenceError('Name, path and branch are required')
    if not isinstance(body['name'], str) or not 1 <= len(body['name'].strip()) <= 100:
        raise ReferenceError('Name must contain 1 to 100 characters')
    branch = body['branch']
    if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', branch) or '..' in branch:
        raise ReferenceError('Invalid branch name')
    path = _repo(body['path'])
    _git(path, 'check-ref-format', '--branch', branch)
    identity = _identity(path, branch)
    rows = records()
    if len(rows) >= 100:
        raise ReferenceError('Reference limit reached', 409)
    if any(row.get('identity') == identity for row in rows):
        raise ReferenceError('This repository branch is already tracked', 409)
    row = {**body, 'name': body['name'].strip(), 'id': uuid4().hex, 'identity': identity,
           'reviewed': None, 'snapshot': None, 'checked_at': None, 'stale': False, 'error': None}
    rows.append(row)
    _write(rows)
    return row


def change(identifier, action, body):
    rows = records()
    row = next((row for row in rows if row.get('id') == identifier), None)
    if row is None:
        raise ReferenceError('Unknown reference', 404)
    if action == 'remove':
        _write([item for item in rows if item is not row])
        return
    if action not in {'check', 'review'}:
        raise ReferenceError('Unknown reference action')
    path = _repo(row['path'])
    if _identity(path, row['branch']) != row['identity']:
        raise ReferenceError('Repository origin or branch changed; register a new reference', 409)
    if action == 'review':
        if not isinstance(body, dict) or body.get('head') != (row.get('snapshot') or {}).get('head') or not body.get('head') or row['stale']:
            raise ReferenceError('Refresh and review the current observed commit', 409)
        row['reviewed'] = body['head']
        row['snapshot']['commits'] = []
        _write(rows)
        return
    row['checked_at'] = datetime.now(timezone.utc).isoformat()
    try:
        _git(path, 'fetch', '--no-tags', 'origin', row['branch'])
        head = _git(path, 'rev-parse', 'FETCH_HEAD^{commit}')
        if row['reviewed']:
            _git(path, 'merge-base', '--is-ancestor', row['reviewed'], head)
        revision = f"{row['reviewed']}..{head}" if row['reviewed'] else head
        lines = _git(path, 'log', '-50', '--format=%H%x09%s', revision, '--').splitlines()
        row['snapshot'] = {'head': head, 'commits': [{'sha': line.partition('\t')[0], 'subject': line.partition('\t')[2]} for line in lines], 'limit': 50}
        row.update(stale=False, error=None)
    except ReferenceError as error:
        row.update(stale=True, error=str(error))
    _write(rows)


def view():
    return {'version': 1, 'references': [{key: value for key, value in row.items() if key != 'identity'} for row in records()]}
