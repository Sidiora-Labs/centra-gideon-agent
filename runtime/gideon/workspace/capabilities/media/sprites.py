import hashlib
import io
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from gideon.workspace.artifacts.models import is_valid_slug

from .cleanup import CleanupService
from .sketches import SketchError, fields, integer


class SpriteService:
    def __init__(self, path, images):
        self.path, self.images, self.artifacts = Path(path), images, images.artifacts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS frames (job TEXT, position INTEGER, body TEXT, PRIMARY KEY(job,position))"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def frames(self, job_id):
        with self.db() as db:
            rows = db.execute(
                "SELECT body FROM frames WHERE job=? ORDER BY position", (job_id,)
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows]}

    def inspect(self, body):
        fields(body, ("artifact_id", "version"), ("artifact_id", "version"))
        if not isinstance(body["artifact_id"], str) or not is_valid_slug(
            body["artifact_id"]
        ):
            raise SketchError("Invalid sprite artifact ID")
        integer(body["version"], 1, 1000000)
        image = CleanupService(self.images).source(body["artifact_id"], body["version"])
        raw = self.artifacts.raw_bytes(body["artifact_id"], version=body["version"])[0]
        return dict(
            body,
            sha256=hashlib.sha256(raw).hexdigest(),
            width=image.width,
            height=image.height,
            approved=False,
        )

    def labels(self, frames):
        if not isinstance(frames, list) or not 1 <= len(frames) <= 64:
            raise SketchError("Sprite jobs require 1–64 frames")
        names = set()
        for frame in frames:
            if not isinstance(frame, dict):
                raise SketchError("Sprite frame must be an object")
            for key in ("name", "animation", "direction"):
                value = frame.get(key, "")
                if (
                    not isinstance(value, str)
                    or len(value) > 64
                    or key == "name"
                    and not value.strip()
                ):
                    raise SketchError("Sprite labels require bounded strings")
            if frame["name"] in names:
                raise SketchError("Sprite frame names must be unique")
            names.add(frame["name"])

    def prepare_generate(self, body):
        fields(body, ("title", "frames"), ("title", "frames"))
        self.title(body["title"])
        self.labels(body["frames"])
        frames = []
        for frame in body["frames"]:
            fields(
                frame,
                ("name", "animation", "direction", "prompt", "size"),
                ("name", "prompt"),
            )
            prepared = self.images.prepare(
                {key: frame[key] for key in ("prompt", "size") if key in frame}
            )
            frames.append(
                dict(
                    name=frame["name"],
                    animation=frame.get("animation", ""),
                    direction=frame.get("direction", ""),
                    input=prepared,
                )
            )
        return dict(title=body["title"], frames=frames)

    def title(self, title):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise SketchError("Sprite title requires 1–120 characters")

    def prepare_compile(self, body):
        fields(
            body,
            (
                "title",
                "frames",
                "cell_width",
                "cell_height",
                "columns",
                "padding",
                "trim",
                "fps",
            ),
            (
                "title",
                "frames",
                "cell_width",
                "cell_height",
                "columns",
                "padding",
                "trim",
                "fps",
            ),
        )
        self.title(body["title"])
        self.labels(body["frames"])
        for key in ("cell_width", "cell_height"):
            integer(body[key], 1, 2048)
        integer(body["columns"], 1, 64)
        integer(body["padding"], 0, 32)
        integer(body["fps"], 1, 60)
        if not isinstance(body["trim"], bool):
            raise SketchError("Sprite trim must be a boolean")
        width = body["columns"] * (body["cell_width"] + 2 * body["padding"])
        height = math.ceil(len(body["frames"]) / body["columns"]) * (
            body["cell_height"] + 2 * body["padding"]
        )
        if width > 4096 or height > 4096:
            raise SketchError("Sprite atlas exceeds 4096 pixels per side")
        for frame in body["frames"]:
            fields(
                frame,
                (
                    "name",
                    "animation",
                    "direction",
                    "artifact_id",
                    "version",
                    "sha256",
                    "approved",
                ),
                ("name", "artifact_id", "version", "sha256", "approved"),
            )
            if (
                frame["approved"] is not True
                or not isinstance(frame["sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", frame["sha256"])
            ):
                raise SketchError(
                    "Each frame requires explicit approval and its SHA256"
                )
            self.checked_frame(frame, body)
        return dict(body)

    def checked_frame(self, frame, request):
        inspected = self.inspect(
            {key: frame[key] for key in ("artifact_id", "version")}
        )
        if inspected["sha256"] != frame["sha256"]:
            raise SketchError("Approved sprite frame hash changed", 409)
        image = CleanupService(self.images).source(
            frame["artifact_id"], frame["version"]
        )
        bbox = (
            image.getchannel("A").getbbox()
            if request["trim"]
            else (0, 0, image.width, image.height)
        )
        bbox = bbox or (0, 0, 1, 1)
        if (
            bbox[2] - bbox[0] > request["cell_width"]
            or bbox[3] - bbox[1] > request["cell_height"]
        ):
            raise SketchError(
                "Frame does not fit its cell; resize explicitly before compiling"
            )
        return image, bbox

    async def generate(self, request, job_id, stopped, progress):
        completed = {row["position"]: row for row in self.frames(job_id)["items"]}
        for index, frame in enumerate(request["frames"]):
            if stopped():
                raise SketchError(
                    "Sprite generation cancelled; completed frames retained", 409
                )
            if index not in completed:
                result = await self.images.execute(
                    frame["input"], job_id + "-frame-" + str(index)
                )
                inspected = self.inspect(
                    {key: result[key] for key in ("artifact_id", "version")}
                )
                record = dict(
                    inspected,
                    position=index,
                    name=frame["name"],
                    animation=frame["animation"],
                    direction=frame["direction"],
                )
                with self.db() as db:
                    db.execute(
                        "INSERT INTO frames VALUES (?,?,?)",
                        (job_id, index, json.dumps(record)),
                    )
            else:
                if (
                    self.inspect(
                        {
                            key: completed[index][key]
                            for key in ("artifact_id", "version")
                        }
                    )["sha256"]
                    != completed[index]["sha256"]
                ):
                    raise SketchError("Generated frame checkpoint hash changed", 409)
            progress((index + 1) / len(request["frames"]))
        return {"frames": self.frames(job_id)["items"], "approval_required": True}

    def compile(self, request, job_id, stopped, progress):
        self.prepare_compile(request)
        padding, width, height, columns = (
            request[key] for key in ("padding", "cell_width", "cell_height", "columns")
        )
        sheet = Image.new(
            "RGBA",
            (
                columns * (width + 2 * padding),
                math.ceil(len(request["frames"]) / columns) * (height + 2 * padding),
            ),
        )
        entries = []
        for index, frame in enumerate(request["frames"]):
            if stopped():
                raise SketchError("Sprite compilation cancelled", 409)
            image, bbox = self.checked_frame(frame, request)
            cropped = image.crop(bbox)
            x = (index % columns) * (width + 2 * padding) + padding
            y = (index // columns) * (height + 2 * padding) + padding
            sheet.paste(cropped, (x, y))
            entries.append(
                dict(
                    name=frame["name"],
                    animation=frame.get("animation", ""),
                    direction=frame.get("direction", ""),
                    artifact_id=frame["artifact_id"],
                    version=frame["version"],
                    sha256=frame["sha256"],
                    frame=dict(x=x, y=y, w=cropped.width, h=cropped.height),
                    sourceSize=dict(w=image.width, h=image.height),
                    spriteSourceSize=dict(
                        x=bbox[0], y=bbox[1], w=cropped.width, h=cropped.height
                    ),
                )
            )
            progress((index + 1) / (len(request["frames"]) + 1))
        output = io.BytesIO()
        sheet.save(output, "PNG")
        metadata = dict(
            media_job_id=job_id,
            sprite_request_sha256=hashlib.sha256(
                json.dumps(request, sort_keys=True).encode()
            ).hexdigest(),
            engine="Pillow",
        )
        with self.artifacts.mutation_lock:
            atlas = self.artifacts.get("sprite-atlas-" + job_id)
            if atlas and (
                atlas.version != 1
                or not atlas.events
                or atlas.events[0].metadata != metadata
            ):
                raise SketchError("Sprite atlas identity conflict", 409)
            if atlas is None:
                atlas = self.artifacts.create_binary(
                    name=request["title"],
                    data=output.getvalue(),
                    mime="image/png",
                    kind="image",
                    slug="sprite-atlas-" + job_id,
                    event_metadata=metadata,
                )
            manifest = dict(
                schema_version=1,
                atlas=dict(
                    artifact_id=atlas.slug,
                    version=atlas.version,
                    sha256=hashlib.sha256(output.getvalue()).hexdigest(),
                    width=sheet.width,
                    height=sheet.height,
                ),
                fps=request["fps"],
                frames=entries,
            )
            encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
            layout = self.artifacts.get("sprite-layout-" + job_id)
            if layout and (
                layout.version != 1
                or layout.content != encoded
                or not layout.events
                or layout.events[0].metadata != metadata
            ):
                raise SketchError("Sprite manifest identity conflict", 409)
            if layout is None:
                layout = self.artifacts.create(
                    name=request["title"] + " layout",
                    content=encoded,
                    kind="json",
                    slug="sprite-layout-" + job_id,
                    event_metadata=metadata,
                )
        progress(1)
        return dict(
            artifact_id=atlas.slug,
            version=atlas.version,
            manifest_id=layout.slug,
            manifest_version=layout.version,
        )

    def manifest(self, result):
        artifact = self.artifacts.get(
            result["manifest_id"], version=result["manifest_version"]
        )
        if not artifact or artifact.kind != "json":
            raise SketchError("Sprite manifest is unavailable", 404)
        return json.loads(artifact.content)
