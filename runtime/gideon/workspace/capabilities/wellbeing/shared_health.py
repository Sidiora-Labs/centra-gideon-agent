"""Versioned native-file exchange with canonical measurement transactions."""
import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from uuid import UUID, uuid4
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .store import MeasurementError, MeasurementStore, instant, normalized, text

SCHEMA = 'gideon.wellbeing-shared'
MAX_BYTES = 8 * 1024 * 1024
FIELDS = ('id', 'revision', 'kind', 'observed_at', 'unit', 'values', 'source', 'notes')


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identifier(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise MeasurementError('Shared identity must be a canonical UUID') from exc
    return value


class SharedHealthStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        self.file = self.path.parent.parent / 'shared' / 'wellbeing.json'
        self.artifacts = NativeArtifactProvider(root=self.path.parent.parent / 'artifacts')
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS shared_health_meta(id INTEGER PRIMARY KEY,store_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_health_links(store_id TEXT NOT NULL,external_id TEXT NOT NULL,remote_revision INTEGER NOT NULL,fingerprint TEXT NOT NULL,record_id TEXT NOT NULL,local_revision INTEGER NOT NULL,PRIMARY KEY(store_id,external_id));
                CREATE TABLE IF NOT EXISTS shared_health_imports(id TEXT PRIMARY KEY,data TEXT NOT NULL);''')
            db.execute('INSERT OR IGNORE INTO shared_health_meta VALUES(1,?)', (str(uuid4()),))

    def _parse(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'content'}:
            raise MeasurementError('Preview requires only content')
        content = payload['content']
        if not isinstance(content, str) or len(content.encode()) > MAX_BYTES:
            raise MeasurementError('Shared document must be UTF-8 text at most8MiB', 413, 'too_large')
        try:
            document = json.loads(content.lstrip('\ufeff'))
        except (ValueError, RecursionError) as exc:
            raise MeasurementError('Malformed shared JSON') from exc
        if not isinstance(document, dict) or set(document) != {'schema', 'version', 'store_id', 'records'} or document['schema'] != SCHEMA or type(document['version']) is not int or document['version'] != 1:
            raise MeasurementError('Unsupported shared health schema')
        identifier(document['store_id'])
        if not isinstance(document['records'], list) or len(document['records']) > 10000:
            raise MeasurementError('Shared records must be an array of at most10000')
        seen = set()
        for row in document['records']:
            if not isinstance(row, dict) or set(row) != set(FIELDS):
                raise MeasurementError('Shared record fields are invalid')
            identifier(row['id'])
            if row['id'] in seen:
                raise MeasurementError('Duplicate shared record identity')
            seen.add(row['id'])
            if type(row['revision']) is not int or not 1 <= row['revision'] <= 1000000000:
                raise MeasurementError('Shared revision must be a positive integer')
            instant(row['observed_at'])
            normalized(row['kind'], row['unit'], row['values'])
            text(row['source'], 'source', 256)
            text(row['notes'], 'notes', 4000, True)
        return document, content.encode()

    def _plan(self, db, document, raw):
        plan, counts = [], dict(create=0, correct=0, unchanged=0)
        for row in document['records']:
            link = db.execute('SELECT remote_revision,fingerprint,record_id,local_revision FROM shared_health_links WHERE store_id=? AND external_id=?', (document['store_id'], row['id'])).fetchone()
            fingerprint = digest(encoded(row))
            if link is None:
                action = 'create'
            else:
                current = self._get(db, link[2])
                if row['revision'] < link[0] or row['revision'] == link[0] and fingerprint != link[1]:
                    raise MeasurementError('Shared revision is stale or has conflicting content', 409, 'conflict')
                if row['revision'] == link[0]:
                    action = 'unchanged'
                else:
                    if current['revision'] != link[3]:
                        raise MeasurementError('Canonical record changed locally; resolve before importing', 409, 'conflict')
                    if row['kind'] != current['kind'] or row['source'] != current['source']:
                        raise MeasurementError('Shared kind and source cannot change', 409, 'conflict')
                    action = 'correct'
            counts[action] += 1
            plan.append(dict(id=row['id'], action=action))
        return dict(preview_id=digest(raw), store_id=document['store_id'], records=plan, counts=counts)

    def preview(self, payload):
        document, raw = self._parse(payload)
        with self.connection() as db:
            db.execute('BEGIN')
            return self._plan(db, document, raw)

    def commit(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'content', 'preview_id', 'request_id'}:
            raise MeasurementError('Commit requires content, preview_id and request_id')
        request_id = text(payload['request_id'], 'request_id', 128)
        document, raw = self._parse({'content': payload['content']})
        sha = digest(raw)
        if payload['preview_id'] != sha:
            raise MeasurementError('Shared document changed since preview', 409, 'conflict')
        fingerprint = json.dumps(['shared-health-import', sha])
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT payload,result FROM requests WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise MeasurementError('Request ID already used', 409, 'conflict')
                return json.loads(previous[1])
            plan = self._plan(db, document, raw)
            slug, filename = 'shared-health-' + sha, 'original@' + sha + '.json'
            descriptor = json.dumps(dict(schema=SCHEMA, sha256=sha, bytes=len(raw), filename=filename), sort_keys=True)
            artifact = self.artifacts.get(slug, version=1)
            if artifact is None:
                artifact = self.artifacts.create(name='Native shared health source', content=descriptor, kind='document', source='import', slug=slug, readonly=True)
            if artifact.content != descriptor or not self.artifacts.store_version_file(slug, filename, raw):
                raise MeasurementError('Shared original could not be saved', 503, 'unavailable')
            results = []
            for row, step in zip(document['records'], plan['records']):
                link = db.execute('SELECT record_id,local_revision FROM shared_health_links WHERE store_id=? AND external_id=?', (document['store_id'], row['id'])).fetchone()
                if step['action'] == 'unchanged':
                    result = self._get(db, link[0])
                else:
                    fields = ('observed_at', 'unit', 'values', 'notes') if link else ('kind', 'observed_at', 'unit', 'values', 'source', 'notes')
                    mutation = {key: row[key] for key in fields}
                    mutation['request_id'] = 'shared-' + digest(encoded([document['store_id'], row]))
                    if link:
                        mutation['revision'] = link[1]
                    result = self._write_in(db, link[0] if link else None, mutation)
                    db.execute('INSERT OR REPLACE INTO shared_health_links VALUES(?,?,?,?,?,?)', (document['store_id'], row['id'], row['revision'], digest(encoded(row)), result['id'], result['revision']))
                results.append(result)
            receipt = dict(import_id=sha, store_id=document['store_id'], counts=plan['counts'], records=results, artifact=dict(slug=slug, version=1, filename=filename, sha256=sha))
            value = json.dumps(receipt)
            db.execute('INSERT OR IGNORE INTO shared_health_imports VALUES(?,?)', (sha, value))
            db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, fingerprint, value))
            return receipt

    def _read_file(self):
        if self.file.parent.is_symlink():
            raise MeasurementError('Shared directory symlinks are unsupported', 409, 'conflict')
        if self.file.is_symlink():
            raise MeasurementError('Shared file symlinks are unsupported', 409, 'conflict')
        try:
            if self.file.stat().st_size > MAX_BYTES:
                raise MeasurementError('Shared file exceeds8MiB', 413, 'too_large')
            raw = self.file.read_bytes()
            content = raw.decode('utf-8')
        except FileNotFoundError as exc:
            raise MeasurementError('Shared file is missing', 404, 'not_found') from exc
        except (OSError, UnicodeError) as exc:
            raise MeasurementError('Shared file is unavailable or not UTF-8', 503, 'unavailable') from exc
        self._parse({'content': content})
        return raw

    def status(self):
        try:
            raw = self._read_file()
            document, _ = self._parse({'content': raw.decode()})
            return dict(state='ready', sha256=digest(raw), bytes=len(raw), records=len(document['records']), store_id=document['store_id'])
        except MeasurementError as exc:
            return dict(state='missing' if exc.status == 404 else 'unavailable' if exc.status == 503 else 'invalid', error=str(exc))

    def preview_file(self):
        return self.preview({'content': self._read_file().decode()})

    def commit_file(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'preview_id', 'request_id'}:
            raise MeasurementError('File commit requires only preview_id and request_id')
        return self.commit(dict(payload, content=self._read_file().decode()))

    @contextmanager
    def _lock(self):
        if self.file.parent.is_symlink():
            raise MeasurementError('Shared directory symlinks are unsupported', 409, 'conflict')
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with (self.file.parent / '.wellbeing.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def publish(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'request_id', 'expected_sha256'}:
            raise MeasurementError('Publish requires request_id and expected_sha256')
        request_id = text(payload['request_id'], 'request_id', 128)
        expected = payload['expected_sha256']
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected)):
            raise MeasurementError('expected_sha256 must be null or lowercase SHA-256')
        fingerprint = json.dumps(['shared-health-publish', expected])
        with self._lock(), self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT payload,result FROM requests WHERE id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError('Request ID already used', 409, 'conflict')
                return json.loads(prior[1])
            status = self.status()
            if status['state'] not in ('missing', 'ready'):
                raise MeasurementError('Present shared file cannot be overwritten: ' + status['state'], 409, 'conflict')
            store_id = db.execute('SELECT store_id FROM shared_health_meta WHERE id=1').fetchone()[0]
            rows = [json.loads(row[0]) for row in db.execute('SELECT r.data FROM revisions r WHERE r.revision=(SELECT MAX(s.revision) FROM revisions s WHERE s.id=r.id) ORDER BY r.id')]
            records = [{key: row[key] for key in FIELDS} for row in rows]
            raw = encoded(dict(schema=SCHEMA, version=1, store_id=store_id, records=records))
            self._parse({'content': raw.decode()})
            if status.get('sha256') not in (payload['expected_sha256'], digest(raw)):
                raise MeasurementError('Shared file changed; refresh before publishing', 409, 'conflict')
            temp = self.file.with_name('.wellbeing-' + str(uuid4()) + '.tmp')
            try:
                with temp.open('xb') as handle:
                    os.chmod(temp, 0o600)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.file)
            finally:
                temp.unlink(missing_ok=True)
            for row in records:
                db.execute('INSERT OR REPLACE INTO shared_health_links VALUES(?,?,?,?,?,?)', (store_id, row['id'], row['revision'], digest(encoded(row)), row['id'], row['revision']))
            receipt = dict(sha256=digest(raw), bytes=len(raw), store_id=store_id, records=len(records))
            db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, fingerprint, json.dumps(receipt)))
            return receipt

    def download(self):
        return self._read_file()
