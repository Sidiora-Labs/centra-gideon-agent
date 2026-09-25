from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from uuid import uuid4

from .store import PeopleError, fields, text

PLATFORMS = ('x', 'stackernews', 'github', 'mastodon', 'bluesky', 'linkedin', 'instagram', 'other')
FIELDS = {'platform', 'handle', 'label', 'profile_url', 'credential_ref', 'person_id', 'status', 'notes'}
PREFIXES = {'x': 'https://x.com/', 'stackernews': 'https://stacker.news/', 'github': 'https://github.com/', 'instagram': 'https://www.instagram.com/', 'bluesky': 'https://bsky.app/profile/', 'linkedin': 'https://www.linkedin.com/in/'}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS social_accounts (id TEXT PRIMARY KEY,platform TEXT NOT NULL,handle TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL,UNIQUE(platform,handle))')
    db.execute('CREATE TABLE IF NOT EXISTS social_requests (key TEXT PRIMARY KEY,body TEXT NOT NULL,account_id TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS social_history (id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,event TEXT NOT NULL,at TEXT NOT NULL,body TEXT NOT NULL)')


def normalize(data):
    fields(data, FIELDS)
    platform = data.get('platform')
    if platform not in PLATFORMS:
        raise PeopleError('Unsupported social platform')
    handle = unicodedata.normalize('NFKC', text(data.get('handle'), 'handle', 200, True)).lstrip('@').casefold()
    if not handle or any(character.isspace() or unicodedata.category(character).startswith('C') for character in handle) or any(character in handle for character in '/?#\\:'):
        raise PeopleError('Use a platform handle, not a URL or secret')
    url = text(data.get('profile_url', ''), 'profile_url', 2000)
    if not url and platform in PREFIXES:
        url = PREFIXES[platform] + quote(handle, safe='@.-_')
    if url:
        try:
            parsed = urlsplit(url)
            port = parsed.port
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or port not in (None, 443) or any(c.isspace() or unicodedata.category(c).startswith('C') for c in url):
                raise ValueError()
        except ValueError:
            raise PeopleError('Social profile links must be HTTPS without credentials or invalid characters') from None
    credential = text(data.get('credential_ref', ''), 'credential_ref', 120)
    if credential and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', credential):
        raise PeopleError('Use a credential reference instead of a secret')
    status = data.get('status', 'active')
    if status not in ('active', 'paused', 'archived'):
        raise PeopleError('Social account status must be active, paused or archived')
    person_id = data.get('person_id')
    if person_id is not None:
        person_id = text(person_id, 'person_id', 100, True)
    return {'platform': platform, 'handle': handle, 'label': text(data.get('label', handle), 'label', 200, True), 'profile_url': url,
            'credential_ref': credential, 'person_id': person_id, 'status': status, 'notes': text(data.get('notes', ''), 'notes', 10000)}


def accounts(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [{**json.loads(row[0]), 'revision': row[1]} for row in db.execute('SELECT body,revision FROM social_accounts ORDER BY platform,handle')]


def get(store, account_id):
    row = next((item for item in accounts(store) if item['id'] == account_id), None)
    if row is None:
        raise PeopleError('Social account not found', 404)
    return row


def record(db, account_id, event, body):
    db.execute('INSERT INTO social_history(account_id,event,at,body) VALUES (?,?,?,?)', (account_id, event, datetime.now(timezone.utc).isoformat(), json.dumps(body)))


def save(store, data, account_id=None):
    fields(data, FIELDS | ({'revision'} if account_id else {'request_key'}))
    values = normalize({k: v for k, v in data.items() if k in FIELDS})
    revision = data.get('revision')
    if account_id and (type(revision) is not int or revision < 1):
        raise PeopleError('Current social account revision is required')
    request_key = text(data.get('request_key'), 'request_key', 200, True) if account_id is None else None
    canonical = json.dumps(values, sort_keys=True)
    try:
        with closing(store.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            schema(db)
            if values['person_id'] and not db.execute('SELECT id FROM people WHERE id=?', (values['person_id'],)).fetchone():
                raise PeopleError('Linked person not found', 404)
            if request_key:
                previous = db.execute('SELECT body,account_id FROM social_requests WHERE key=?', (request_key,)).fetchone()
                if previous:
                    if previous[0] != canonical:
                        raise PeopleError('Social account request key conflicts', 409)
                    old = db.execute('SELECT body,revision FROM social_accounts WHERE id=?', (previous[1],)).fetchone()
                    if not old:
                        raise PeopleError('This registration was removed; use a new request key', 410)
                    return {**json.loads(old[0]), 'revision': old[1]}, False
            old = db.execute('SELECT body,revision FROM social_accounts WHERE id=?', (account_id,)).fetchone() if account_id else None
            if account_id and not old:
                raise PeopleError('Social account not found', 404)
            if old and old[1] != revision:
                raise PeopleError('Social account changed; reload', 409)
            account_id = account_id or uuid4().hex
            next_revision = old[1] + 1 if old else 1
            body = {**values, 'id': account_id, 'qualification': 'registry_only', 'created_at': json.loads(old[0])['created_at'] if old else datetime.now(timezone.utc).isoformat()}
            db.execute('INSERT INTO social_accounts VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET platform=excluded.platform,handle=excluded.handle,body=excluded.body,revision=excluded.revision', (account_id, values['platform'], values['handle'], json.dumps(body), next_revision))
            if request_key:
                db.execute('INSERT INTO social_requests VALUES (?,?,?)', (request_key, canonical, account_id))
            record(db, account_id, 'updated' if old else 'created', {**body, 'revision': next_revision})
        return {**body, 'revision': next_revision}, old is None
    except sqlite3.IntegrityError:
        raise PeopleError('This platform and normalized handle are already registered', 409) from None


def remove(store, account_id, revision):
    if type(revision) is not int:
        raise PeopleError('Current social account revision is required')
    with closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        old = db.execute('SELECT body,revision FROM social_accounts WHERE id=?', (account_id,)).fetchone()
        if not old:
            raise PeopleError('Social account not found', 404)
        if old[1] != revision:
            raise PeopleError('Social account changed; reload', 409)
        try:
            db.execute('DELETE FROM social_accounts WHERE id=?', (account_id,))
        except sqlite3.IntegrityError:
            raise PeopleError('Social account is referenced by another record; archive it instead', 409) from None
        record(db, account_id, 'removed', {**json.loads(old[0]), 'revision': revision})
    return {'id': account_id, 'removed': True, 'external_account_changed': False}


def history(store, account_id):
    with closing(store.connect()) as db, db:
        schema(db)
        return [{'event': row[0], 'at': row[1], 'account': json.loads(row[2])} for row in db.execute('SELECT event,at,body FROM social_history WHERE account_id=? ORDER BY id', (account_id,))]
