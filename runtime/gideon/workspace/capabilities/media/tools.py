import asyncio
import json

from jsonschema import Draft202012Validator, ValidationError

from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .readiness import MediaReadiness
from .jobs import MediaJobs
from .library import MediaLibrary
from .annotations import AnnotationStore
from .sketches import SketchError, SketchStore

STRING = {"type": "string", "maxLength": 200}
INTEGER = {"type": "integer", "minimum": 1}
STROKES = {"type": "array", "maxItems": 500, "items": {
    "type": "object", "additionalProperties": False, "required": ["tool", "color", "width", "points"],
    "properties": {"tool": {"enum": ["draw", "erase"]}, "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "width": {"type": "integer", "minimum": 1, "maximum": 128},
        "points": {"type": "array", "minItems": 1, "maxItems": 5000, "items": {
            "type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number", "minimum": 0, "maximum": 4095}}}}}}
CATALOG = {
    "media_library_list": ("List image/video artifacts with query-wide facets and pagination.", (), {**{key: STRING for key in ("kind", "tag", "collection", "q")}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, False),
    "media_library_get": ("Read canonical media metadata and version-pinned download reference.", ("artifact_id",), {"artifact_id": STRING}, False),
    "media_library_update": ("Edit canonical media name, tags and collection using a current updated_at token; source bytes stay unchanged.", ("artifact_id", "expected_updated_at"), {"artifact_id": STRING, "expected_updated_at": STRING, "name": STRING, "collection": STRING, "tags": {"type": "array", "maxItems": 16, "items": STRING}}, True),
    "media_sketch_list": ("List the latest 100 editable sketches.", (), {}, False),
    "media_sketch_get": ("Read saved strokes and pinned original image reference.", ("sketch_id",), {"sketch_id": STRING}, False),
    "media_sketch_create": ("Create a blank sketch or overlay on an existing image artifact at its exact dimensions.", ("width", "height", "request_id"), {"width": INTEGER, "height": INTEGER, "request_id": STRING, "source_artifact_id": STRING, "source_version": INTEGER, "strokes": STROKES}, True),
    "media_sketch_update": ("Save draw/erase strokes using the current revision; omit the last stroke to undo.", ("sketch_id", "revision", "strokes"), {"sketch_id": STRING, "revision": INTEGER, "strokes": STROKES}, True),
    "media_sketch_export": ("Flatten a saved sketch revision into a canonical PNG derivative without modifying its original.", ("sketch_id", "revision"), {"sketch_id": STRING, "revision": INTEGER}, True),
}
ATTRIBUTION = {"type": "object", "additionalProperties": False, "required": ["creator", "license", "source_url"], "properties": {key: {"type": "string"} for key in ("creator", "license", "source_url")}}
ANNOTATIONS = {"type": "array", "maxItems": 100, "items": {"type": "object", "additionalProperties": False, "required": ["id", "text"], "properties": {
    "id": STRING, "text": {"type": "string", "maxLength": 4000}, "region": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number", "minimum": 0, "maximum": 1}}, "time_seconds": {"type": "number", "minimum": 0, "maximum": 604800}}}}
CATALOG.update({
    "media_annotations_get": ("Read media annotations at a pinned artifact version, optionally a historical annotation revision.", ("artifact_id", "version"), {"artifact_id": STRING, "version": INTEGER, "annotation_revision": INTEGER}, False),
    "media_annotations_save": ("Save versioned notes, image regions or video timestamps plus attribution without rewriting original provenance.", ("artifact_id", "version", "revision", "request_id", "annotations", "attribution"), {"artifact_id": STRING, "version": INTEGER, "revision": {"type": "integer", "minimum": 0}, "request_id": STRING, "annotations": ANNOTATIONS, "attribution": ATTRIBUTION}, True),
    "media_annotations_history": ("List retained media annotation revision summaries with pagination.", ("artifact_id", "version"), {"artifact_id": STRING, "version": INTEGER, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, False),
})

IMAGE_INPUT = {"type": "object", "additionalProperties": False, "required": ["prompt"], "properties": {
    "prompt": {"type": "string", "minLength": 1, "maxLength": 4000}, "size": STRING,
    "source_artifact_id": STRING, "source_version": INTEGER, "mask_artifact_id": STRING, "mask_version": INTEGER,
    "controls": {"type": "object", "additionalProperties": False, "properties": {key: {"type": "integer" if key in ("seed", "steps") else "number"} for key in ("seed", "steps", "guidance", "strength")}}}}
CATALOG.update({
    "media_image_capabilities": ("Read the selected image model's advertised controls and conditioning support.", (), {}, False),
    "media_image_submit": ("Queue image generation or pinned-source conditioning; unsupported controls fail explicitly before provider inference.", ("request_id", "input"), {"request_id": STRING, "input": IMAGE_INPUT}, True),
    "media_readiness_get": ("Read the last observed image/video provider readiness; availability is not inference verification.", (), {}, False),
    "media_readiness_refresh": ("Probe selected image/video provider availability and catalogs without generating media or installing models.", (), {}, False),
    "media_jobs_list": ("List the latest 100 durable local rendering jobs.", (), {}, False),
    "media_jobs_get": ("Read rendering state, attempt history and any canonical result artifact.", ("job_id",), {"job_id": STRING}, False),
    "media_jobs_submit": ("Queue a saved sketch PNG export for the supervised media worker.", ("operation", "sketch_id", "revision", "request_id"), {"operation": {"enum": ["sketch_export"]}, "sketch_id": STRING, "revision": INTEGER, "request_id": STRING}, True),
    "media_jobs_cancel": ("Request cancellation using the current state revision; completed output remains available.", ("job_id", "state_revision"), {"job_id": STRING, "state_revision": INTEGER}, True),
    "media_jobs_retry": ("Retry a failed or cancelled render with its current state revision; attempt history is retained.", ("job_id", "state_revision"), {"job_id": STRING, "state_revision": INTEGER}, True),
})


def schema(name):
    _, required, properties, _ = CATALOG[name]
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(required)}


class MediaToolProvider(ToolProvider):
    def __init__(self, sketches, library, annotations=None):
        self.sketches, self.library = sketches, library
        self.readiness = MediaReadiness(sketches.path.parent / 'readiness.sqlite3')
        self.jobs = MediaJobs(sketches.path.parent / 'jobs.sqlite3', sketches)
        self.annotations = annotations or AnnotationStore(sketches.path.parent / 'annotations.sqlite3', sketches.artifacts)

    @property
    def name(self):
        return "gideon-media"

    @property
    def display_name(self):
        return "Gideon Media"

    async def list_tools(self):
        return [ToolDefinition(name=name, description=row[0], provider=self.name, parameters=schema(name),
                    requires_approval=False, risk_level=RiskLevel.CAUTION if row[3] else RiskLevel.SAFE)
                for name, row in CATALOG.items()]

    async def invoke(self, tool_name, arguments):
        if tool_name not in CATALOG:
            return ToolResult(success=False, error="Unknown media tool")
        try:
            Draft202012Validator(schema(tool_name)).validate(arguments)
            if tool_name == "media_readiness_refresh":
                result = await self.readiness.refresh()
            elif tool_name == "media_image_capabilities":
                result = await self.jobs.images.capabilities()
            else:
                result = await asyncio.to_thread(self._run, tool_name, dict(arguments))
            return ToolResult(success=True, output=json.dumps(result, allow_nan=False))
        except (SketchError, ValidationError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc)[:500], metadata={"status": getattr(exc, "status", 400)},
                              recovery_hints=["Read the current artifact or sketch, correct the input, and retry with its current revision."])

    def _run(self, name, args):
        if name == 'media_image_submit':
            return self.jobs.submit(dict(operation='image_generate', **args))
        if name == 'media_readiness_get':
            return self.readiness.get()
        if name.startswith('media_jobs_'):
            action = name.removeprefix('media_jobs_')
            if action == 'list':
                return self.jobs.list()
            if action == 'submit':
                return self.jobs.submit(args)
            job_id = args.pop('job_id')
            return self.jobs.get(job_id) if action == 'get' else getattr(self.jobs, action)(job_id, args)
        if name.startswith('media_annotations_'):
            artifact_id, version = args.pop('artifact_id'), args.pop('version')
            if name == 'media_annotations_get':
                return self.annotations.get(artifact_id, version, args.get('annotation_revision'))
            if name == 'media_annotations_history':
                return self.annotations.history(artifact_id, version, **args)
            return self.annotations.save(artifact_id, version, args)
        if name == "media_library_list":
            return self.library.list(args)
        if name == "media_library_get":
            return self.library.get(args["artifact_id"])
        if name == "media_library_update":
            return self.library.update(args.pop("artifact_id"), args)
        if name == "media_sketch_list":
            return {"items": self.sketches.list()}
        if name == "media_sketch_get":
            return self.sketches.get(args["sketch_id"])
        if name == "media_sketch_create":
            return self.sketches.create(args)
        sketch_id = args.pop("sketch_id")
        return self.sketches.update(sketch_id, args) if name == "media_sketch_update" else self.sketches.export(sketch_id, args)


def create_provider(config=None):
    from gideon.core.config.loader import config_dir
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    home = config_dir()
    artifacts = NativeArtifactProvider(home / "artifacts")
    return MediaToolProvider(SketchStore(home / "capabilities/media/sketches.sqlite3", artifacts), MediaLibrary(artifacts))
