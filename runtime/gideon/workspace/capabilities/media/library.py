import hashlib
import io
import re
from collections import Counter

from PIL import Image

from .sketches import SketchError, fields


def text(value, limit=200, empty=True):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()) or any(ord(c) < 32 for c in value):
        raise SketchError("Invalid text value")
    return value.strip()


class MediaLibrary:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def _artifact(self, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", artifact_id):
            raise SketchError("Invalid artifact ID")
        artifact = self.artifacts.get(artifact_id)
        if not artifact or artifact.kind not in ("image", "video"):
            raise SketchError("Media artifact not found", 404)
        return artifact

    def project(self, artifact):
        keys = {"original_filename", "sha256", "sketch_id", "sketch_revision", "source_artifact_id", "source_version"}
        provenance = {}
        for event in artifact.events:
            if event.type == "created":
                provenance.update({key: value for key, value in event.metadata.items() if key in keys})
        return dict(id=artifact.slug, name=artifact.name, kind=artifact.kind, mime=artifact.mime,
                    version=artifact.version, updated_at=artifact.updated_at, tags=artifact.tags,
                    collection=artifact.collection, readonly=artifact.readonly, source=artifact.source,
                    raw_url=f"/api/artifacts/{artifact.slug}/raw?version={artifact.version}", provenance=provenance)

    def get(self, artifact_id):
        return self.project(self._artifact(artifact_id))

    def list(self, query):
        fields(query, ("kind", "tag", "collection", "q", "offset", "limit"))
        if query.get("kind", "") not in ("", "image", "video"):
            raise SketchError("Unsupported media kind")
        try:
            offset, limit = int(query.get("offset", "0")), int(query.get("limit", "25"))
        except (ValueError, TypeError) as exc:
            raise SketchError("Invalid pagination") from exc
        if not 0 <= offset <= 1000000 or not 1 <= limit <= 100:
            raise SketchError("Pagination outside allowed range")
        filters = {key: text(query.get(key, "")) for key in ("kind", "tag", "collection", "q")}
        found = self.artifacts.list(**{key: value for key, value in filters.items() if value})
        rows = sorted((item for item in found if item.kind in ("image", "video")), key=lambda item: (item.updated_at, item.slug), reverse=True)
        facets = dict(kinds=dict(Counter(item.kind for item in rows)),
                      tags=dict(Counter(tag for item in rows for tag in item.tags)),
                      collections=dict(Counter(item.collection for item in rows if item.collection)))
        return dict(items=[self.project(item) for item in rows[offset:offset+limit]], total=len(rows), offset=offset, limit=limit, facets=facets)

    def update(self, artifact_id, body):
        fields(body, ("expected_updated_at", "name", "tags", "collection"), ("expected_updated_at",))
        expected = text(body["expected_updated_at"], 80, empty=False)
        if not set(body) & {"name", "tags", "collection"}:
            raise SketchError("No metadata changes supplied")
        patch = {}
        for key in ("name", "collection"):
            if key in body:
                patch[key] = text(body[key], empty=key == "collection")
        if "tags" in body:
            if not isinstance(body["tags"], list) or len(body["tags"]) > 16:
                raise SketchError("At most 16 tags are allowed")
            patch["tags"] = list(dict.fromkeys(text(tag, 64, empty=False) for tag in body["tags"]))
        self._artifact(artifact_id)
        try:
            artifact = self.artifacts.update(artifact_id, expect_updated_at=expected, **patch)
        except ValueError as exc:
            raise SketchError(str(exc), 409) from exc
        if not artifact:
            raise SketchError("Media artifact not found", 404)
        return self.project(artifact)

    def import_image(self, data, *, filename, request_id, name="", mime=""):
        with self.artifacts.mutation_lock:
            return self._import_image(data, filename=filename, request_id=request_id, name=name, mime=mime)

    def _import_image(self, data, *, filename, request_id, name, mime):
        filename = text(filename, 200, empty=False)
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            raise SketchError("Filename must be a basename")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
            raise SketchError("Invalid request ID")
        name = text(name or filename, empty=False)
        if not data or len(data) > 16 * 1024 * 1024:
            raise SketchError("Image exceeds 16 MB limit", 413)
        try:
            with Image.open(io.BytesIO(data)) as image:
                detected = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
                if not detected or image.width * image.height > 4096 * 4096 or getattr(image, "n_frames", 1) != 1:
                    raise SketchError("Unsupported image format, animation or dimensions")
                image.verify()
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise SketchError("Invalid image bytes") from exc
        if mime != detected:
            raise SketchError("Image content type does not match bytes")
        digest = hashlib.sha256(data).hexdigest()
        fingerprint = hashlib.sha256((digest + "\0" + filename + "\0" + name).encode()).hexdigest()
        slug = "media-import-" + hashlib.sha256(request_id.encode()).hexdigest()
        existing = self.artifacts.get(slug)
        if existing:
            if not any(event.metadata.get("import_fingerprint") == fingerprint for event in existing.events):
                raise SketchError("Import request ID conflicts", 409)
            return self.project(existing)
        artifact = self.artifacts.create_binary(name=name, slug=slug, data=data, mime=detected, kind="image", source="import",
            event_metadata={"original_filename": filename, "sha256": digest, "import_fingerprint": fingerprint})
        return self.project(artifact)
