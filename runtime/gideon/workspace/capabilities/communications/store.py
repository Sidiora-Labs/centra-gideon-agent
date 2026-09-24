from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RINGS = ('support', 'core', 'tribe', 'village', 'external')
PERSON_FIELDS = {'name', 'identities', 'ring', 'cadence_days', 'notes'}
TOUCH_FIELDS = {'source', 'external_id', 'occurred_at', 'direction', 'summary'}


class PeopleError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise PeopleError(f'Invalid {name}')
    value = value.strip()
    if required and not value:
        raise PeopleError(f'{name} is required')
    return value


def fields(data, allowed):
    if not isinstance(data, dict) or set(data) - allowed:
        raise PeopleError('Unknown or invalid fields')


def instant(value):
    value = text(value, 'occurred_at', 64, True)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise PeopleError('occurred_at requires an ISO timestamp with offset') from None


def person_values(data):
    fields(data, PERSON_FIELDS)
    result = {'name': text(data.get('name'), 'name', 200, True),
              'notes': text(data.get('notes', ''), 'notes', 10000),
              'ring': data.get('ring', 'tribe'), 'cadence_days': data.get('cadence_days', 30)}
    if result['ring'] not in RINGS:
        raise PeopleError('Invalid ring')
    if type(result['cadence_days']) is not int or not 1 <= result['cadence_days'] <= 3650:
        raise PeopleError('cadence_days must be between 1 and 3650')
    identities = data.get('identities', [])
    if not isinstance(identities, list) or len(identities) > 100:
        raise PeopleError('Invalid identities')
    normalized = []
    for identity in identities:
        fields(identity, {'kind', 'value'})
        kind = identity.get('kind')
        value = text(identity.get('value'), 'identity', 320, True)
        if kind == 'email':
            value = value.casefold()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                raise PeopleError('Invalid email identity')
        elif kind == 'phone':
            value = re.sub(r'[ ()-]', '', value)
            if not re.fullmatch(r'\+[1-9][0-9]{6,14}', value):
                raise PeopleError('Phone identity requires international +country format')
        elif kind != 'handle':
            raise PeopleError('Unknown identity kind')
        row = {'kind': kind, 'value': value}
        if row not in normalized:
            normalized.append(row)
    result['identities'] = normalized
    return result


def care(person, touchpoints, zone='UTC', now=None):
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise PeopleError('Unknown timezone') from None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise PeopleError('Current time requires timezone')
    past = [datetime.fromisoformat(t['occurred_at']) for t in touchpoints
            if datetime.fromisoformat(t['occurred_at']) <= current]
    last = max(past) if past else None
    days = (current.astimezone(tz).date() - last.astimezone(tz).date()).days if last else None
    state = 'excluded' if person['ring'] == 'external' else (
        'missing' if days is None else 'overdue' if days > person['cadence_days'] else 'current')
    return {'state': state, 'last_contact': last.isoformat() if last else None,
            'days_since': days, 'days_overdue': max(0, days - person['cadence_days']) if days is not None else None}


class PeopleStore:
    def __init__(self, root: Path | None = None):
        if root is None:
            from gideon.core.config.loader import config_dir
            root = config_dir() / 'capabilities' / 'communications'
        self.path = Path(root) / 'people.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS people
                (id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS touchpoints
                (id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id),
                 source TEXT NOT NULL, external_id TEXT NOT NULL, body TEXT NOT NULL,
                 UNIQUE(source, external_id)); PRAGMA user_version=1;''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def get(self, person_id):
        with closing(self.connect()) as db:
            row = db.execute('SELECT body,revision FROM people WHERE id=?', (person_id,)).fetchone()
        if row is None:
            raise PeopleError('Person not found', 404)
        return {**json.loads(row[0]), 'revision': row[1]}

    def people(self):
        with closing(self.connect()) as db:
            return [{**json.loads(body), 'revision': revision} for body, revision in
                    db.execute('SELECT body,revision FROM people ORDER BY id')]

    def save(self, data, person_id=None):
        fields(data, PERSON_FIELDS | ({'revision'} if person_id else set()))
        revision = data.get('revision')
        if person_id and (type(revision) is not int or revision < 1):
            raise PeopleError('revision is required')
        values = person_values({k: v for k, v in data.items() if k != 'revision'})
        person_id = person_id or uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision FROM people WHERE id=?', (person_id,)).fetchone()
            if revision is not None and old is None:
                raise PeopleError('Person not found', 404)
            if old and revision != old[0]:
                raise PeopleError('Person changed; reload before saving', 409)
            for body, other_id in db.execute('SELECT body,id FROM people'):
                if other_id != person_id and any(i in json.loads(body)['identities'] for i in values['identities']):
                    raise PeopleError('Identity already belongs to another person; resolve ambiguity explicitly', 409)
            values.update(id=person_id)
            next_revision = old[0] + 1 if old else 1
            db.execute('INSERT INTO people VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision',
                       (person_id, json.dumps(values), next_revision))
        return {**values, 'revision': next_revision}

    def touchpoints(self, person_id):
        self.get(person_id)
        with closing(self.connect()) as db:
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT body FROM touchpoints WHERE person_id=?', (person_id,))]
        return sorted(rows, key=lambda t: (t['occurred_at'], t['id']), reverse=True)

    def record(self, person_id, data):
        fields(data, TOUCH_FIELDS)
        values = {'person_id': person_id, 'source': text(data.get('source'), 'source', 80, True),
                  'external_id': text(data.get('external_id'), 'external_id', 500, True),
                  'occurred_at': instant(data.get('occurred_at')), 'direction': data.get('direction'),
                  'summary': text(data.get('summary', ''), 'summary', 2000)}
        if values['direction'] not in ('inbound', 'outbound', 'mutual'):
            raise PeopleError('Invalid direction')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM people WHERE id=?', (person_id,)).fetchone():
                raise PeopleError('Person not found', 404)
            old = db.execute('SELECT body FROM touchpoints WHERE source=? AND external_id=?',
                             (values['source'], values['external_id'])).fetchone()
            if old:
                existing = json.loads(old[0])
                if {k: v for k, v in existing.items() if k != 'id'} != values:
                    raise PeopleError('Source identity already recorded with different content', 409)
                return existing, False
            values['id'] = uuid4().hex
            db.execute('INSERT INTO touchpoints VALUES (?,?,?,?,?)',
                       (values['id'], person_id, values['source'], values['external_id'], json.dumps(values)))
        return values, True
