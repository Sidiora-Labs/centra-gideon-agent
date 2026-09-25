from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .imports import digest
from .store import PeopleError, care, fields, instant, text

BATCH_FIELDS = {'source', 'source_account_id', 'captured_at', 'coverage_start', 'coverage_end', 'incoming_complete', 'outgoing_complete', 'messages'}
MESSAGE_FIELDS = {'person_id', 'thread_id', 'external_id', 'occurred_at', 'direction', 'summary'}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS relationship_messages (source TEXT NOT NULL, source_account_id TEXT NOT NULL, external_id TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(source,source_account_id,external_id))')
    db.execute('CREATE TABLE IF NOT EXISTS relationship_coverage (source TEXT NOT NULL, source_account_id TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(source,source_account_id))')
    db.execute('CREATE TABLE IF NOT EXISTS relationship_batches (digest TEXT PRIMARY KEY, receipt TEXT NOT NULL)')


def normalize(data):
    fields(data, BATCH_FIELDS)
    result = {key: text(data.get(key), key, 100, True) for key in ('source', 'source_account_id')}
    result.update({key: instant(data.get(key)) for key in ('captured_at', 'coverage_start', 'coverage_end')})
    if not result['coverage_start'] <= result['coverage_end'] <= result['captured_at']:
        raise PeopleError('Coverage must start before it ends and end by captured_at')
    for key in ('incoming_complete', 'outgoing_complete'):
        if type(data.get(key)) is not bool:
            raise PeopleError(f'{key} must be an explicit boolean')
        result[key] = data[key]
    messages = data.get('messages')
    if not isinstance(messages, list) or len(messages) > 1000:
        raise PeopleError('A batch accepts at most 1000 messages')
    result['messages'] = []
    seen = set()
    for raw in messages:
        fields(raw, MESSAGE_FIELDS)
        row = {key: text(raw.get(key), key, 500 if key == 'external_id' else 100, True)
               for key in ('person_id', 'thread_id', 'external_id')}
        row.update(occurred_at=instant(raw.get('occurred_at')), direction=raw.get('direction'),
                   summary=text(raw.get('summary', ''), 'summary', 2000))
        if row['direction'] not in ('inbound', 'outbound'):
            raise PeopleError('Message direction must be inbound or outbound')
        if not result['coverage_start'] <= row['occurred_at'] <= result['coverage_end']:
            raise PeopleError('Message falls outside declared coverage')
        if row['external_id'] in seen:
            raise PeopleError('Duplicate message identity in batch')
        seen.add(row['external_id'])
        result['messages'].append(row)
    result['messages'].sort(key=lambda row: row['external_id'])
    return result


def ingest(store, data):
    batch = normalize(data)
    batch_digest = digest(batch)
    source, account = batch['source'], batch['source_account_id']
    with closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        previous = db.execute('SELECT receipt FROM relationship_batches WHERE digest=?', (batch_digest,)).fetchone()
        if previous:
            return json.loads(previous[0]), False
        coverage = db.execute('SELECT body FROM relationship_coverage WHERE source=? AND source_account_id=?', (source, account)).fetchone()
        if coverage and json.loads(coverage[0])['captured_at'] >= batch['captured_at']:
            raise PeopleError('Coverage observation is stale or changed at the same timestamp', 409)
        inserted = 0
        for row in batch['messages']:
            if not db.execute('SELECT id FROM people WHERE id=?', (row['person_id'],)).fetchone():
                raise PeopleError('Message references an unknown person', 404)
            old = db.execute('SELECT body FROM relationship_messages WHERE source=? AND source_account_id=? AND external_id=?',
                             (source, account, row['external_id'])).fetchone()
            if old:
                if json.loads(old[0]) != row:
                    raise PeopleError('Message identity has conflicting content', 409)
                continue
            db.execute('INSERT INTO relationship_messages VALUES (?,?,?,?)', (source, account, row['external_id'], json.dumps(row)))
            inserted += 1
        coverage = {k: v for k, v in batch.items() if k != 'messages'}
        db.execute('INSERT INTO relationship_coverage VALUES (?,?,?) ON CONFLICT(source,source_account_id) DO UPDATE SET body=excluded.body',
                   (source, account, json.dumps(coverage)))
        receipt = {'id': uuid4().hex, 'batch_digest': batch_digest, 'inserted': inserted, 'coverage': coverage,
                   'qualification': 'recorded_evidence_only'}
        db.execute('INSERT INTO relationship_batches VALUES (?,?)', (batch_digest, json.dumps(receipt)))
    return receipt, True


def thread_verdict(messages, coverage, now):
    latest = max(messages, key=lambda row: (row['occurred_at'], row['external_id']))
    if coverage is None:
        return 'unknown', 'No coverage observation', latest
    captured = datetime.fromisoformat(coverage['captured_at'])
    end = datetime.fromisoformat(coverage['coverage_end'])
    if captured > now or now - captured > timedelta(hours=24) or now - end > timedelta(hours=24):
        return 'unknown', 'Coverage is stale or in the future', latest
    if not coverage['incoming_complete'] or not coverage['outgoing_complete']:
        return 'unknown', 'Both incoming and outgoing coverage are required', latest
    if any(not coverage['coverage_start'] <= row['occurred_at'] <= coverage['coverage_end'] for row in messages):
        return 'unknown', 'Retained thread extends outside the covered window', latest
    directions = {row['direction'] for row in messages if row['occurred_at'] == latest['occurred_at']}
    if len(directions) > 1:
        return 'unknown', 'Latest message ordering is ambiguous', latest
    return ('unanswered' if latest['direction'] == 'inbound' else 'answered'), '', latest


def report(store, zone='UTC', now=None):
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise PeopleError('Unknown timezone') from None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise PeopleError('Current time requires timezone')
    with closing(store.connect()) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        messages = list(db.execute('SELECT source,source_account_id,body FROM relationship_messages')) if 'relationship_messages' in tables else []
        coverage = {(s, a): json.loads(body) for s, a, body in db.execute('SELECT source,source_account_id,body FROM relationship_coverage')} if 'relationship_coverage' in tables else {}
    groups = {}
    for source, account, body in messages:
        row = json.loads(body)
        groups.setdefault((source, account, row['person_id'], row['thread_id']), []).append(row)
    threads = []
    for (source, account, person_id, thread_id), rows in groups.items():
        observation = coverage.get((source, account))
        state, reason, latest = thread_verdict(rows, observation, current)
        threads.append({'source': source, 'source_account_id': account, 'person_id': person_id, 'thread_id': thread_id,
                        'state': state, 'reason': reason, 'as_of': observation['coverage_end'] if observation else None,
                        'latest': latest, 'message_count': len(rows), 'qualification': 'recorded_evidence_only'})
    people = []
    for person in store.people():
        points = store.touchpoints(person['id'])
        points += [json.loads(body) for _, _, body in messages if json.loads(body)['person_id'] == person['id']]
        people.append({'person': person, 'care': care(person, points, zone, current)})
    return {'people': people, 'threads': sorted(threads, key=lambda row: (row['person_id'], row['source'], row['source_account_id'], row['thread_id'])),
            'timezone': zone, 'qualification': 'recorded_evidence_only'}
