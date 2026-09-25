import asyncio
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .cleanup import CleanupService
from .sketches import SketchError, fields, integer


def selected_video():
    from gideon.integrations.video_gen.registry import active_video_gen

    return active_video_gen()


def media_tools():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SketchError(
            "FFmpeg and ffprobe are required for verified video processing", 503
        )


def probe_video(path):
    media_tools()
    header = Path(path).read_bytes()[:16]
    if header[4:8] != b"ftyp" and not header.startswith(b"\x1aE\xdf\xa3"):
        raise SketchError("Video requires an MP4 or WebM container")
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-protocol_whitelist",
                "file,pipe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name:format=duration,format_name",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=20,
            check=True,
        )
        info = json.loads(completed.stdout)
        stream = info["streams"][0]
        duration = float(info["format"]["duration"])
        width, height = stream["width"], stream["height"]
        if (
            not math.isfinite(duration)
            or not 0 < duration <= 3600
            or not 1 <= width <= 4096
            or not 1 <= height <= 4096
        ):
            raise ValueError()
        return {
            "width": width,
            "height": height,
            "duration_seconds": duration,
            "codec": stream["codec_name"],
            "format": info["format"]["format_name"],
        }
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        raise SketchError("Video cannot be decoded within supported bounds") from exc


class VideoService:
    def __init__(self, images, selector=None):
        self.images, self.selector = images, selector or selected_video
        self.artifacts = images.artifacts

    def source(self, artifact_id, version):
        if not isinstance(artifact_id, str) or not 1 <= len(artifact_id) <= 200:
            raise SketchError("Video artifact ID must be a nonempty string")
        integer(version, 1, 1000000)
        artifact = self.artifacts.get(artifact_id)
        raw = (
            self.artifacts.raw_bytes(artifact_id, version=version)
            if artifact and artifact.kind == "video"
            else None
        )
        if not raw:
            raise SketchError("Pinned continuation video is unavailable", 404)
        if len(raw[0]) > 16 * 1024 * 1024:
            raise SketchError("Video exceeds 16 MiB")
        return raw

    def prepare(self, body):
        keys = (
            "prompt",
            "duration_seconds",
            "aspect_ratio",
            "controls",
            "first_frame_artifact_id",
            "first_frame_version",
            "last_frame_artifact_id",
            "last_frame_version",
            "continuation_artifact_id",
            "continuation_version",
        )
        fields(body, keys, ("prompt", "duration_seconds"))
        prompt, duration = body["prompt"], body["duration_seconds"]
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise SketchError("Video prompt must contain 1–4000 characters")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or not 1 <= duration <= 60
        ):
            raise SketchError("Video duration must be between 1 and 60 seconds")
        aspect = body.get("aspect_ratio", "")
        if not isinstance(aspect, str) or len(aspect) > 20:
            raise SketchError("Invalid video aspect ratio")
        controls = body.get("controls", {})
        fields(controls, ("seed", "guidance", "motion"))
        for key, value in controls.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 4294967295
                or key == "seed"
                and not isinstance(value, int)
            ):
                raise SketchError("Video controls require bounded numeric values")
        for prefix in ("first_frame", "last_frame", "continuation"):
            if (prefix + "_artifact_id" in body) != (prefix + "_version" in body):
                raise SketchError(
                    "Video conditioning references require an artifact and version"
                )
            if prefix + "_artifact_id" in body:
                artifact_id = body[prefix + "_artifact_id"]
                if not isinstance(artifact_id, str) or not artifact_id:
                    raise SketchError("Invalid conditioning artifact ID")
                if prefix == "continuation":
                    raw = self.source(artifact_id, body[prefix + "_version"])
                    with TemporaryDirectory(prefix="gideon-video-check-") as directory:
                        path = Path(directory) / "source.video"
                        path.write_bytes(raw[0])
                        probe_video(path)
                else:
                    CleanupService(self.images).source(
                        artifact_id, body[prefix + "_version"]
                    )
        if body.get("continuation_artifact_id") and body.get("first_frame_artifact_id"):
            raise SketchError("Continuation already determines the first frame")
        selected = self.selector()
        return dict(
            body,
            prompt=prompt.strip(),
            aspect_ratio=aspect,
            controls=dict(controls),
            selection=f"{selected[0].name}:{selected[1]}" if selected else "",
        )

    async def capabilities(self):
        selected = self.selector()
        tools_available = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
        if selected is None:
            return {
                "selection": "",
                "available": False,
                "media_tools_available": tools_available,
                "models": [],
            }
        provider, model_id = selected
        models = await asyncio.wait_for(provider.list_models(), timeout=5)
        return dict(
            selection=f"{provider.name}:{model_id}",
            available=bool(await asyncio.wait_for(provider.is_available(), timeout=5)),
            media_tools_available=tools_available,
            models=[
                dict(
                    name=model.name,
                    durations=model.durations,
                    aspect_ratios=model.aspect_ratios,
                    max_duration_s=model.max_duration_s,
                    supports_first_frame=model.supports_first_frame,
                    supports_last_frame=model.supports_last_frame,
                    supports_continuation=model.supports_continuation,
                    controls={
                        key: dict(
                            minimum=value.minimum,
                            maximum=value.maximum,
                            integer=value.integer,
                        )
                        for key, value in model.supported_controls.items()
                    },
                )
                for model in models
                if model.name == model_id
            ],
        )

    def validate_model(self, request, model):
        if model.durations and request["duration_seconds"] not in model.durations:
            raise SketchError("Selected model does not support this clip duration")
        if request["duration_seconds"] > model.max_duration_s:
            raise SketchError("Requested duration exceeds the selected model maximum")
        if (
            request["aspect_ratio"]
            and request["aspect_ratio"] not in model.aspect_ratios
        ):
            raise SketchError("Selected model does not advertise this aspect ratio")
        for prefix in ("first_frame", "last_frame", "continuation"):
            if request.get(prefix + "_artifact_id") and not getattr(
                model, "supports_" + prefix
            ):
                raise SketchError(
                    "Selected model does not advertise " + prefix + " conditioning"
                )
        for key, value in request["controls"].items():
            control = model.supported_controls.get(key)
            if (
                control is None
                or not control.minimum <= value <= control.maximum
                or control.integer
                and not isinstance(value, int)
            ):
                raise SketchError(
                    "Selected model does not support this " + key + " control"
                )

    def stage(self, request, directory):
        paths = {}
        for prefix in ("first_frame", "last_frame"):
            if request.get(prefix + "_artifact_id"):
                path = directory / (prefix + ".png")
                CleanupService(self.images).source(
                    request[prefix + "_artifact_id"], request[prefix + "_version"]
                ).save(path, "PNG")
                paths[prefix] = str(path)
        if request.get("continuation_artifact_id"):
            source = directory / "continuation.video"
            source.write_bytes(
                self.source(
                    request["continuation_artifact_id"], request["continuation_version"]
                )[0]
            )
            probe_video(source)
            frame = directory / "continuation.png"
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-v",
                        "error",
                        "-y",
                        "-sseof",
                        "-1",
                        "-i",
                        str(source),
                        "-map",
                        "0:v:0",
                        "-vsync",
                        "0",
                        "-update",
                        "1",
                        str(frame),
                    ],
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SketchError("Continuation frame extraction failed") from exc
            if not frame.is_file():
                raise SketchError("Continuation contains no decodable final frame")
            paths.update(continuation_video=str(source), continuation_frame=str(frame))
        return paths

    async def execute(self, request, job_id):
        media_tools()
        selected = self.selector()
        if selected is None:
            raise SketchError("No video generation provider is configured", 503)
        provider, model_id = selected
        if request["selection"] != f"{provider.name}:{model_id}":
            raise SketchError("Video selection changed; submit a new request", 409)
        if not await asyncio.wait_for(provider.is_available(), timeout=5):
            raise SketchError("Video provider is unavailable", 503)
        models = await asyncio.wait_for(provider.list_models(), timeout=5)
        model = next((model for model in models if model.name == model_id), None)
        if model is None:
            raise SketchError("Selected video model is absent from its catalog")
        self.validate_model(request, model)
        with TemporaryDirectory(prefix="gideon-video-input-") as directory:
            paths = await asyncio.to_thread(self.stage, request, Path(directory))
            try:
                results = await asyncio.wait_for(
                    provider.generate(
                        request["prompt"],
                        model=model_id,
                        duration_seconds=request["duration_seconds"],
                        aspect_ratio=request["aspect_ratio"],
                        **request["controls"],
                        **paths,
                    ),
                    timeout=1200,
                )
            except Exception as exc:
                raise SketchError(
                    "Video provider execution failed; inspect provider diagnostics", 503
                ) from exc
            if not results:
                raise SketchError("Video provider returned no output")
            return await asyncio.to_thread(
                self.materialize, results[0], request, job_id
            )

    def materialize(self, result, request, job_id):
        from gideon.integrations.mcp_artifacts import _materialize_video

        raw = _materialize_video(result)
        if (
            raw is None
            or len(raw[0]) > 16 * 1024 * 1024
            or raw[1] not in ("video/mp4", "video/webm")
        ):
            raise SketchError("Video output is absent, oversized or unsupported")
        with TemporaryDirectory(prefix="gideon-video-output-") as directory:
            path = Path(directory) / "output.video"
            path.write_bytes(raw[0])
            decoded = probe_video(path)
        if (
            raw[1] == "video/mp4"
            and "mp4" not in decoded["format"]
            or raw[1] == "video/webm"
            and "webm" not in decoded["format"]
        ):
            raise SketchError("Video MIME does not match its decoded container")
        metadata = dict(
            media_job_id=job_id,
            video_request_sha256=hashlib.sha256(
                json.dumps(request, sort_keys=True).encode()
            ).hexdigest(),
            model_selection=request["selection"],
        )
        if request.get("engine") == "ffmpeg":
            metadata.update(
                engine="FFmpeg",
                timeline_id=request["timeline_id"],
                timeline_revision=request["revision"],
            )
        if request.get("continuation_artifact_id"):
            metadata.update(
                source_artifact_id=request["continuation_artifact_id"],
                source_version=request["continuation_version"],
            )
        slug = "video-job-" + job_id
        artifact = self.artifacts.get(slug)
        if artifact and (
            not artifact.events or artifact.events[0].metadata != metadata
        ):
            raise SketchError("Video output identity conflicts", 409)
        if artifact is None:
            artifact = self.artifacts.create_binary(
                name=request["prompt"][:80],
                data=raw[0],
                mime=raw[1],
                kind="video",
                source="chat",
                slug=slug,
                event_metadata=metadata,
            )
        return dict(
            artifact_id=artifact.slug,
            version=artifact.version,
            **{key: decoded[key] for key in ("width", "height", "duration_seconds")},
        )
