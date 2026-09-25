import asyncio
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.extensions.apps.background import BackgroundWorker

from .sprites import SpriteService
from .episodes import EpisodeStore
from .timelines import TimelineStore
from .videos import VideoService
from .cleanup import CleanupService
from .datasets import DatasetStore
from .training import DiffusersTrainer
from .images import ImageService
from .animations import AnimationService
from .downloads import SourceDownloader
from .sketches import SketchError, fields, integer


def identity(pid):
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def public(job):
    return {key: value for key, value in job.items() if key not in ("owner_pid", "owner_identity", "child_pid", "child_identity")}


def now():
    return datetime.now(timezone.utc).isoformat()


class MediaJobs:
    def __init__(self, path, sketches, images=None, training_allowed=True, videos=None):
        self.path, self.sketches = Path(path), sketches
        self.images = images or ImageService(sketches.artifacts)
        self.cleanup = CleanupService(self.images)
        self.animations = AnimationService(sketches.artifacts)
        self.downloads = SourceDownloader(sketches.artifacts)
        self.sprites = SpriteService(self.path.parent / "sprites.sqlite3", self.images)
        self.videos = videos or VideoService(self.images)
        self.timelines = TimelineStore(self.path.parent / "timelines.sqlite3", self.videos)
        self.episodes = EpisodeStore(self.path.parent / "episodes.sqlite3", self.videos, self.timelines)
        self.datasets = DatasetStore(self.path.parent / 'datasets.sqlite3', self.images)
        self.trainer = DiffusersTrainer(sketches.artifacts.root.parent, self.datasets, allowed=training_allowed)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT UNIQUE, fingerprint TEXT, status TEXT, body TEXT)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, job_id, internal=False):
        with self.db() as db:
            row = db.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise SketchError('Media job not found', 404)
        return json.loads(row[0]) if internal else public(json.loads(row[0]))

    def list(self):
        with self.db() as db:
            rows = db.execute('SELECT body FROM jobs ORDER BY rowid DESC LIMIT 100').fetchall()
        return {'items': [public(json.loads(row[0])) for row in rows]}

    def _write(self, db, job, status, detail):
        job.update(status=status, updated_at=now(), state_revision=job['state_revision']+1)
        job['events'].append(dict(status=status, at=job['updated_at'], detail=detail, attempt=job['attempt'], result=job['result'], error=job['error']))
        db.execute('UPDATE jobs SET status=?,body=? WHERE id=?', (status, json.dumps(job), job['id']))
        return public(job)

    def submit(self, body):
        if not isinstance(body, dict):
            raise SketchError('Expected an object')
        image_request = None
        if body.get('operation') == 'source_download':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.downloads.prepare(body['input'])
        elif body.get('operation') == 'code_animation_generate':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.animations.prepare(body['input'])
        elif body.get('operation') in ('sprite_generate', 'sprite_compile'):
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.sprites.prepare_generate(body['input']) if body['operation'] == 'sprite_generate' else self.sprites.prepare_compile(body['input'])
        elif body.get('operation') == 'episode_render':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.episodes.prepare(body['input'])
        elif body.get('operation') == 'timeline_render':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.timelines.prepare(body['input'])
        elif body.get('operation') == 'video_generate':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.videos.prepare(body['input'])
        elif body.get('operation') == 'image_cleanup':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.cleanup.prepare(body['input'])
        elif body.get('operation') == 'lora_train':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.trainer.prepare(body['input'])
        elif body.get('operation') == 'image_generate':
            fields(body, ('operation', 'request_id', 'input'), ('operation', 'request_id', 'input'))
            image_request = self.images.prepare(body['input'])
        else:
            fields(body, ('operation', 'sketch_id', 'revision', 'request_id'), ('operation', 'sketch_id', 'revision', 'request_id'))
            if body['operation'] != 'sketch_export':
                raise SketchError('Unsupported media operation')
            integer(body['revision'], 1, 1000000)
            sketch = self.sketches.get(body['sketch_id'])
            if sketch['revision'] != body['revision']:
                raise SketchError('Sketch revision changed', 409)
        if not isinstance(body['request_id'], str) or not 1 <= len(body['request_id']) <= 100:
            raise SketchError('Invalid request ID')
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint,body FROM jobs WHERE request=?', (body['request_id'],)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise SketchError('Request ID conflicts with a prior job', 409)
                return public(json.loads(row[1]))
            timestamp = now()
            job = dict(id=str(uuid4()), operation=body['operation'], sketch_id=body.get('sketch_id'), revision=body.get('revision'), input=image_request, status='queued',
                       attempt=0, state_revision=1, owner_pid=None, owner_identity=None, result=None, error=None, created_at=timestamp, updated_at=timestamp,
                       events=[dict(status='queued', at=timestamp, detail='Waiting for the media worker')])
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (job['id'], body['request_id'], fingerprint, 'queued', json.dumps(job)))
        return public(job)

    def cancel(self, job_id, body):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.get(job_id, internal=True)
            self.guard(job, body)
            if job['status'] not in ('queued', 'running'):
                return public(job)
            return self._write(db, job, 'cancelled' if job['status'] == 'queued' else 'cancel_requested', 'Cancellation requested; completed output is retained')

    def retry(self, job_id, body):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.get(job_id, internal=True)
            self.guard(job, body)
            if job['status'] == 'queued':
                return public(job)
            if job['status'] not in ('failed', 'cancelled'):
                raise SketchError('Only failed or cancelled jobs can retry', 409)
            if job['attempt'] >= 20:
                raise SketchError('Job attempt limit reached')
            job.update(error=None, owner_pid=None, result=None)
            return self._write(db, job, 'queued', 'User requested retry')

    def guard(self, job, body):
        fields(body, ("state_revision",), ("state_revision",))
        integer(body["state_revision"], 1, 1000000)
        if body["state_revision"] != job["state_revision"]:
            raise SketchError("Job state changed", 409)

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT body FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            job = json.loads(row[0])
            job.update(owner_pid=os.getpid(), owner_identity=identity(os.getpid()), attempt=job['attempt']+1)
            return self._write(db, job, 'running', 'Media worker claimed the job')

    def attach_child(self, job_id, pid):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.get(job_id, internal=True)
            if job['owner_pid'] != os.getpid():
                raise SketchError('Job is not owned by this worker', 409)
            job.update(child_pid=pid, child_identity=identity(pid))
            db.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(job), job_id))

    def progress(self, job_id, value):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.get(job_id, internal=True)
            if job['owner_pid'] != os.getpid():
                raise SketchError('Progress is not owned by this worker', 409)
            job['progress'] = value
            db.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(job), job_id))

    def finish(self, job_id, result=None, error=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.get(job_id, internal=True)
            if job['status'] not in ('running', 'cancel_requested') or job['owner_pid'] != os.getpid():
                raise SketchError('Job is not owned by this worker', 409)
            status = 'cancelled' if job['status'] == 'cancel_requested' else 'failed' if error else 'succeeded'
            job.update(result=result, error=error, owner_pid=None, child_pid=None, child_identity=None)
            return self._write(db, job, status, error or 'Renderer finished; any output artifact remains available')

    def recover(self):
        recovered = []
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT body FROM jobs WHERE status IN ('running','cancel_requested')").fetchall()
            for row in rows:
                job = json.loads(row[0])
                if identity(job['owner_pid']) != job['owner_identity'] or not job['owner_identity']:
                    if job.get('child_pid') and identity(job['child_pid']) == job.get('child_identity'):
                        try:
                            os.killpg(job['child_pid'], 9)
                        except ProcessLookupError:
                            pass
                    job.update(error='Media worker exited before recording completion', owner_pid=None, owner_identity=None)
                    recovered.append(self._write(db, job, 'failed', job['error']))
        return recovered


class MediaWorker(BackgroundWorker):
    poll_interval = 1.0

    def __init__(self, jobs):
        self.jobs = jobs

    def run_once(self, ctx):
        if ctx.should_stop():
            return
        self.jobs.recover()
        job = self.jobs.claim()
        if not job:
            return
        if ctx.should_stop() or self.jobs.get(job['id'])['status'] == 'cancel_requested':
            self.jobs.cancel(job['id'], {'state_revision': self.jobs.get(job['id'])['state_revision']})
            self.jobs.finish(job['id'])
            return
        try:
            if job['operation'] == 'source_download':
                result = asyncio.run(self.jobs.downloads.execute(job['input'], job['id'], lambda: ctx.should_stop() or self.jobs.get(job['id'])['status'] == 'cancel_requested'))
            elif job['operation'] == 'code_animation_generate':
                result = asyncio.run(self.jobs.animations.execute(job['input'], job['id']))
            elif job['operation'] in ('sprite_generate', 'sprite_compile'):
                stopped = lambda: ctx.should_stop() or self.jobs.get(job['id'])['status'] == 'cancel_requested'
                progress = lambda value: self.jobs.progress(job['id'], value)
                result = asyncio.run(self.jobs.sprites.generate(job['input'], job['id'], stopped, progress)) if job['operation'] == 'sprite_generate' else self.jobs.sprites.compile(job['input'], job['id'], stopped, progress)
            elif job['operation'] == 'episode_render':
                result = asyncio.run(self.jobs.episodes.execute(job['input'], job['id'], lambda: ctx.should_stop() or self.jobs.get(job['id'])['status'] == 'cancel_requested', lambda pid: self.jobs.attach_child(job['id'], pid), lambda value: self.jobs.progress(job['id'], value)))
            elif job['operation'] == 'timeline_render':
                result = self.jobs.timelines.execute(job['input'], job['id'], lambda: ctx.should_stop() or self.jobs.get(job['id'])['status'] == 'cancel_requested', lambda pid: self.jobs.attach_child(job['id'], pid), lambda value: self.jobs.progress(job['id'], value))
            elif job['operation'] == 'video_generate':
                result = asyncio.run(self.jobs.videos.execute(job['input'], job['id']))
            elif job['operation'] == 'image_cleanup':
                result = self.jobs.cleanup.execute(job['input'], job['id'])
            elif job['operation'] == 'lora_train':
                result = self.jobs.trainer.execute(job['input'], job['id'], lambda: ctx.should_stop() or self.jobs.get(job['id'])['status'] == 'cancel_requested', lambda pid: self.jobs.attach_child(job['id'], pid))
            else:
                result = asyncio.run(self.jobs.images.execute(job['input'], job['id'])) if job['operation'] == 'image_generate' else self.jobs.sketches.export(job['sketch_id'], {'revision': job['revision']})
            self.jobs.finish(job['id'], result=result)
        except Exception as exc:
            self.jobs.finish(job['id'], error=('Training failed; inspect local diagnostics' if job['operation'] == 'lora_train' and not isinstance(exc, SketchError) else str(exc)[:500]))
