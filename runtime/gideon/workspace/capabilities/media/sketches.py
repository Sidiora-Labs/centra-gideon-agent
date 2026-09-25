from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageOps


class SketchError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def fields(body, allowed, required=()):
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise SketchError("Invalid request fields")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise SketchError("Integer outside allowed range")
    return value


def strokes(value, width, height):
    if not isinstance(value, list) or len(value) > 500:
        raise SketchError("At most 500 strokes are allowed")
    total = 0
    for stroke in value:
        fields(stroke, ("tool", "color", "width", "points"), ("tool", "color", "width", "points"))
        if stroke["tool"] not in ("draw", "erase") or not isinstance(stroke["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", stroke["color"]):
            raise SketchError("Invalid brush")
        integer(stroke["width"], 1, 128)
        points = stroke["points"]
        if not isinstance(points, list) or not 1 <= len(points) <= 5000:
            raise SketchError("Invalid stroke points")
        total += len(points)
        if total > 20000:
            raise SketchError("Too many points")
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise SketchError("Invalid point")
            for number, bound in zip(point, (width, height)):
                if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number < bound:
                    raise SketchError("Point outside canvas")
    return value


class SketchStore:
    def __init__(self, path: Path, artifacts):
        self.path = Path(path)
        self.artifacts = artifacts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sketches (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, request_hash TEXT, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS exports (id TEXT, revision INTEGER, artifact_id TEXT, PRIMARY KEY(id,revision))")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, sketch_id):
        with self._db() as db:
            row = db.execute("SELECT body FROM sketches WHERE id=?", (sketch_id,)).fetchone()
        if not row:
            raise SketchError("Sketch not found", 404)
        return json.loads(row[0])

    def list(self):
        with self._db() as db:
            rows = db.execute("SELECT body FROM sketches ORDER BY rowid DESC LIMIT 100").fetchall()
        return [json.loads(row[0]) for row in rows]

    def source(self, sketch):
        source = sketch["source_artifact_id"]
        art = self.artifacts.get(source, version=sketch["source_version"]) if source else None
        raw = self.artifacts.raw_bytes(source, version=sketch["source_version"]) if art and art.kind == "image" else None
        if not raw:
            raise SketchError("Source image is unavailable", 404)
        return raw

    def _image(self, sketch):
        if not sketch["source_artifact_id"]:
            return Image.new("RGBA", (sketch["width"], sketch["height"]), "white")
        raw, _ = self.source(sketch)
        if len(raw) > 20 * 1024 * 1024:
            raise SketchError("Source image is too large")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 4096 * 4096:
                    raise SketchError("Source image is too large")
                image = ImageOps.exif_transpose(image).convert("RGBA")
                if image.size != (sketch["width"], sketch["height"]):
                    raise SketchError("Canvas must match source dimensions")
                return image
        except (OSError, Image.DecompressionBombError) as exc:
            raise SketchError("Source is not a supported image") from exc

    def create(self, body):
        fields(body, ("width", "height", "strokes", "source_artifact_id", "source_version", "request_id"), ("width", "height", "request_id"))
        request_id = body["request_id"]
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
            raise SketchError("Invalid request ID")
        width, height = integer(body["width"], 1, 4096), integer(body["height"], 1, 4096)
        source = body.get("source_artifact_id")
        version = body.get("source_version")
        if source is not None:
            if not isinstance(source, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", source):
                raise SketchError("Invalid source artifact")
            integer(version, 1, 1000000)
        elif version is not None:
            raise SketchError("Source version requires an artifact")
        sketch = dict(id=str(uuid4()), width=width, height=height, source_artifact_id=source, source_version=version,
                      strokes=strokes(body.get("strokes", []), width, height), revision=1, updated_at=datetime.now(timezone.utc).isoformat())
        self._image(sketch)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT request_hash,body FROM sketches WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise SketchError("Request ID already used with different content", 409)
                return json.loads(previous[1])
            db.execute("INSERT INTO sketches VALUES (?,?,?,?)", (sketch["id"], request_id, fingerprint, json.dumps(sketch)))
        return sketch

    def update(self, sketch_id, body):
        fields(body, ("revision", "strokes"), ("revision", "strokes"))
        integer(body["revision"], 1, 1000000)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM sketches WHERE id=?", (sketch_id,)).fetchone()
            if not row:
                raise SketchError("Sketch not found", 404)
            sketch = json.loads(row[0])
            if sketch["revision"] != body["revision"]:
                raise SketchError("Sketch changed; reload before saving", 409)
            sketch.update(strokes=strokes(body["strokes"], sketch["width"], sketch["height"]), revision=sketch["revision"] + 1,
                          updated_at=datetime.now(timezone.utc).isoformat())
            db.execute("UPDATE sketches SET body=? WHERE id=?", (json.dumps(sketch), sketch_id))
        return sketch

    def export(self, sketch_id, body):
        fields(body, ("revision",), ("revision",))
        integer(body["revision"], 1, 1000000)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            sketch = self.get(sketch_id)
            if sketch["revision"] != body["revision"]:
                raise SketchError("Sketch changed; reload before exporting", 409)
            slug = f"sketch-{sketch_id}-r{sketch['revision']}"
            artifact = self.artifacts.get(slug)
            if artifact and not any(event.metadata.get("sketch_id") == sketch_id and event.metadata.get("sketch_revision") == sketch["revision"] for event in artifact.events):
                raise SketchError("Export artifact identity conflicts", 409)
            if not artifact:
                image = self._image(sketch)
                overlay = Image.new("RGBA", image.size)
                for stroke in sketch["strokes"]:
                    draw = ImageDraw.Draw(overlay)
                    color = stroke["color"] if stroke["tool"] == "draw" else (0, 0, 0, 0)
                    points = [tuple(point) for point in stroke["points"]]
                    draw.line(points, fill=color, width=stroke["width"])
                    radius = stroke["width"] / 2
                    for x, y in (points[0], points[-1]):
                        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
                image = Image.alpha_composite(image, overlay)
                output = io.BytesIO()
                image.save(output, format="PNG")
                artifact = self.artifacts.create_binary(name="Sketch export", slug=slug, data=output.getvalue(), mime="image/png", kind="image",
                    event_metadata={"sketch_id": sketch_id, "sketch_revision": sketch["revision"], "source_artifact_id": sketch["source_artifact_id"] or "", "source_version": sketch["source_version"] or 0})
            db.execute("INSERT OR REPLACE INTO exports VALUES (?,?,?)", (sketch_id, sketch["revision"], artifact.slug))
        return dict(artifact_id=artifact.slug, version=artifact.version, sketch_id=sketch_id, sketch_revision=sketch["revision"],
                    source_artifact_id=sketch["source_artifact_id"], source_version=sketch["source_version"])
