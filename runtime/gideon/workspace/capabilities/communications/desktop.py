from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from . import evidence
from .store import PeopleError, fields, person_values, text

MAX_BYTES = 8 * 1024 * 1024
INPUT = {'source', 'source_account_id', 'content_base64'}
SHAPES = {
    'imessage': {'message': {'guid', 'date', 'text', 'is_from_me', 'handle_id'}, 'handle': {'id'}, 'chat': {'guid'}, 'chat_message_join': {'message_id', 'chat_id'}},
    'signal': {'messages': {'id', 'conversationId', 'sent_at', 'received_at', 'type', 'body'}, 'conversations': {'id', 'e164', 'type'}},
}


def decode(data):
    fields(data, INPUT)
    source = data.get('source')
    if source not in SHAPES:
        raise PeopleError('Choose iMessage or Signal Desktop')
    account = text(data.get('source_account_id'), 'source_account_id', 100, True)
    encoded = data.get('content_base64')
    if not isinstance(encoded, str) or len(encoded) > ((MAX_BYTES + 2) // 3) * 4:
        raise PeopleError('SQLite snapshot must be base64 and at most 8 MiB')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise PeopleError('Invalid base64 snapshot') from None
    if len(raw) > MAX_BYTES or not raw.startswith(b'SQLite format 3\x00'):
        raise PeopleError('A plain SQLite snapshot is required; encrypted Signal databases must first be exported or decrypted locally')
    return source, account, raw


def timestamp(value, source):
    try:
        number = int(value)
        if number <= 0:
            return None
        if source == 'imessage':
            seconds = number / 1_000_000_000 if number > 10_000_000_000 else number
            observed = datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        else:
            observed = datetime.fromtimestamp(number / 1000, timezone.utc)
        return observed.isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def rows_from_snapshot(raw, source):
    with closing(sqlite3.connect(':memory:')) as db:
        try:
            db.deserialize(raw)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.enable_load_extension(False)
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES)
            budget = 0
            def progress():
                nonlocal budget
                budget += 1
                return int(budget > 2000)
            db.set_progress_handler(progress, 1000)
            for table, columns in SHAPES[source].items():
                definition = db.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (table,)).fetchone()
                if not definition or definition[0] != 'table' or not isinstance(definition[1], str) or 'VIRTUAL TABLE' in definition[1].upper():
                    raise PeopleError('Unsupported desktop schema: required ordinary table ' + table)
                actual = {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}
                if not columns <= actual:
                    raise PeopleError('Unsupported desktop schema: missing columns in ' + table)
            if source == 'imessage':
                query = 'SELECT m.ROWID,m.guid,c.guid,m.date,m.text,m.is_from_me,h.id FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID JOIN chat c ON c.ROWID=j.chat_id LEFT JOIN handle h ON h.ROWID=m.handle_id ORDER BY m.ROWID LIMIT 1001'
            else:
                query = 'SELECT m.ROWID,m.id,m.conversationId,COALESCE(NULLIF(m.sent_at,0),m.received_at),m.body,m.type,CASE WHEN c.type="private" THEN c.e164 ELSE NULL END FROM messages m LEFT JOIN conversations c ON c.id=m.conversationId ORDER BY m.ROWID LIMIT 1001'
            raw_rows = db.execute(query).fetchall()
            if len(raw_rows) > 1000:
                raise PeopleError('Snapshot contains more than 1000 messages; export a bounded subset')
            result = []
            identities = set()
            for rowid, identity, thread, date, body, kind, handle in raw_rows:
                identity = text(identity, 'message identity', 500, True)
                thread = text(thread, 'thread identity', 100, True)
                if identity in identities:
                    raise PeopleError('Snapshot has duplicate message identities')
                identities.add(identity)
                direction = ('outbound' if kind == 1 else 'inbound' if kind == 0 else None) if source == 'imessage' else {'incoming': 'inbound', 'outgoing': 'outbound'}.get(kind)
                occurred = timestamp(date, source)
                row = {'rowid': rowid, 'external_id': identity, 'thread_id': thread, 'occurred_at': occurred,
                       'direction': direction, 'body': text(body or '', 'message body', 100000), 'identity': None,
                       'limitations': []}
                if handle:
                    try:
                        row['identity'] = person_values({'name': 'source', 'identities': [{'kind': 'email' if '@' in handle else 'phone', 'value': handle}]})['identities'][0]
                    except PeopleError:
                        row['limitations'].append('unresolved_identity')
                if not body:
                    row['limitations'].append('text_or_attachment_body_unavailable')
                if not direction or not occurred:
                    row['limitations'].append('unsupported_event_or_timestamp')
                result.append(row)
            return result
        except (sqlite3.Error, TypeError, UnicodeError) as exc:
            raise PeopleError('Unsupported or damaged desktop SQLite snapshot') from exc


