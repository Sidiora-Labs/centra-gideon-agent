"""Music catalog whose render references retain canonical audio and measured facts."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import wave
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .store import DomainError, integer, text

FIELDS = {'artists': {'name', 'bio'}, 'albums': {'title', 'artist_id', 'track_ids'}, 'tracks': {'title', 'artist_id', 'notes'}}


class MusicCatalog:
    def __init__(self, root, artifacts):
        self.root, self.artifacts = Path(root), artifacts
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'music_catalog.sqlite3'
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS catalog (kind TEXT, id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _kind(self, kind):
        if kind not in FIELDS:
            raise DomainError('Unknown catalog collection', 404, 'not_found')

    def _read(self, db, kind, entity_id):
        self._kind(kind)
        row = db.execute('SELECT payload FROM catalog WHERE kind=? AND id=?', (kind, entity_id)).fetchone()
        if row is None:
            raise DomainError('Catalog record not found', 404, 'not_found')
        return json.loads(row[0])

    def _write(self, db, kind, item):
        db.execute('INSERT OR REPLACE INTO catalog VALUES (?,?,?)', (kind, item['id'], json.dumps(item)))

    def _fields(self, db, kind, data):
        self._kind(kind)
        if not isinstance(data, dict) or set(data) - FIELDS[kind] - {'revision', 'archived'}:
            raise DomainError('Unknown catalog fields')
        clean = {}
        for key in ('name', 'title', 'bio', 'notes'):
            if key in data:
                clean[key] = text(data[key], key, 200 if key in ('name', 'title') else 10000, key in ('name', 'title'))
        if 'archived' in data:
            if type(data['archived']) is not bool:
                raise DomainError('Archived must be a boolean')
            clean['archived'] = data['archived']
        if 'artist_id' in data:
            artist = text(data['artist_id'], 'artist_id', 100)
            if artist:
                self._read(db, 'artists', artist)
            clean['artist_id'] = artist
        if 'track_ids' in data:
            tracks = data['track_ids']
            if not isinstance(tracks, list) or len(tracks) > 1000 or any(not isinstance(value, str) for value in tracks):
                raise DomainError('Invalid ordered track IDs')
            if len(set(tracks)) != len(tracks):
                raise DomainError('An album cannot repeat a track ID')
            for track_id in tracks:
                self._read(db, 'tracks', track_id)
            clean['track_ids'] = tracks
        return clean

    def create(self, kind, data):
        with self._db() as db:
            fields = self._fields(db, kind, data)
            label = 'name' if kind == 'artists' else 'title'
            if label not in fields or 'revision' in data:
                raise DomainError('A label is required; revision is server managed')
            defaults = {'artists': {'bio': ''}, 'albums': {'artist_id': '', 'track_ids': []},
                        'tracks': {'artist_id': '', 'notes': '', 'renders': [], 'selected_render_id': None}}
            item = {'id': str(uuid4()), 'revision': 1, 'archived': False, **defaults[kind], **fields}
            self._write(db, kind, item)
            return item

    def get(self, kind, entity_id):
        with self._db() as db:
            return self._read(db, kind, entity_id)

    def list(self, kind, *, q='', archived=False, offset=0, limit=50):
        self._kind(kind)
        text(q, 'query', 200)
        integer(offset, 'offset', 0, 1000000)
        integer(limit, 'limit', 1, 100)
        if type(archived) is not bool:
            raise DomainError('Archived filter must be boolean')
        with self._db() as db:
            rows = db.execute("SELECT payload FROM catalog WHERE kind=? AND json_extract(payload,'$.archived')=? AND instr(lower(COALESCE(json_extract(payload,'$.title'),json_extract(payload,'$.name'))),lower(?))>0 ORDER BY id LIMIT ? OFFSET ?", (kind, int(archived), q, limit, offset))
            return [json.loads(row[0]) for row in rows]

    def _revision(self, item, data):
        revision = integer(data.get('revision'), 'revision', 1, 1000000000)
        if item['revision'] != revision:
            raise DomainError('Catalog record changed; reload before editing', 409, 'revision_conflict')
        item['revision'] += 1

    def update(self, kind, entity_id, data):
        with self._db() as db:
            fields = self._fields(db, kind, data)
            item = self._read(db, kind, entity_id)
            self._revision(item, data)
            item.update(fields)
            self._write(db, kind, item)
            return item

    def attach(self, track_id, data):
        if not isinstance(data, dict) or set(data) != {'revision', 'artifact_ref', 'source'}:
            raise DomainError('Render requires revision, artifact_ref and source')
        source, ref = data['source'], data['artifact_ref']
        if not isinstance(source, dict) or source.get('kind') not in ('imported', 'generated'):
            raise DomainError('Declare the actual audio source')
        if source['kind'] == 'generated':
            raise DomainError('Generated-source registration requires an available verified generation job adapter', 503, 'generation_provenance_unavailable')
        if set(source) != {'kind', 'label', 'license'}:
            raise DomainError('Imported source requires kind, label and license; model and job are not inferred')
        source = {'kind': 'imported', 'label': text(source['label'], 'source label', 500, True),
                  'license': text(source['license'], 'license', 500, True), 'model': None, 'job_id': None,
                  'attestation': 'user_supplied'}
        if not isinstance(ref, dict) or set(ref) != {'slug', 'version'}:
            raise DomainError('Canonical artifact slug and version required')
        slug = text(ref['slug'], 'artifact slug', 200, True)
        version = integer(ref['version'], 'artifact version', 1, 1000000)
        artifact = self.artifacts.get(slug, version=version)
        raw = self.artifacts.raw_bytes(slug, version=version) if artifact else None
        if artifact is None or raw is None:
            raise DomainError('Audio artifact version or bytes missing', 404, 'artifact_not_found')
        if artifact.kind != 'audio' or raw[1] != 'audio/wav':
            raise DomainError('Catalog measurement currently supports PCM WAV audio artifacts', 422, 'unsupported_audio')
        try:
            with wave.open(io.BytesIO(raw[0]), 'rb') as audio:
                frames, rate = audio.getnframes(), audio.getframerate()
                decoded = audio.readframes(frames)
                if frames <= 0 or rate <= 0 or len(decoded) != frames * audio.getsampwidth() * audio.getnchannels():
                    raise ValueError('Truncated or empty WAV')
                duration = frames / rate
        except (wave.Error, EOFError, ValueError) as exc:
            raise DomainError('Invalid or truncated PCM WAV', 422, 'invalid_audio') from exc
        with self._db() as db:
            track = self._read(db, 'tracks', track_id)
            for prior in track['renders']:
                if prior['artifact_ref'] == ref:
                    if prior['source'] == source:
                        return track
                    raise DomainError('This artifact version already has immutable provenance', 409, 'render_conflict')
            self._revision(track, data)
            render = {'id': str(uuid4()), 'artifact_ref': ref, 'duration_seconds': duration,
                      'sha256': hashlib.sha256(raw[0]).hexdigest(), 'source': source}
            track['renders'].append(render)
            if track['selected_render_id'] is None:
                track['selected_render_id'] = render['id']
            self._write(db, 'tracks', track)
            return track

    def select(self, track_id, data):
        if not isinstance(data, dict) or set(data) != {'revision', 'render_id'}:
            raise DomainError('Selection requires revision and render_id')
        with self._db() as db:
            track = self._read(db, 'tracks', track_id)
            self._revision(track, data)
            render = next((row for row in track['renders'] if row['id'] == data['render_id']), None)
            if render is None:
                raise DomainError('Render is not attached to this track', 404, 'render_not_found')
            ref = render['artifact_ref']
            if self.artifacts.raw_bytes(ref['slug'], version=ref['version']) is None:
                raise DomainError('Selected audio bytes are missing', 404, 'artifact_not_found')
            track['selected_render_id'] = render['id']
            self._write(db, 'tracks', track)
            return track
