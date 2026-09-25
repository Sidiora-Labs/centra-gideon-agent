"""Durable explicit music composition using the documented ElevenLabs API."""

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from gideon.sdk.credentials import CredentialStore

from .store import DomainError, integer, text

MODELS = ("music_v1", "music_v2", "music_v2_5")
ENDPOINT = "https://api.elevenlabs.io/v1/music"


class MusicGeneration:
    def __init__(self, home, catalog):
        self.home, self.catalog = Path(home), catalog
        self.path = catalog.root / "generation.sqlite3"
        self.credentials = CredentialStore(self.home)
        self.tasks = {}
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def config(self):
        with self._db() as db:
            row = db.execute("SELECT payload FROM config WHERE id=1").fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "enabled": False,
                "model": MODELS[0],
                "credential_name": "",
                "revision": 0,
            }
        )

    def configure(self, data):
        if not isinstance(data, dict) or set(data) != {
            "enabled",
            "model",
            "credential_name",
            "revision",
        }:
            raise DomainError(
                "Configuration requires enabled, model, credential_name and revision"
            )
        if type(data["enabled"]) is not bool or data["model"] not in MODELS:
            raise DomainError("Invalid music engine configuration")
        name = text(data["credential_name"], "credential name", 100)
        revision = integer(data["revision"], "revision", 0, 1000000)
        with self._db() as db:
            row = db.execute("SELECT payload FROM config WHERE id=1").fetchone()
            current = json.loads(row[0])["revision"] if row else 0
            if current != revision:
                raise DomainError(
                    "Engine configuration changed", 409, "revision_conflict"
                )
            result = {**data, "credential_name": name, "revision": revision + 1}
            db.execute(
                "INSERT OR REPLACE INTO config VALUES (1,?)", (json.dumps(result),)
            )
        return result

    def readiness(self):
        config = self.config()
        self.credentials.reload()
        name = config["credential_name"]
        key = (
            self.credentials.resolve(name).secret
            if name and self.credentials.has(name)
            else None
        )
        return {
            "config": config,
            "models": list(MODELS),
            "credential_available": bool(key),
            "ready_to_submit": config["enabled"] and bool(key),
            "remote_status": "unverified",
            "provider": "elevenlabs",
            "min_duration_ms": 3000,
            "max_duration_ms": 600000,
            "instrumental_supported": True,
            "automatic_duration_supported": True,
        }

    def _request(self, data):
        if not isinstance(data, dict) or set(data) != {
            "request_id",
            "track_id",
            "track_revision",
            "prompt",
            "music_length_ms",
            "force_instrumental",
            "license",
        }:
            raise DomainError("Invalid generation fields")
        request = dict(data)
        request["request_id"] = text(data["request_id"], "request ID", 100, True)
        request["track_id"] = text(data["track_id"], "track ID", 100, True)
        request["prompt"] = text(data["prompt"], "prompt", 4100, True)
        request["license"] = text(data["license"], "license statement", 500, True)
        integer(data["track_revision"], "track revision", 1, 1000000000)
        if data["music_length_ms"] is not None:
            integer(data["music_length_ms"], "music length milliseconds", 3000, 600000)
        if type(data["force_instrumental"]) is not bool:
            raise DomainError("Instrumental mode must be boolean")
        return request

    def get(self, request_id):
        with self._db() as db:
            row = db.execute(
                "SELECT payload FROM jobs WHERE id=?", (request_id,)
            ).fetchone()
        if not row:
            raise DomainError("Generation job not found", 404, "not_found")
        return json.loads(row[0])

    def list(self, offset=0, limit=50):
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 100)
        with self._db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM jobs ORDER BY id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def _save(self, job):
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job), job["id"])
            )

    async def submit(self, data):
        request = self._request(data)
        fingerprint = json.dumps(request, sort_keys=True)
        with self._db() as db:
            prior = db.execute(
                "SELECT fingerprint,payload FROM jobs WHERE id=?",
                (request["request_id"],),
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError(
                        "Request ID already used with different input",
                        409,
                        "request_conflict",
                    )
                return json.loads(prior[1])
        track = self.catalog.get("tracks", request["track_id"])
        if track["revision"] != request["track_revision"]:
            raise DomainError(
                "Track changed before generation", 409, "revision_conflict"
            )
        ready = self.readiness()
        if not ready["ready_to_submit"]:
            raise DomainError(
                "Enable the engine and configure an existing named credential",
                503,
                "engine_unavailable",
            )
        job = {
            "id": request["request_id"],
            "status": "queued",
            "request": request,
            "model": ready["config"]["model"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifact_ref": None,
            "error": None,
        }
        with self._db() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?,?,?)",
                (job["id"], fingerprint, json.dumps(job)),
            ).rowcount
            if not inserted:
                prior = db.execute(
                    "SELECT fingerprint,payload FROM jobs WHERE id=?", (job["id"],)
                ).fetchone()
                if prior[0] != fingerprint:
                    raise DomainError(
                        "Request ID already used with different input",
                        409,
                        "request_conflict",
                    )
                return json.loads(prior[1])
        task = asyncio.create_task(self._run(job, ready["config"]["credential_name"]))
        self.tasks[job["id"]] = task
        task.add_done_callback(lambda completed: self.tasks.pop(job["id"], None))
        return job

    async def _run(self, job, credential_name):
        try:
            job["status"] = "running"
            self._save(job)
            request = job["request"]
            payload = {
                key: request[key]
                for key in ("prompt", "music_length_ms", "force_instrumental")
            }
            payload["model_id"] = job["model"]
            self.credentials.reload()
            key = self.credentials.resolve(credential_name).secret
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=900)
            ) as session:
                async with session.post(
                    ENDPOINT,
                    params={"output_format": "mp3_44100_128"},
                    json=payload,
                    headers={"xi-api-key": key},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise DomainError(
                            f"Music provider returned HTTP {response.status}",
                            502,
                            "provider_error",
                        )
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 32 * 1024 * 1024:
                            raise DomainError("Provider audio exceeds artifact limit")
                        chunks.append(chunk)
                    if not size or response.content_type not in (
                        "audio/mpeg",
                        "application/octet-stream",
                    ):
                        raise DomainError("Provider did not return MP3 audio")
                    artifact = self.catalog.artifacts.create_binary(
                        name="Music composition",
                        kind="audio",
                        mime="audio/mpeg",
                        data=b"".join(chunks),
                        source="manual",
                        event_metadata={
                            "provider": "elevenlabs",
                            "model": job["model"],
                            "job_id": job["id"],
                        },
                    )
                    job["artifact_ref"] = {
                        "slug": artifact.slug,
                        "version": artifact.version,
                    }
                    job["provider_song_id"] = response.headers.get("song-id")
                    self._save(job)
            source = {
                "kind": "generated",
                "label": "ElevenLabs music composition",
                "license": request["license"],
                "model": job["model"],
                "job_id": job["id"],
                "attestation": "provider_response",
            }
            self.catalog._attach_verified(
                request["track_id"],
                request["track_revision"],
                job["artifact_ref"],
                source,
            )
            job["status"] = "completed"
            self._save(job)
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["error"] = (
                "Local request cancelled; remote billing/completion is unknown"
            )
            self._save(job)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = (
                str(exc)
                if isinstance(exc, DomainError)
                else "Music generation failed; provider or storage unavailable"
            )
            self._save(job)

    async def cancel(self, request_id):
        job = self.get(request_id)
        task = self.tasks.get(request_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            job = self.get(request_id)
            if job["status"] in ("queued", "running"):
                job["status"] = "cancelled"
                job["error"] = "Cancelled before completion"
                self._save(job)
        elif job["status"] in ("queued", "running"):
            job["status"] = "interrupted"
            job["error"] = "Owner unavailable; remote completion is unknown"
            self._save(job)
        return self.get(request_id)

    async def close(self):
        for request_id in list(self.tasks):
            await self.cancel(request_id)

    def recover(self):
        with self._db() as db:
            for request_id, payload in db.execute(
                "SELECT id,payload FROM jobs"
            ).fetchall():
                job = json.loads(payload)
                if job["status"] in ("queued", "running"):
                    job["status"] = "interrupted"
                    job["error"] = "Runtime restarted; remote completion is unknown"
                    db.execute(
                        "UPDATE jobs SET payload=? WHERE id=?",
                        (json.dumps(job), request_id),
                    )
