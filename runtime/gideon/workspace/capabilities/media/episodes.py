import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from .sketches import SketchError, fields, integer
from .timelines import TimelineStore, number
from .videos import probe_video


class EpisodeStore(TimelineStore):
    def __init__(self, path, videos, timelines):
        super().__init__(path, videos)
        self.timelines = timelines
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS scenes (job TEXT, position INTEGER, body TEXT, PRIMARY KEY(job,position))"
            )

    def validate(self, body):
        fields(
            body,
            (
                "title",
                "width",
                "height",
                "fps",
                "aspect_ratio",
                "scenes",
                "request_id",
                "revision",
            ),
            ("title", "width", "height", "fps", "aspect_ratio", "scenes", "request_id"),
        )
        if (
            not isinstance(body["title"], str)
            or not 1 <= len(body["title"].strip()) <= 120
        ):
            raise SketchError("Episode title requires 1–120 characters")
        for key in ("width", "height"):
            integer(body[key], 2, 1920)
            if body[key] % 2:
                raise SketchError("Episode dimensions must be even")
        integer(body["fps"], 1, 60)
        if not isinstance(body["aspect_ratio"], str) or len(body["aspect_ratio"]) > 20:
            raise SketchError("Invalid episode aspect ratio")
        scenes = body["scenes"]
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 20:
            raise SketchError("Episodes require 1–20 scenes")
        total = 0
        previous_partial = False
        for index, scene in enumerate(scenes):
            fields(
                scene,
                (
                    "prompt",
                    "duration_seconds",
                    "mode",
                    "allow_fallback",
                    "artifact_id",
                    "version",
                ),
                ("prompt", "duration_seconds", "mode", "allow_fallback"),
            )
            if (
                not isinstance(scene["prompt"], str)
                or not scene["prompt"].strip()
                or len(scene["prompt"]) > 4000
            ):
                raise SketchError("Each scene requires a prompt of 1–4000 characters")
            total += number(scene["duration_seconds"], 0.05, 60)
            if scene["mode"] not in (
                "establish",
                "continue",
                "reuse",
            ) or not isinstance(scene["allow_fallback"], bool):
                raise SketchError("Invalid scene mode or fallback policy")
            if scene["mode"] == "continue" and previous_partial:
                raise SketchError(
                    "Continuation after a trimmed reused clip requires a separately trimmed canonical artifact"
                )
            previous_partial = False
            if index == 0 and scene["mode"] == "continue":
                raise SketchError("The first scene must establish or reuse a clip")
            if scene["mode"] == "reuse":
                if "artifact_id" not in scene or "version" not in scene:
                    raise SketchError("Reuse requires a pinned canonical clip")
                raw = self.videos.source(scene["artifact_id"], scene["version"])
                with TemporaryDirectory(prefix="gideon-episode-check-") as temporary:
                    path = Path(temporary) / "clip"
                    path.write_bytes(raw[0])
                    clip_duration = probe_video(path)["duration_seconds"]
                    previous_partial = scene["duration_seconds"] < clip_duration - 0.001
                    if scene["duration_seconds"] > clip_duration + 0.001:
                        raise SketchError("Reused scene exceeds clip duration")
            elif "artifact_id" in scene or "version" in scene:
                raise SketchError("Generated scenes cannot override their input clip")
            elif scene["duration_seconds"] < 1:
                raise SketchError("Generated scenes require at least one second")
        return number(total, 0.05, 300)

    def save(self, body, episode_id=None):
        duration = self.validate(body)
        revision = integer(body.get("revision", 0), 0, 1000000)
        if (episode_id is None) != (revision == 0):
            raise SketchError("Existing episode requires current revision")
        request_id = body["request_id"]
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise SketchError("Invalid episode request ID")
        fingerprint = hashlib.sha256(
            json.dumps([episode_id, body], sort_keys=True).encode()
        ).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute(
                "SELECT fingerprint,body FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if replay:
                if replay[0] != fingerprint:
                    raise SketchError("Episode request ID conflict", 409)
                return json.loads(replay[1])
            if episode_id and self.get(episode_id)["revision"] != revision:
                raise SketchError("Episode revision changed", 409)
            value = {
                key: body[key]
                for key in ("title", "width", "height", "fps", "aspect_ratio", "scenes")
            }
            value.update(
                id=episode_id or str(uuid4()),
                revision=revision + 1,
                duration=duration,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            encoded = json.dumps(value)
            db.execute(
                "INSERT INTO timelines VALUES (?,?,?)",
                (value["id"], value["revision"], encoded),
            )
            db.execute(
                "INSERT INTO requests VALUES (?,?,?)",
                (request_id, fingerprint, encoded),
            )
        return value

    def prepare(self, body):
        fields(body, ("episode_id", "revision"), ("episode_id", "revision"))
        self.get(body["episode_id"], body["revision"])
        selected = self.videos.selector()
        return dict(
            body, selection=f"{selected[0].name}:{selected[1]}" if selected else ""
        )

    def scenes(self, job_id):
        with self.db() as db:
            rows = db.execute(
                "SELECT body FROM scenes WHERE job=? ORDER BY position", (job_id,)
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows]}

    def record(self, job_id, position, state, **values):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM scenes WHERE job=? AND position=?", (job_id, position)
            ).fetchone()
            value = (
                json.loads(row[0])
                if row
                else dict(position=position, attempts=0, events=[], result=None)
            )
            if state == "running":
                value["attempts"] += 1
            value.update(
                values, status=state, updated_at=datetime.now(timezone.utc).isoformat()
            )
            value["events"].append(
                dict(
                    status=state,
                    at=value["updated_at"],
                    fallback=value.get("fallback", False),
                    error=value.get("error"),
                )
            )
            db.execute(
                "INSERT OR REPLACE INTO scenes VALUES (?,?,?)",
                (job_id, position, json.dumps(value)),
            )
        return value

    async def lint(self, document, selection):
        if all(scene["mode"] == "reuse" for scene in document["scenes"]):
            return
        selected = self.videos.selector()
        if not selected:
            raise SketchError(
                "No video provider is configured for generated episode scenes", 503
            )
        provider, model_id = selected
        if selection != f"{provider.name}:{model_id}":
            raise SketchError("Episode video selection changed; submit again", 409)
        models = await asyncio.wait_for(provider.list_models(), timeout=5)
        model = next((item for item in models if item.name == model_id), None)
        if model is None:
            raise SketchError("Episode model is absent from its catalog")
        for scene in document["scenes"]:
            if scene["mode"] == "reuse":
                continue
            request = dict(
                duration_seconds=scene["duration_seconds"],
                aspect_ratio=document["aspect_ratio"],
                controls={},
            )
            if scene["mode"] == "continue" and not scene["allow_fallback"]:
                request["continuation_artifact_id"] = "predecessor"
            self.videos.validate_model(request, model)

    async def execute(
        self, request, job_id, stopped, attached, progress=lambda value: None
    ):
        document = self.get(request["episode_id"], request["revision"])
        saved = {row["position"]: row for row in self.scenes(job_id)["items"]}
        pending = dict(
            document,
            scenes=[
                scene
                for index, scene in enumerate(document["scenes"])
                if saved.get(index, {}).get("status") != "succeeded"
            ],
        )
        await self.lint(pending, request["selection"])
        segments, predecessor, predecessor_partial = [], None, False
        for index, scene in enumerate(document["scenes"]):
            if stopped():
                raise SketchError(
                    "Episode cancelled; completed clips are retained", 409
                )
            if scene["mode"] == "continue" and predecessor_partial:
                message = "Continuation requires the visible predecessor endpoint; save a trimmed canonical clip first"
                self.record(
                    job_id,
                    index,
                    "failed",
                    mode=scene["mode"],
                    predecessor=predecessor,
                    error=message,
                )
                raise SketchError(message)
            prior = saved.get(index)
            if prior and prior["status"] == "succeeded":
                result = prior["result"]
                self.videos.source(result["artifact_id"], result["version"])
            else:
                self.record(
                    job_id,
                    index,
                    "running",
                    mode=scene["mode"],
                    predecessor=predecessor,
                    error=None,
                )
                try:
                    if scene["mode"] == "reuse":
                        result = dict(
                            artifact_id=scene["artifact_id"],
                            version=scene["version"],
                            duration_seconds=scene["duration_seconds"],
                        )
                        self.videos.source(result["artifact_id"], result["version"])
                    else:
                        body = dict(
                            prompt=scene["prompt"],
                            duration_seconds=scene["duration_seconds"],
                            aspect_ratio=document["aspect_ratio"],
                        )
                        if scene["mode"] == "continue":
                            body.update(
                                continuation_artifact_id=predecessor["artifact_id"],
                                continuation_version=predecessor["version"],
                            )
                        prepared = self.videos.prepare(body)
                        if prepared["selection"] != request["selection"]:
                            raise SketchError("Episode model selection changed", 409)
                        try:
                            result = await self.videos.execute(
                                prepared, job_id + "-scene-" + str(index)
                            )
                        except SketchError:
                            if (
                                scene["mode"] != "continue"
                                or not scene["allow_fallback"]
                                or stopped()
                            ):
                                raise
                            self.record(
                                job_id,
                                index,
                                "fallback",
                                fallback=True,
                                detail="Continuation failed; establishing a fresh shot by explicit policy",
                            )
                            body.pop("continuation_artifact_id")
                            body.pop("continuation_version")
                            result = await self.videos.execute(
                                self.videos.prepare(body),
                                job_id + "-scene-" + str(index) + "-fallback",
                            )
                    self.record(job_id, index, "succeeded", result=result, error=None)
                except Exception as exc:
                    message = (
                        str(exc)
                        if isinstance(exc, SketchError)
                        else "Episode scene generation failed"
                    )
                    self.record(job_id, index, "failed", error=message)
                    raise SketchError(message, getattr(exc, "status", 503)) from exc
            segments.append(
                dict(
                    artifact_id=result["artifact_id"],
                    version=result["version"],
                    kind="video",
                    start=0,
                    duration=min(scene["duration_seconds"], result["duration_seconds"]),
                )
            )
            predecessor = dict(
                artifact_id=result["artifact_id"], version=result["version"]
            )
            predecessor_partial = (
                scene["duration_seconds"] < result["duration_seconds"] - 0.001
            )
            progress((index + 1) / (len(document["scenes"]) + 1))
        timeline = self.timelines.save(
            dict(
                title=document["title"],
                width=document["width"],
                height=document["height"],
                fps=document["fps"],
                segments=segments,
                overlays=[],
                audio=[],
                request_id="episode-" + job_id,
            )
        )
        result = await asyncio.to_thread(
            self.timelines.execute,
            dict(timeline_id=timeline["id"], revision=timeline["revision"]),
            job_id,
            stopped,
            attached,
            lambda value: progress((len(segments) + value) / (len(segments) + 1)),
        )
        return dict(
            result,
            episode_id=document["id"],
            episode_revision=document["revision"],
            timeline_id=timeline["id"],
            timeline_revision=timeline["revision"],
        )
