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

from .images import ImageService
from .sketches import SketchError, fields, integer


def identity(pid):
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def public(job):
    return {key: value for key, value in job.items() if key not in ("owner_pid", "owner_identity")}


def now():
    return datetime.now(timezone.utc).isoformat()


class MediaJobs:
    def __init__(self, path, sketches, images=None):
        self.path, self.sketches = Path(path), sketches
        self.images = images or ImageService(sketches.artifacts)
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
        if body.get('operation') == 'image_generate':
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

    def finish(self, job_id, result=None, error=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.get(job_id, internal=True)
            if job['status'] not in ('running', 'cancel_requested') or job['owner_pid'] != os.getpid():
                raise SketchError('Job is not owned by this worker', 409)
            status = 'cancelled' if job['status'] == 'cancel_requested' else 'failed' if error else 'succeeded'
            job.update(result=result, error=error, owner_pid=None)
            return self._write(db, job, status, error or 'Renderer finished; any output artifact remains available')

    def recover(self):
        recovered = []
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT body FROM jobs WHERE status IN ('running','cancel_requested')").fetchall()
            for row in rows:
                job = json.loads(row[0])
                if identity(job['owner_pid']) != job['owner_identity'] or not job['owner_identity']:
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
            result = asyncio.run(self.jobs.images.execute(job['input'], job['id'])) if job['operation'] == 'image_generate' else self.jobs.sketches.export(job['sketch_id'], {'revision': job['revision']})
            self.jobs.finish(job['id'], result=result)
        except Exception as exc:
            self.jobs.finish(job['id'], error=str(exc)[:500])
