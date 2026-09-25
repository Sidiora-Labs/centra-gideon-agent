from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import sqlite3
import struct
from contextlib import closing
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import evidence
from .store import PeopleError, fields, person_values, text

PAGE_SIZE = 4096
RESERVE_BYTES = 80
MAX_BYTES = 32 * 1024 * 1024
INPUT = {'source_account_id', 'content_base64', 'key'}


def _decode(data):
    fields(data, INPUT)
    account = text(data.get('source_account_id'), 'source_account_id', 100, True)
    key = text(data.get('key'), 'Signal SQLCipher key', 64, True)
    if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
        raise PeopleError('Signal SQLCipher key must be exactly 64 hexadecimal characters')
    encoded = data.get('content_base64')
    if not isinstance(encoded, str) or len(encoded) > ((MAX_BYTES + 2) // 3) * 4:
        raise PeopleError('Encrypted Signal database must be base64 and at most 32 MiB')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise PeopleError('Invalid base64 Signal database') from None
    if len(raw) < PAGE_SIZE or len(raw) > MAX_BYTES or len(raw) % PAGE_SIZE:
        raise PeopleError('Signal database is not a bounded SQLCipher-4 page file')
    if raw.startswith(b'SQLite format 3\x00'):
        raise PeopleError('Signal import requires the encrypted SQLCipher database, not plaintext SQLite')
    return account, raw, bytes.fromhex(key)


def decrypt_sqlcipher4(raw, encryption_key):
    salt = raw[:16]
    hmac_key = hashlib.pbkdf2_hmac('sha512', encryption_key, bytes(value ^ 0x3A for value in salt), 2, 32)
    output = bytearray(len(raw))
    for page in range(1, len(raw) // PAGE_SIZE + 1):
        start = (page - 1) * PAGE_SIZE
        body_start = start + (16 if page == 1 else 0)
        iv_start = start + PAGE_SIZE - RESERVE_BYTES
        authenticated = raw[body_start:iv_start + 16]
        stored = raw[iv_start + 16:iv_start + RESERVE_BYTES]
        expected = hmac.new(hmac_key, authenticated + struct.pack('<I', page), hashlib.sha512).digest()
        if not hmac.compare_digest(stored, expected):
            raise PeopleError('Signal SQLCipher authentication failed; the key, database, or supported format is wrong')
        decryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(raw[iv_start:iv_start + 16])).decryptor()
        plain = decryptor.update(raw[body_start:iv_start]) + decryptor.finalize()
        output[start + (16 if page == 1 else 0):iv_start] = plain
    output[:16] = b'SQLite format 3\x00'
    if output[16:18] != b'\x10\x00' or output[20] != RESERVE_BYTES:
        raise PeopleError('Decrypted Signal database does not use the supported SQLCipher-4 page layout')
    return output


def _attachment_refs(value):
    try:
        document = json.loads(value or '{}')
    except (TypeError, json.JSONDecodeError):
        return []
    attachments = document.get('attachments', []) if isinstance(document, dict) else []
    if not isinstance(attachments, list):
        return []
    result = []
    for item in attachments[:100]:
        if not isinstance(item, dict):
            continue
        result.append({'path': text(item.get('path', ''), 'attachment path', 2000),
                       'name': text(item.get('fileName', ''), 'attachment name', 1000),
                       'content_type': text(item.get('contentType', ''), 'attachment content type', 500),
                       'size': item.get('size') if type(item.get('size')) is int and 0 <= item['size'] <= 10**12 else None})
    return result


def _rows(raw):
    with closing(sqlite3.connect(':memory:')) as db:
        try:
            db.deserialize(raw)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.enable_load_extension(False)
            required = {'messages': {'id', 'conversationId', 'sent_at', 'received_at', 'type', 'body', 'json'},
                        'conversations': {'id', 'e164', 'type'}}
            for table, columns in required.items():
                definition = db.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (table,)).fetchone()
                actual = {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')} if definition else set()
                if not definition or definition[0] != 'table' or not columns <= actual:
                    raise PeopleError('Unsupported Signal Desktop schema: missing required ' + table + ' fields')
            values = db.execute('''SELECT m.id,m.conversationId,COALESCE(NULLIF(m.sent_at,0),m.received_at),
                m.type,m.body,m.json,CASE WHEN c.type='private' THEN c.e164 ELSE NULL END
                FROM messages m LEFT JOIN conversations c ON c.id=m.conversationId ORDER BY m.ROWID LIMIT 1001''').fetchall()
        except (sqlite3.Error, TypeError, UnicodeError) as exc:
            raise PeopleError('Unsupported or damaged decrypted Signal database') from exc
    if len(values) > 1000:
        raise PeopleError('Signal archive contains more than 1000 messages; supply a bounded owned archive')
    rows = []
    seen = set()
    for identifier, conversation, when, kind, body, metadata, handle in values:
        identifier = text(identifier, 'message identity', 500, True)
        conversation = text(conversation, 'conversation identity', 500, True)
        provenance = conversation + ':' + identifier
        if provenance in seen:
            raise PeopleError('Signal archive has duplicate conversation message provenance')
        seen.add(provenance)
        try:
            occurred = datetime.fromtimestamp(int(when) / 1000, timezone.utc).isoformat() if int(when) > 0 else None
        except (TypeError, ValueError, OverflowError, OSError):
            occurred = None
        identity = None
        if handle:
            try:
                identity = person_values({'name': 'Signal source', 'identities': [{'kind': 'phone', 'value': handle}]})['identities'][0]
            except PeopleError:
                pass
        direction = {'incoming': 'inbound', 'outgoing': 'outbound'}.get(kind)
        rows.append({'external_id': provenance, 'message_id': identifier, 'conversation_id': conversation,
                     'occurred_at': occurred, 'direction': direction,
                     'body': text(body or '', 'message body', 100000), 'identity': identity,
                     'attachments': _attachment_refs(metadata),
                     'limitations': [] if direction and occurred else ['unsupported_event_or_timestamp']})
    return rows


def preview(store, data):
    account, encrypted, key = _decode(data)
    plaintext = decrypt_sqlcipher4(encrypted, key)
    try:
        rows = _rows(plaintext)
    finally:
        plaintext[:] = b'\x00' * len(plaintext)
    people = store.people()
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        matches = [person for person in people if row['identity'] and row['identity'] in person['identities']]
        row['person_id'] = matches[0]['id'] if len(matches) == 1 else None
        row['eligible'] = bool(row['person_id'] and row['direction'] and row['occurred_at'] and row['occurred_at'] <= now)
    digest = hashlib.sha256(encrypted).hexdigest()
    review = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return {'source_account_id': account, 'source_digest': digest, 'review_token': review, 'rows': rows,
            'coverage': 'supplied_encrypted_archive', 'qualification': 'authenticated_sqlcipher4'}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS signal_imports(account TEXT NOT NULL,digest TEXT NOT NULL,receipt TEXT NOT NULL,UNIQUE(account,digest))')
    db.execute('CREATE TABLE IF NOT EXISTS signal_messages(account TEXT NOT NULL,external_id TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(account,external_id))')
    evidence.schema(db)


def commit(store, data):
    fields(data, INPUT | {'source_digest', 'review_token'})
    projected = preview(store, {key: data.get(key) for key in INPUT})
    if data.get('source_digest') != projected['source_digest'] or data.get('review_token') != projected['review_token']:
        raise PeopleError('Encrypted Signal archive changed; preview it again', 409)
    account = projected['source_account_id']
    with closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        old = db.execute('SELECT receipt FROM signal_imports WHERE account=? AND digest=?', (account, projected['source_digest'])).fetchone()
        if old:
            return json.loads(old[0]), False
        inserted = linked = 0
        for row in projected['rows']:
            canonical = {key: value for key, value in row.items() if key not in ('person_id', 'eligible')}
            body = json.dumps(canonical, sort_keys=True)
            old_message = db.execute('SELECT body FROM signal_messages WHERE account=? AND external_id=?', (account, row['external_id'])).fetchone()
            if old_message and old_message[0] != body:
                raise PeopleError('Signal message provenance conflicts with existing history', 409)
            if not old_message:
                db.execute('INSERT INTO signal_messages VALUES (?,?,?)', (account, row['external_id'], body))
                inserted += 1
            if row['eligible']:
                event = {'person_id': row['person_id'], 'thread_id': row['conversation_id'],
                         'external_id': row['external_id'], 'occurred_at': row['occurred_at'],
                         'direction': row['direction'], 'summary': row['body'][:2000]}
                if not db.execute('SELECT 1 FROM relationship_messages WHERE source=? AND source_account_id=? AND external_id=?', ('signal', account, row['external_id'])).fetchone():
                    db.execute('INSERT INTO relationship_messages VALUES (?,?,?,?)', ('signal', account, row['external_id'], json.dumps(event)))
                    linked += 1
        receipt = {'source_account_id': account, 'source_digest': projected['source_digest'],
                   'review_token': projected['review_token'], 'coverage': projected['coverage'],
                   'qualification': projected['qualification'], 'messages': len(projected['rows']),
                   'inserted': inserted, 'linked': linked, 'captured_at': datetime.now(timezone.utc).isoformat()}
        db.execute('INSERT INTO signal_imports VALUES (?,?,?)', (account, projected['source_digest'], json.dumps(receipt)))
    return receipt, True


def imports(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [json.loads(body) for body, in db.execute('SELECT receipt FROM signal_imports ORDER BY rowid DESC')]


def history(store, account):
    with closing(store.connect()) as db, db:
        schema(db)
        return [json.loads(body) for body, in db.execute('SELECT body FROM signal_messages WHERE account=? ORDER BY rowid', (account,))]
