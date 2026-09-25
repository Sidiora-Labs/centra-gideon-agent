"""Beat-grid image and audio composition with real bounded FFmpeg render jobs."""
import asyncio
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import math
from PIL import Image, ImageOps
from .store import DomainError, integer, text


class VideoStore:
    def __init__(self, root, catalog):
        self.root, self.catalog = Path(root), catalog
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'music_video.sqlite3'
        self.tasks = {}
        with self._db() as db:
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def _read(self, db, table, item_id):
        row = db.execute('SELECT payload FROM '+table+' WHERE id=?', (text(item_id, 'id', 200, True),)).fetchone()
        if not row:
            raise DomainError('Music video record not found', 404, 'not_found')
        return json.loads(row[0])

    def _image(self, ref):
        if not isinstance(ref, dict) or set(ref) != {'slug', 'version'}:
            raise DomainError('Image requires canonical slug and version')
        text(ref['slug'], 'slug', 200, True); integer(ref['version'], 'version', 1, 1000000)
        artifact = self.catalog.artifacts.get(ref['slug'], version=ref['version'])
        raw = self.catalog.artifacts.raw_bytes(ref['slug'], version=ref['version']) if artifact else None
        if not artifact or artifact.kind != 'image' or not raw:
            raise DomainError('Canonical scene image missing', 404, 'artifact_not_found')
        try:
            with Image.open(io.BytesIO(raw[0])) as image:
                if image.width * image.height > 16000000:
                    raise ValueError('Image dimensions exceed render limit')
                image.load()
        except Exception as exc:
            raise DomainError('Invalid scene image', 422, 'invalid_image') from exc
        return raw[0]

    def _validate(self, data):
        fields = {'title', 'track_id', 'render_id', 'tempo_bpm', 'offset_seconds', 'scenes'}
        if not isinstance(data, dict) or set(data) != fields:
            raise DomainError('Project requires title,track_id,render_id,tempo_bpm,offset_seconds,scenes')
        title = text(data['title'], 'title', 200, True)
        integer(data['tempo_bpm'], 'tempo_bpm', 20, 300)
        offset = data['offset_seconds']
        if type(offset) not in (int, float) or not math.isfinite(offset) or offset < 0:
            raise DomainError('Invalid audio start offset')
        track = self.catalog.get('tracks', text(data['track_id'], 'track_id', 100, True))
        render = next((row for row in track['renders'] if row['id'] == data['render_id']), None)
        if not render:
            raise DomainError('Audio render not attached to track', 404, 'not_found')
        raw = self.catalog.artifacts.raw_bytes(render['artifact_ref']['slug'], version=render['artifact_ref']['version'])
        if not raw or raw[1] not in ('audio/wav', 'audio/mpeg'):
            raise DomainError('Source audio missing or unsupported', 422, 'unsupported_audio')
        scenes = data['scenes']
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 16:
            raise DomainError('One to sixteen scenes required')
        ids, elapsed, cleaned = set(), 0., []
        for scene in scenes:
            if not isinstance(scene, dict) or set(scene) != {'id', 'image_ref', 'beats'}:
                raise DomainError('Scene requires id,image_ref,beats')
            scene_id = text(scene['id'], 'scene id', 100, True)
            if scene_id in ids:
                raise DomainError('Scene IDs must be unique')
            ids.add(scene_id); integer(scene['beats'], 'beats', 1, 64)
            self._image(scene['image_ref'])
            duration = scene['beats'] * 60 / data['tempo_bpm']
            cleaned.append({**scene, 'start_seconds': elapsed, 'duration_seconds': duration})
            elapsed += duration
        if elapsed > 60 or offset + elapsed > render['duration_seconds'] + .000001:
            raise DomainError('Beat arrangement exceeds recording or sixty seconds')
        return {**data, 'title': title, 'scenes': cleaned, 'duration_seconds': elapsed, 'audio_ref': render['artifact_ref']}

    def create(self, data):
        item = {**self._validate(data), 'id': str(uuid4()), 'revision': 1}
        with self._db() as db:
            db.execute('INSERT INTO projects VALUES (?,?)', (item['id'], json.dumps(item)))
        return item

    def get(self, item_id):
        with self._db() as db:
            return self._read(db, 'projects', item_id)

    def list(self, offset=0, limit=50):
        integer(offset, 'offset', 0, 1000000); integer(limit, 'limit', 1, 100)
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM projects ORDER BY rowid LIMIT ? OFFSET ?', (limit, offset))]

    def update(self, item_id, data):
        if not isinstance(data, dict) or 'revision' not in data:
            raise DomainError('Saved revision required')
        fields = self._validate({key: value for key, value in data.items() if key != 'revision'})
        with self._db() as db:
            item = self._read(db, 'projects', item_id)
            self._revision(item, data['revision'])
            item.update(fields, revision=item['revision']+1)
            db.execute('UPDATE projects SET payload=? WHERE id=?', (json.dumps(item), item_id))
            return item

    def _revision(self, item, revision):
        integer(revision, 'revision', 1, 1000000)
        if revision != item['revision']:
            raise DomainError('Video arrangement changed; reload first', 409, 'revision_conflict')

    def get_job(self, item_id):
        with self._db() as db:
            return self._read(db, 'jobs', item_id)

    def jobs(self):
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100')]

    def _save(self, job):
        with self._db() as db:
            db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(job), job['id']))

    async def submit(self, data):
        if not isinstance(data, dict) or set(data) != {'request_id', 'project_id', 'revision'}:
            raise DomainError('Render requires request_id,project_id,revision')
        text(data['request_id'], 'request_id', 200, True)
        fingerprint = json.dumps(data, sort_keys=True)
        with self._db() as db:
            prior = db.execute('SELECT fingerprint,payload FROM jobs WHERE id=?', (data['request_id'],)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError('Render request ID conflict', 409, 'request_conflict')
                return json.loads(prior[1])
            project = self._read(db, 'projects', data['project_id']); self._revision(project, data['revision'])
            if not shutil.which('ffmpeg'):
                raise DomainError('Local video renderer unavailable', 503, 'renderer_unavailable')
            if db.execute("SELECT count(*) FROM jobs WHERE json_extract(payload,'$.status') IN ('queued','running')").fetchone()[0] >= 2:
                raise DomainError('Two renders are already active', 429, 'render_capacity')
            job = {'id': data['request_id'], 'status': 'queued', 'project_id': project['id'], 'revision': project['revision'], 'snapshot': project, 'artifact_ref': None, 'error': None}
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'], fingerprint, json.dumps(job)))
        task = asyncio.create_task(self._run(job)); self.tasks[job['id']] = task
        task.add_done_callback(lambda _: self.tasks.pop(job['id'], None))
        return job

    async def _run(self, job):
        process = None
        try:
            job['status'] = 'running'; self._save(job)
            item = job['snapshot']
            with tempfile.TemporaryDirectory(dir=self.root, prefix='render-') as directory:
                root = Path(directory); entries = []
                for index, scene in enumerate(item['scenes']):
                    with Image.open(io.BytesIO(self._image(scene['image_ref']))) as image:
                        ImageOps.pad(image.convert('RGB'), (640, 360)).save(root / f'{index}.png')
                    entries.extend([f"file '{index}.png'", f"duration {scene['duration_seconds']:.9f}"])
                entries.append(f"file '{len(item['scenes'])-1}.png'")
                (root / 'scenes.txt').write_text('\n'.join(entries)+'\n')
                ref = item['audio_ref']; audio = self.catalog.artifacts.raw_bytes(ref['slug'], version=ref['version'])
                if not audio:
                    raise DomainError('Source audio unavailable', 404, 'artifact_not_found')
                (root / 'audio').write_bytes(audio[0]); output = root / 'output.mp4'
                process = await asyncio.create_subprocess_exec('ffmpeg', '-v', 'error', '-nostdin', '-threads', '1', '-filter_threads', '1', '-f', 'concat', '-safe', '1', '-i', str(root/'scenes.txt'), '-ss', str(item['offset_seconds']), '-i', str(root/'audio'), '-map', '0:v:0', '-map', '1:a:0', '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-c:a', 'aac', '-t', str(item['duration_seconds']), '-movflags', '+faststart', str(output), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, errors = await asyncio.wait_for(process.communicate(), timeout=90)
                if process.returncode or not output.exists():
                    raise DomainError('Video encoder rejected the source media', 422, 'render_failed')
                artifact = self.catalog.artifacts.create_binary(name=item['title'], data=output.read_bytes(), mime='video/mp4', kind='video', source='manual', event_metadata={'project_id': item['id'], 'revision': str(item['revision'])})
                job['artifact_ref'] = {'slug': artifact.slug, 'version': artifact.version}
                job['status'] = 'completed'; self._save(job)
        except asyncio.CancelledError:
            job['status'] = 'cancelled'; job['error'] = 'Local render cancelled'; self._save(job)
        except Exception as exc:
            job['status'] = 'failed'; job['error'] = str(exc) if isinstance(exc, DomainError) else 'Video renderer or storage unavailable'; self._save(job)
        finally:
            if process and process.returncode is None:
                process.kill(); await process.wait()

    async def cancel(self, request_id):
        job = self.get_job(request_id)
        task = self.tasks.get(request_id)
        if task and not task.done():
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
            job = self.get_job(request_id)
            if job['status'] in ('queued', 'running'):
                job.update(status='cancelled', error='Cancelled before render start'); self._save(job)
        elif job['status'] in ('queued', 'running'):
            raise DomainError('Render is owned by another active service', 409, 'render_owner_unavailable')
        return self.get_job(request_id)

    async def close(self):
        for request_id in list(self.tasks):
            await self.cancel(request_id)

    def recover(self):
        with self._db() as db:
            for request_id, payload in db.execute('SELECT id,payload FROM jobs').fetchall():
                job = json.loads(payload)
                if job['status'] in ('queued', 'running'):
                    job.update(status='interrupted', error='Runtime restarted; submit a new render request to retry')
                    db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(job), request_id))


_SERVICES = {}


def default_store():
    from gideon.core.config.loader import config_dir
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    from .catalog import MusicCatalog
    home = config_dir().resolve()
    if home not in _SERVICES:
        _SERVICES[home] = VideoStore(home / 'capabilities' / 'music', MusicCatalog(home / 'capabilities' / 'music', NativeArtifactProvider(root=home / 'artifacts')))
    return _SERVICES[home]
