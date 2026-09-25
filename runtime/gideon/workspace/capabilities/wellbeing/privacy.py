"""Local subject consent and encrypted identity facts; explicit reveal only."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from gideon.automation.workflows.project_archive import encrypt_archive, decrypt_archive, ArchiveRefused
from .store import MeasurementError, text

SCOPES = {'vault', 'reveal', 'broker_scan', 'broker_submit', 'twin_share'}
TYPES = {'legal_name', 'email', 'phone', 'address', 'birth_date', 'tax_id', 'passport', 'other'}
SENSITIVE = {'tax_id', 'passport'}


def data(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def now():
    return datetime.now(timezone.utc).isoformat()


def fields(payload, required, optional=()):
    if not isinstance(payload, dict) or set(payload) - set(required) - set(optional) or set(required) - set(payload):
        raise MeasurementError('Missing or unsupported privacy fields')


def passphrase(payload):
    value = text(payload.get('passphrase'), 'passphrase', 1024)
    if len(value) < 12:
        raise MeasurementError('Passphrase requires at least12 characters')
    return value


class PrivacyStore:
    def __init__(self, home):
        self.path = Path(home) / 'capabilities' / 'privacy.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS privacy_subjects(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS privacy_consents(subject_id TEXT NOT NULL,scope TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(subject_id,scope,revision));
                CREATE TABLE IF NOT EXISTS privacy_facts(id TEXT NOT NULL,revision INTEGER NOT NULL,subject_id TEXT NOT NULL,data TEXT NOT NULL,cipher BLOB NOT NULL,PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS privacy_audit(id TEXT PRIMARY KEY,subject_id TEXT NOT NULL,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS privacy_requests(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload BLOB NOT NULL,result TEXT NOT NULL);''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _subject(self, db, identity):
        row = db.execute('SELECT data FROM privacy_subjects WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise MeasurementError('Privacy subject not found', 404, 'not_found')
        return json.loads(row[0])

    def _fact(self, db, identity):
        row = db.execute('SELECT data,cipher FROM privacy_facts WHERE id=? ORDER BY revision DESC LIMIT 1', (identity,)).fetchone()
        if row is None:
            raise MeasurementError('Private fact not found', 404, 'not_found')
        return json.loads(row[0]), bytes(row[1])

    def _allowed(self, db, subject, scope):
        self._subject(db, subject)
        row = db.execute('SELECT data FROM privacy_consents WHERE subject_id=? AND scope=? ORDER BY revision DESC LIMIT 1', (subject, scope)).fetchone()
        return bool(row and json.loads(row[0])['granted'])

    def allowed(self, subject, scope):
        if scope not in SCOPES:
            raise MeasurementError('Unknown consent scope')
        with self.connection() as db:
            return self._allowed(db, subject, scope)

    def _require(self, db, subject, scope):
        if not self._allowed(db, subject, scope):
            raise MeasurementError('Explicit subject consent required for ' + scope, 403, 'consent_required')

    def _audit(self, db, subject, operation, **details):
        record = dict(id=str(uuid4()), subject_id=subject, operation=operation, at=now(), **details)
        db.execute('INSERT INTO privacy_audit VALUES(?,?,?)', (record['id'], subject, json.dumps(record)))

    def _prior(self, db, request_id, kind, payload, password=None):
        text(request_id, 'request_id', 128)
        row = db.execute('SELECT kind,payload,result FROM privacy_requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            return None
        if row[0] != kind:
            raise MeasurementError('Request ID already used', 409, 'conflict')
        try:
            original = decrypt_archive(bytes(row[1]), password) if password is not None else bytes(row[1])
        except ArchiveRefused as exc:
            raise MeasurementError('Private request could not be authenticated', 403, 'authentication_failed') from exc
        if original != data(payload):
            raise MeasurementError('Request ID already used', 409, 'conflict')
        return json.loads(row[2])

    def _receipt(self, db, request_id, kind, payload, result, password=None):
        encoded = encrypt_archive(data(payload), password) if password is not None else data(payload)
        db.execute('INSERT INTO privacy_requests VALUES(?,?,?,?)', (request_id, kind, encoded, json.dumps(result)))
        return result

    def create_subject(self, payload):
        fields(payload, ('request_id', 'alias', 'relationship', 'source'))
        text(payload['alias'], 'alias', 80)
        text(payload['source'], 'source', 256)
        if payload['relationship'] not in ('self', 'household', 'other'):
            raise MeasurementError('Invalid subject relationship')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = self._prior(db, payload['request_id'], 'subject', payload)
            if prior:
                return prior
            row = dict(id=str(uuid4()), alias=payload['alias'], relationship=payload['relationship'], source=payload['source'], created_at=now())
            db.execute('INSERT INTO privacy_subjects VALUES(?,?)', (row['id'], json.dumps(row)))
            self._audit(db, row['id'], 'subject_created')
            return self._receipt(db, payload['request_id'], 'subject', payload, row)

    def list_subjects(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM privacy_subjects ORDER BY rowid')]

    def get_subject(self, identity):
        with self.connection() as db:
            return self._subject(db, identity)

    def consent(self, subject, payload):
        fields(payload, ('request_id', 'revision', 'scope', 'granted', 'method'))
        if not isinstance(payload['scope'], str) or payload['scope'] not in SCOPES or type(payload['granted']) is not bool or type(payload['revision']) is not int or payload['revision'] < 0:
            raise MeasurementError('Invalid consent scope, grant or revision')
        text(payload['method'], 'method', 256)
        fingerprint = dict(payload, subject_id=subject)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._subject(db, subject)
            prior = self._prior(db, payload['request_id'], 'consent', fingerprint)
            if prior:
                return prior
            revision = db.execute('SELECT COALESCE(MAX(revision),0) FROM privacy_consents WHERE subject_id=? AND scope=?', (subject, payload['scope'])).fetchone()[0]
            if revision != payload['revision']:
                raise MeasurementError('Consent changed; reload before recording', 409, 'conflict')
            row = dict(subject_id=subject, scope=payload['scope'], revision=revision + 1, granted=payload['granted'], method=payload['method'], recorded_at=now())
            db.execute('INSERT INTO privacy_consents VALUES(?,?,?,?)', (subject, row['scope'], row['revision'], json.dumps(row)))
            self._audit(db, subject, 'consent_recorded', scope=row['scope'], granted=row['granted'], revision=row['revision'])
            return self._receipt(db, payload['request_id'], 'consent', fingerprint, row)

    def consents(self, subject):
        with self.connection() as db:
            self._subject(db, subject)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM privacy_consents WHERE subject_id=? ORDER BY rowid', (subject,))]

    def create_fact(self, subject, payload):
        fields(payload, ('request_id', 'type', 'label', 'value', 'passphrase', 'source', 'use_for_scans'))
        return self._write_fact(subject, None, payload)

    def correct_fact(self, identity, payload):
        fields(payload, ('request_id', 'revision', 'value', 'passphrase'), ('label', 'archived', 'use_for_scans'))
        return self._write_fact(None, identity, payload)

    def _write_fact(self, subject, identity, payload):
        password = passphrase(payload)
        text(payload['value'], 'value', 8000)
        fingerprint = dict(subject_id=subject, fact_id=identity, **{key: value for key, value in payload.items() if key != 'passphrase'})
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if identity:
                row, cipher = self._fact(db, identity)
                subject = row['subject_id']
            self._require(db, subject, 'vault')
            prior = self._prior(db, payload['request_id'], 'fact', fingerprint, password)
            if prior:
                return prior
            if identity:
                if type(payload['revision']) is not int or payload['revision'] != row['revision']:
                    raise MeasurementError('Private fact changed; reload before correcting', 409, 'conflict')
                self._decrypt(row, cipher, password)
                row.update({key: payload[key] for key in ('label', 'archived', 'use_for_scans') if key in payload})
                row['revision'] += 1
            else:
                row = dict(id=str(uuid4()), subject_id=subject, revision=1, type=payload['type'], label=payload['label'], source=payload['source'], use_for_scans=payload['use_for_scans'], archived=False, created_at=now())
            if not isinstance(row['type'], str) or row['type'] not in TYPES or type(row['archived']) is not bool or type(row['use_for_scans']) is not bool:
                raise MeasurementError('Invalid private fact type or flags')
            if row['type'] in SENSITIVE and row['use_for_scans']:
                raise MeasurementError('Sensitive identifiers cannot be used for scans')
            text(row['label'], 'label', 100)
            text(row['source'], 'source', 256)
            row.update(masked_value='••••', updated_at=now())
            plain = dict(subject_id=subject, id=row['id'], revision=row['revision'], value=payload['value'])
            cipher = encrypt_archive(data(plain), password)
            db.execute('INSERT INTO privacy_facts VALUES(?,?,?,?,?)', (row['id'], row['revision'], subject, json.dumps(row), cipher))
            self._audit(db, subject, 'fact_written', fact_id=row['id'], revision=row['revision'])
            return self._receipt(db, payload['request_id'], 'fact', fingerprint, row, password)

    def _decrypt(self, row, cipher, password):
        try:
            plain = json.loads(decrypt_archive(cipher, password))
        except (ArchiveRefused, ValueError, UnicodeError) as exc:
            raise MeasurementError('Private fact could not be authenticated', 403, 'authentication_failed') from exc
        if not isinstance(plain, dict) or any(plain.get(key) != row[key] for key in ('id', 'subject_id', 'revision')):
            raise MeasurementError('Private fact identity authentication failed', 403, 'authentication_failed')
        return plain['value']

    def list_facts(self, subject):
        with self.connection() as db:
            self._subject(db, subject)
            return [json.loads(row[0]) for row in db.execute('SELECT f.data FROM privacy_facts f WHERE subject_id=? AND f.revision=(SELECT MAX(s.revision) FROM privacy_facts s WHERE s.id=f.id) ORDER BY f.rowid', (subject,))]

    def get_fact(self, identity):
        with self.connection() as db:
            return self._fact(db, identity)[0]

    def history_fact(self, identity):
        with self.connection() as db:
            self._fact(db, identity)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM privacy_facts WHERE id=? ORDER BY revision', (identity,))]

    def reveal(self, identity, payload):
        fields(payload, ('passphrase', 'reason'))
        password = passphrase(payload)
        text(payload['reason'], 'reason', 256)
        error, value = None, None
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row, cipher = self._fact(db, identity)
            try:
                self._require(db, row['subject_id'], 'reveal')
                if row['archived']:
                    raise MeasurementError('Archived private fact cannot be revealed', 409, 'conflict')
                value = self._decrypt(row, cipher, password)
            except MeasurementError as exc:
                error = exc
            self._audit(db, row['subject_id'], 'fact_reveal', fact_id=identity, revision=row['revision'], reason=payload['reason'], outcome='denied' if error else 'revealed')
        if error:
            raise error
        return dict(id=identity, revision=row['revision'], value=value)

    def audit(self, subject):
        with self.connection() as db:
            self._subject(db, subject)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM privacy_audit WHERE subject_id=? ORDER BY rowid', (subject,))]