def preview(store, data):
    source, account, raw = decode(data)
    rows = rows_from_snapshot(raw, source)
    people = store.people()
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        matches = [person for person in people if row['identity'] and row['identity'] in person['identities']]
        row['person_id'] = matches[0]['id'] if len(matches) == 1 else None
        row['eligible'] = bool(row['person_id'] and row['direction'] and row['occurred_at'] and row['occurred_at'] <= now)
    return {'source': source, 'source_account_id': account, 'source_digest': hashlib.sha256(raw).hexdigest(),
            'review_token': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            'rows': rows, 'coverage': 'snapshot_only', 'qualification': 'uploaded_snapshot',
            'limits': ['No live desktop access or encrypted database unlock', 'Attributed text and attachment bodies are not decoded', 'Snapshot coverage never certifies a complete conversation']}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS desktop_imports (source TEXT NOT NULL,account TEXT NOT NULL,digest TEXT NOT NULL,receipt TEXT NOT NULL,raw BLOB NOT NULL,UNIQUE(source,account,digest))')
    db.execute('CREATE TABLE IF NOT EXISTS desktop_messages (source TEXT NOT NULL,account TEXT NOT NULL,external_id TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(source,account,external_id))')
    evidence.schema(db)


def commit(store, data):
    fields(data, INPUT | {'source_digest', 'review_token'})
    source, account, raw = decode({k: v for k, v in data.items() if k in INPUT})
    raw_digest = hashlib.sha256(raw).hexdigest()
    with closing(store.connect()) as db, db:
        schema(db)
        previous = db.execute('SELECT receipt FROM desktop_imports WHERE source=? AND account=? AND digest=?', (source, account, raw_digest)).fetchone()
        if previous:
            receipt = json.loads(previous[0])
            if data.get('source_digest') != raw_digest or data.get('review_token') != receipt['review_token']:
                raise PeopleError('Import review does not match the stored receipt', 409)
            return receipt, False
    projected = preview(store, {k: v for k, v in data.items() if k in INPUT})
    if data.get('source_digest') != projected['source_digest'] or data.get('review_token') != projected['review_token']:
        raise PeopleError('Snapshot changed; preview it again', 409)
    source, account, raw = decode({k: v for k, v in data.items() if k in INPUT})
    captured = datetime.now(timezone.utc).isoformat()
    with closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        old = db.execute('SELECT receipt FROM desktop_imports WHERE source=? AND account=? AND digest=?', (source, account, projected['source_digest'])).fetchone()
        if old:
            return json.loads(old[0]), False
        inserted, linked = 0, 0
        for row in projected['rows']:
            canonical = {k: v for k, v in row.items() if k not in ('person_id', 'eligible')}
            body = json.dumps(canonical, sort_keys=True)
            previous = db.execute('SELECT body FROM desktop_messages WHERE source=? AND account=? AND external_id=?', (source, account, row['external_id'])).fetchone()
            if previous and {k: v for k, v in json.loads(previous[0]).items() if k != 'rowid'} != {k: v for k, v in canonical.items() if k != 'rowid'}:
                raise PeopleError('Desktop message identity has conflicting content', 409)
            if not previous:
                db.execute('INSERT INTO desktop_messages VALUES (?,?,?,?)', (source, account, row['external_id'], body))
                inserted += 1
            if row['eligible']:
                event = {k: row[k] for k in ('person_id', 'thread_id', 'external_id', 'occurred_at', 'direction')}
                event['summary'] = row['body'][:2000]
                old_event = db.execute('SELECT body FROM relationship_messages WHERE source=? AND source_account_id=? AND external_id=?', (source, account, row['external_id'])).fetchone()
                if old_event and json.loads(old_event[0]) != event:
                    raise PeopleError('Relationship evidence conflicts with this source message', 409)
                if not old_event:
                    if not db.execute('SELECT id FROM people WHERE id=?', (row['person_id'],)).fetchone():
                        raise PeopleError('Person changed since preview', 409)
                    db.execute('INSERT INTO relationship_messages VALUES (?,?,?,?)', (source, account, row['external_id'], json.dumps(event)))
                    linked += 1
        coverage = {'source': source, 'source_account_id': account, 'captured_at': captured,
                    'coverage_start': min((r['occurred_at'] for r in projected['rows'] if r['eligible']), default=captured),
                    'coverage_end': captured, 'incoming_complete': False, 'outgoing_complete': False}
        db.execute('INSERT INTO relationship_coverage VALUES (?,?,?) ON CONFLICT(source,source_account_id) DO UPDATE SET body=excluded.body', (source, account, json.dumps(coverage)))
        receipt = {k: projected[k] for k in ('source', 'source_account_id', 'source_digest', 'review_token', 'coverage', 'qualification')}
        receipt.update(inserted=inserted, linked=linked, messages=len(projected['rows']), captured_at=captured)
        db.execute('INSERT INTO desktop_imports VALUES (?,?,?,?,?)', (source, account, projected['source_digest'], json.dumps(receipt), raw))
    return receipt, True


def imports(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [json.loads(row[0]) for row in db.execute('SELECT receipt FROM desktop_imports ORDER BY rowid DESC')]


def history(store, source, account):
    with closing(store.connect()) as db, db:
        schema(db)
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM desktop_messages WHERE source=? AND account=? ORDER BY rowid', (source, account))]
