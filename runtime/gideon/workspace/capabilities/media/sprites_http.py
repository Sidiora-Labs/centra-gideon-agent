import asyncio

from aiohttp import web

from .jobs_http import JOBS_KEY
from .sketches import SketchError


async def dispatch(request):
    try:
        if request.query:
            raise SketchError("Query parameters are not accepted")
        jobs = request.app[JOBS_KEY]
        if request.method == "POST":
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError("Invalid JSON") from exc
            value = await asyncio.to_thread(jobs.sprites.inspect, body)
        else:
            job = jobs.get(request.match_info["job_id"])
            if request.path.endswith("/atlas"):
                if job["operation"] != "sprite_compile" or not job.get("result"):
                    raise SketchError("Compiled sprite atlas is unavailable", 404)
                value = jobs.sprites.manifest(job["result"])
            else:
                if job["operation"] != "sprite_generate":
                    raise SketchError("Job is not sprite generation")
                value = jobs.sprites.frames(job["id"])
        return web.json_response(value)
    except SketchError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


def register_sprites(app):
    app.router.add_post("/api/capabilities/media/sprites/inspect", dispatch)
    app.router.add_get("/api/capabilities/media/jobs/{job_id}/frames", dispatch)
    app.router.add_get("/api/capabilities/media/jobs/{job_id}/atlas", dispatch)
