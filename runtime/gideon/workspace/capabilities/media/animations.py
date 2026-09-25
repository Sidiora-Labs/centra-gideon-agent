import hashlib
import re

from .sketches import SketchError, fields, integer

RENDERERS = ("canvas2d", "svg", "css")
_DOCUMENT = re.compile(r"<!doctype\s+html[\s\S]*?</html\s*>", re.IGNORECASE)
_EXTERNAL = re.compile(
    r"(?:\bsrc|\bhref)\s*=\s*['\"]\s*(?:https?:|//)|"
    r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(",
    re.IGNORECASE,
)


class AnimationService:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def prepare(self, body):
        fields(
            body,
            (
                "title",
                "concept",
                "renderer",
                "duration_seconds",
                "width",
                "height",
                "fps",
                "interactive",
            ),
            (
                "title",
                "concept",
                "renderer",
                "duration_seconds",
                "width",
                "height",
                "fps",
                "interactive",
            ),
        )
        for key, limit in (("title", 120), ("concept", 4000)):
            if (
                not isinstance(body[key], str)
                or not 1 <= len(body[key].strip()) <= limit
            ):
                raise SketchError(f"Animation {key} requires 1–{limit} characters")
        if body["renderer"] not in RENDERERS:
            raise SketchError("Animation renderer must be canvas2d, svg, or css")
        integer(body["duration_seconds"], 1, 180)
        integer(body["width"], 64, 1920)
        integer(body["height"], 64, 1920)
        integer(body["fps"], 1, 60)
        if body["width"] * body["height"] > 2_073_600:
            raise SketchError("Animation frame exceeds 2,073,600 pixels")
        if not isinstance(body["interactive"], bool):
            raise SketchError("Animation interactive must be a boolean")
        return dict(body)

    def prompt(self, request):
        mode = (
            "may react to pointer or keyboard input"
            if request["interactive"]
            else "must not require user input"
        )
        return f"""Create one original, self-contained HTML animation from this brief:
Title: {request['title']}
Concept: {request['concept']}

Hard contract:
- Return exactly one complete <!doctype html> document and no commentary or Markdown fence.
- Use only inline HTML, CSS, and JavaScript. No network requests, imports, external assets, fonts, or libraries.
- Render at {request['width']}x{request['height']}, {request['fps']} fps, for {request['duration_seconds']} seconds with the {request['renderer']} renderer.
- Define window.ANIMATION_META={{width,height,fps,durationSeconds,renderer,interactive}} with those exact values.
- Define window.renderFrame(t), where t is seconds, and make rendering deterministic for equal t.
- The page {mode}. Respect prefers-reduced-motion while keeping manual renderFrame(t) usable.
- Start playback in the page and keep all drawing inside the declared frame.
"""

    def extract(self, response):
        if not isinstance(response, str) or len(response.encode()) > 2_000_000:
            raise SketchError("Animation model response is empty or exceeds 2 MB")
        match = _DOCUMENT.search(response.strip())
        if not match:
            raise SketchError(
                "Animation model response did not contain a complete HTML document"
            )
        html = match.group(0)
        if _EXTERNAL.search(html) or re.search(
            r"<meta[^>]+http-equiv\s*=\s*['\"]?refresh", html, re.IGNORECASE
        ):
            raise SketchError("Animation HTML must be self-contained and offline")
        if not re.search(r"\brenderFrame\s*=|\bfunction\s+renderFrame\s*\(", html):
            raise SketchError("Animation HTML is missing renderFrame(t)")
        if "ANIMATION_META" not in html or "<script" not in html.lower():
            raise SketchError("Animation HTML is missing its runtime contract")
        return html

    def publish(self, request, job_id, response):
        html = self.extract(response)
        digest = hashlib.sha256(html.encode()).hexdigest()
        metadata = {
            "media_job_id": job_id,
            "animation_request_sha256": hashlib.sha256(
                self.prompt(request).encode()
            ).hexdigest(),
            "html_sha256": digest,
            "provider_use_case": "reasoning",
        }
        slug = "code-animation-" + job_id
        existing = self.artifacts.get(slug)
        if existing:
            current = self.artifacts.get(slug, version=1)
            if (
                existing.version != 1
                or not current
                or current.content != html
                or not existing.events
                or existing.events[0].metadata != metadata
            ):
                raise SketchError("Animation artifact identity conflict", 409)
            artifact = existing
        else:
            artifact = self.artifacts.create(
                name=request["title"],
                content=html,
                kind="widget",
                slug=slug,
                description="Self-contained generated code animation",
                tags=["animation", request["renderer"]],
                event_metadata=metadata,
            )
        return {
            "artifact_id": artifact.slug,
            "version": artifact.version,
            "sha256": digest,
            "frame": {
                key: request[key]
                for key in ("width", "height", "fps", "duration_seconds")
            },
            "renderer": request["renderer"],
        }

    async def execute(self, request, job_id):
        from gideon.integrations.llm_helpers import one_shot_completion

        response = await one_shot_completion(self.prompt(request), use_case="reasoning")
        return self.publish(request, job_id, response)
