"""Source-bound speech jobs using the existing voice and artifact contracts."""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from uuid import uuid4
from weakref import WeakValueDictionary

from gideon.integrations.tts.registry import active_voice_params, route_synthesis
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .graph import identifier, revision
from .store import Conflict, NotFound

_services = WeakValueDictionary()


def get_narration_jobs(store):
    key = str(store.path.resolve())
    if key not in _services:
        service = NarrationJobs(store)
        _services[key] = service
        return service
    return _services[key]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def voice_digest(params):
    fields = ("voice", "speech_voice", "speed", "profile_id", "seed", "instruct", "ref_text", "design_params", "locked")
    values = {key: params.get(key) for key in fields}
    values["provider"] = params["provider"].name
    reference = params.get("ref_audio")
    values["reference"] = hashlib.sha256(Path(reference).read_bytes()).hexdigest() if reference else ""
    return digest(values)


def audio_mime(data):
    if not data or len(data) > 16 * 1024 * 1024:
        raise ValueError("speech output is empty or exceeds 16 MiB")
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:3] == b"ID3" or (len(data) > 2 and data[0] == 255 and data[1] & 224 == 224):
        return "audio/mpeg"
    raise ValueError("speech provider returned an unsupported audio format")


class NarrationJobs:
    def __init__(self, store):
        self.store = store
        self.artifacts = NativeArtifactProvider(store.path.parent.parent / "artifacts")
        self.tasks = {}
        with store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS narrations(id TEXT PRIMARY KEY, session_id TEXT, request_id TEXT, input TEXT, body TEXT, UNIQUE(session_id,request_id))")
            for key, body in db.execute("SELECT id,body FROM narrations").fetchall():
                job = json.loads(body)
                if job["status"] in ("queued", "running"):
                    job.update(status="interrupted", error="Narration was interrupted by a restart; request again.")
                    db.execute("UPDATE narrations SET body=? WHERE id=?", (json.dumps(job), key))

    def get(self, key):
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM narrations WHERE id=?", (identifier(key),)).fetchone()
            if row is None:
                raise NotFound("narration not found")
            return json.loads(row[0])

    def update(self, key, **changes):
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM narrations WHERE id=?", (key,)).fetchone()
            job = json.loads(row[0])
            job.update(changes)
            db.execute("UPDATE narrations SET body=? WHERE id=?", (json.dumps(job), key))
            return job

    def start(self, session_id, body):
        if not isinstance(body, dict) or set(body) != {"revision", "request_id"}:
            raise ValueError("narration requires revision and request_id")
        expected, request_id = revision(body["revision"]), identifier(body["request_id"])
        with self.store.connection() as db:
            session = self.store._session(db, session_id)
            old = db.execute("SELECT input,body FROM narrations WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
            if old:
                if old[0] != digest(body):
                    raise Conflict("narration request identifier was used with different input")
                return json.loads(old[1])
            if session["revision"] != expected:
                raise Conflict("session revision changed")
            if len(self.tasks) >= 4:
                raise Conflict("four narration jobs are already active")
            view = self.store._view(db, session)
            source = {"story_id": session["story_id"], "story_revision": session["story_revision"], "node_id": view["node"]["id"], "text": view["node"]["text"]}
            key = uuid4().hex
            job = {"id": key, "session_id": session_id, "story_id": source["story_id"], "story_revision": source["story_revision"],
                   "node_id": source["node_id"], "source_hash": digest(source), "voice_hash": "", "status": "queued",
                   "artifact_slug": "", "artifact_version": None, "audio_hash": "", "error": "", "audio_url": f"/api/capabilities/experience/narrations/{key}/audio"}
            db.execute("INSERT INTO narrations VALUES(?,?,?,?,?)", (key, session_id, request_id, digest(body), json.dumps(job)))
        task = asyncio.create_task(self.produce(key, source["text"]))
        self.tasks[key] = task
        task.add_done_callback(lambda _: self.tasks.pop(key, None))
        return job

    def audio(self, key):
        job = self.get(key)
        if job["status"] != "ready":
            raise Conflict("narration audio is not ready")
        result = self.artifacts.raw_bytes(job["artifact_slug"], version=job["artifact_version"])
        if result is None or hashlib.sha256(result[0]).hexdigest() != job["audio_hash"]:
            raise NotFound("narration audio is missing or changed; request again")
        if audio_mime(result[0]) != result[1]:
            raise ValueError("narration artifact is not the expected audio format")
        return result

    async def produce(self, key, prose):
        try:
            self.update(key, status="running")
            params = active_voice_params(surface="channel:webui")
            if params is None or not params.get("enabled") or not await params["provider"].can_synthesize(params.get("voice", "")):
                self.update(key, status="unavailable", error="No enabled speech provider is ready. Configure Speech settings and request again.")
                return
            job = self.update(key, voice_hash=voice_digest(params))
            with self.store.connection() as db:
                candidates = [json.loads(r[0]) for r in db.execute("SELECT body FROM narrations WHERE id!=?", (key,))]
            for previous in candidates:
                if previous["status"] == "ready" and (previous["source_hash"], previous["voice_hash"]) == (job["source_hash"], job["voice_hash"]):
                    try:
                        self.audio(previous["id"])
                    except NotFound:
                        continue
                    self.update(key, status="ready", **{name: previous[name] for name in ("artifact_slug", "artifact_version", "audio_hash")})
                    return
            with tempfile.TemporaryDirectory(prefix="gideon-narration-") as temporary:
                output = str(Path(temporary) / "speech.mp3")
                result = await route_synthesis(params, prose, output_path=output)
                if not result or not Path(result).is_file() or not Path(result).resolve().is_relative_to(Path(temporary)):
                    raise ValueError("speech provider produced no owned audio file")
                data = Path(result).read_bytes()
                mime = audio_mime(data)
                artifact = self.artifacts.create_binary(name=f"{job.get('source_kind', 'Story narration')} {job['node_id']}", data=data, mime=mime, kind="audio", source="manual", tags=[job.get("source_kind", "story-narration")], description=f"Story {job['story_id']} revision {job['story_revision']}; node {job['node_id']}; source {job['source_hash']}")
                self.update(key, status="ready", artifact_slug=artifact.slug, artifact_version=artifact.version, audio_hash=hashlib.sha256(data).hexdigest())
        except asyncio.CancelledError:
            self.update(key, status="cancelled", error="Narration cancelled.")
            raise
        except Exception:
            self.update(key, status="failed", error="Speech generation failed; no audio was published. Check Speech settings and retry.")

    async def cancel(self, key):
        job = self.get(key)
        task = self.tasks.get(key)
        if job["status"] in ("queued", "running"):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            job = self.update(key, status="cancelled", error="Narration cancelled.")
        return job

    async def close(self):
        for task in tuple(self.tasks.values()):
            task.cancel()
        for key in list(self.tasks):
            await self.cancel(key)
