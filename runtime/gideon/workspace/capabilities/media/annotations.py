import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .library import text
from .sketches import SketchError, fields, integer


class AnnotationStore:
    def __init__(self, path, artifacts):
        self.path, self.artifacts = Path(path), artifacts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS revisions (artifact TEXT, version INTEGER, revision INTEGER, body TEXT, PRIMARY KEY(artifact,version,revision))')
            db.execute('CREATE TABLE IF NOT EXISTS requests (artifact TEXT, version INTEGER, request TEXT, fingerprint TEXT, revision INTEGER, PRIMARY KEY(artifact,version,request))')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def source(self, artifact_id, version):
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', artifact_id):
            raise SketchError('Invalid artifact ID')
        integer(version, 1, 1000000)
        artifact = self.artifacts.get(artifact_id, version=version)
        raw = self.artifacts.raw_bytes(artifact_id, version=version) if artifact and artifact.kind in ('image', 'video') else None
        return artifact if raw and raw[0] else None

    def get(self, artifact_id, version, revision=None):
        source = self.source(artifact_id, version)
        with self.db() as db:
            if revision is None:
                row = db.execute('SELECT body FROM revisions WHERE artifact=? AND version=? ORDER BY revision DESC LIMIT 1', (artifact_id, version)).fetchone()
            else:
                integer(revision, 1, 1000000)
                row = db.execute('SELECT body FROM revisions WHERE artifact=? AND version=? AND revision=?', (artifact_id, version, revision)).fetchone()
        if row:
            body = json.loads(row[0])
            body['source_available'] = source is not None
            return body
        if not source or revision is not None:
            raise SketchError('Annotation source or revision not found', 404)
        return dict(artifact_id=artifact_id, artifact_version=version, source_kind=source.kind, source_available=True,
                    revision=0, updated_at=None, annotations=[], attribution=dict(creator='', license='', source_url=''), duration_verified=False)

    def history(self, artifact_id, version, offset=0, limit=50):
        integer(offset, 0, 1000000)
        integer(limit, 1, 100)
        self.get(artifact_id, version)
        with self.db() as db:
            total = db.execute('SELECT COUNT(*) FROM revisions WHERE artifact=? AND version=?', (artifact_id, version)).fetchone()[0]
            rows = db.execute('SELECT body FROM revisions WHERE artifact=? AND version=? ORDER BY revision DESC LIMIT ? OFFSET ?', (artifact_id, version, limit, offset)).fetchall()
        return {'items': [dict(revision=value['revision'], updated_at=value['updated_at'], annotation_count=len(value['annotations'])) for value in map(lambda row: json.loads(row[0]), rows)], 'total': total, 'offset': offset, 'limit': limit}

    def save(self, artifact_id, version, body):
        fields(body, ('revision', 'request_id', 'annotations', 'attribution'), ('revision', 'request_id', 'annotations', 'attribution'))
        integer(body['revision'], 0, 1000000)
        if not isinstance(body['request_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', body['request_id']):
            raise SketchError('Invalid request ID')
        source = self.source(artifact_id, version)
        if not source:
            raise SketchError('Original media version is unavailable', 404)
        entries = body['annotations']
        if not isinstance(entries, list) or len(entries) > 100:
            raise SketchError('At most 100 annotations are allowed')
        ids = set()
        for entry in entries:
            fields(entry, ('id', 'text', 'region', 'time_seconds'), ('id', 'text'))
            identity = text(entry['id'], 100, empty=False)
            if identity in ids:
                raise SketchError('Duplicate annotation ID')
            ids.add(identity)
            note = entry['text']
            if not isinstance(note, str) or not note.strip() or len(note) > 4000 or any(ord(c) < 32 and c not in '\n\t' for c in note):
                raise SketchError('Invalid annotation text')
            if 'region' in entry:
                region = entry['region']
                if source.kind != 'image' or not isinstance(region, list) or len(region) != 4:
                    raise SketchError('Region requires an image and four coordinates')
                if any(type(n) not in (float, int) or not math.isfinite(n) or not 0 <= n <= 1 for n in region):
                    raise SketchError('Invalid normalized region')
                x, y, width, height = region
                if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
                    raise SketchError('Region exceeds the image')
            if 'time_seconds' in entry:
                value = entry['time_seconds']
                if source.kind != 'video' or type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 604800:
                    raise SketchError('Timestamp requires a video and bounded nonnegative seconds')
        attribution = body['attribution']
        fields(attribution, ('creator', 'license', 'source_url'), ('creator', 'license', 'source_url'))
        text(attribution['creator'], 200)
        text(attribution['license'], 200)
        url = text(attribution['source_url'], 2000)
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise SketchError('Invalid attribution URL') from exc
        if url and (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password):
            raise SketchError('Attribution source must be an HTTP URL without credentials')
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT fingerprint,revision FROM requests WHERE artifact=? AND version=? AND request=?', (artifact_id, version, body['request_id'])).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise SketchError('Request ID conflicts with a prior change', 409)
                return self.get(artifact_id, version, prior[1])
            latest = db.execute('SELECT MAX(revision) FROM revisions WHERE artifact=? AND version=?', (artifact_id, version)).fetchone()[0] or 0
            if body['revision'] != latest:
                raise SketchError('Annotations changed; reload before saving', 409)
            record = dict(artifact_id=artifact_id, artifact_version=version, source_kind=source.kind, source_available=True,
                          revision=latest+1, updated_at=datetime.now(timezone.utc).isoformat(), annotations=entries,
                          attribution=attribution, duration_verified=False)
            db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (artifact_id, version, latest+1, json.dumps(record)))
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?)', (artifact_id, version, body['request_id'], fingerprint, latest+1))
        return record
