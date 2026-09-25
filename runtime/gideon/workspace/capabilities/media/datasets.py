import hashlib
import io
import json
import re
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .sketches import SketchError, fields, integer


class DatasetStore:
    def __init__(self, path, images):
        self.path, self.images = path, images
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS datasets (id TEXT, revision INTEGER, body TEXT, PRIMARY KEY(id,revision))')
            db.execute('CREATE TABLE IF NOT EXISTS dataset_requests (request TEXT PRIMARY KEY, fingerprint TEXT, body TEXT)')

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, dataset_id, revision=None):
        with self.db() as db:
            if revision is None:
                row = db.execute('SELECT body FROM datasets WHERE id=? ORDER BY revision DESC LIMIT 1', (dataset_id,)).fetchone()
            else:
                integer(revision, 1, 1000000)
                row = db.execute('SELECT body FROM datasets WHERE id=? AND revision=?', (dataset_id, revision)).fetchone()
        if row is None:
            raise SketchError('Dataset revision not found', 404)
        return json.loads(row[0])

    def list(self):
        with self.db() as db:
            rows = db.execute('SELECT body FROM datasets d WHERE revision=(SELECT MAX(revision) FROM datasets WHERE id=d.id) ORDER BY rowid DESC LIMIT 100').fetchall()
        return {'items': [json.loads(row[0]) for row in rows]}

    def history(self, dataset_id):
        self.get(dataset_id)
        with self.db() as db:
            rows = db.execute('SELECT body FROM datasets WHERE id=? ORDER BY revision DESC', (dataset_id,)).fetchall()
        return {'items': [json.loads(row[0]) for row in rows]}

    def save(self, body, dataset_id=None):
        fields(body, ('title', 'base_model', 'entries', 'request_id', 'revision'), ('title', 'base_model', 'entries', 'request_id'))
        title, base, entries = body['title'], body['base_model'], body['entries']
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise SketchError('Dataset title must contain 1–120 characters')
        if not isinstance(base, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', base):
            raise SketchError('Base model must be an explicit owner/model identifier')
        if not isinstance(body['request_id'], str) or not 1 <= len(body['request_id']) <= 100:
            raise SketchError('Invalid request ID')
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise SketchError('Dataset requires 1–100 captioned images')
        seen = set()
        for entry in entries:
            fields(entry, ('artifact_id', 'version', 'caption'), ('artifact_id', 'version', 'caption'))
            caption = entry['caption']
            if not isinstance(caption, str) or not caption.strip() or len(caption) > 2000 or any(ord(c) < 32 and c not in '\n\t' for c in caption):
                raise SketchError('Each image requires a caption of 1–2000 characters')
            self.images.source(entry['artifact_id'], entry['version'])
            key = (entry['artifact_id'], entry['version'])
            if key in seen:
                raise SketchError('Duplicate dataset image version')
            seen.add(key)
        revision = body.get('revision', 0)
        integer(revision, 0, 1000000)
        if dataset_id is not None and revision < 1:
            raise SketchError('Dataset update requires current revision')
        if dataset_id is None and revision != 0:
            raise SketchError('New dataset revision must be zero')
        fingerprint = hashlib.sha256(json.dumps([dataset_id, body], sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            replay = db.execute('SELECT fingerprint,body FROM dataset_requests WHERE request=?', (body['request_id'],)).fetchone()
            if replay:
                if replay[0] != fingerprint:
                    raise SketchError('Dataset request ID conflict', 409)
                return json.loads(replay[1])
            if dataset_id and self.get(dataset_id)['revision'] != revision:
                raise SketchError('Dataset revision changed', 409)
            result = dict(id=dataset_id or str(uuid4()), revision=revision+1, title=title.strip(), base_model=base, entries=entries, updated_at=datetime.now(timezone.utc).isoformat())
            serialized = json.dumps(result)
            db.execute('INSERT INTO datasets VALUES (?,?,?)', (result['id'], result['revision'], serialized))
            db.execute('INSERT INTO dataset_requests VALUES (?,?,?)', (body['request_id'], fingerprint, serialized))
        return result

    def stage(self, dataset_id, revision, directory):
        document = self.get(dataset_id, revision)
        directory.mkdir(parents=True, exist_ok=True)
        metadata, total = [], 0
        for index, entry in enumerate(document['entries']):
            image = self.images.source(entry['artifact_id'], entry['version']).convert('RGB')
            buffer = io.BytesIO()
            image.save(buffer, 'PNG')
            total += len(buffer.getvalue())
            if total > 64 * 1024 * 1024:
                raise SketchError('Prepared dataset exceeds 64 MiB')
            filename = f'{index:05d}.png'
            (directory / filename).write_bytes(buffer.getvalue())
            metadata.append({'file_name': filename, 'text': entry['caption']})
        (directory / 'metadata.jsonl').write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in metadata)+'\n')
        (directory / 'manifest.json').write_text(json.dumps(document, ensure_ascii=False))
        return document

    def export(self, dataset_id, revision):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        output = io.BytesIO()
        with TemporaryDirectory(prefix='gideon-dataset-') as directory:
            root = Path(directory)
            self.stage(dataset_id, revision, root)
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.iterdir()):
                    archive.write(path, path.name)
        return output.getvalue()
