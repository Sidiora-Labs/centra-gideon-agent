from aiohttp import web

from gideon.core.http_request import read_json_body

from .narration import NarrationJobs, get_narration_jobs
from .store import Conflict, NotFound

JOBS = web.AppKey("experience_narration", NarrationJobs)


async def handle(request):
    jobs = request.app[JOBS]
    try:
        key = request.match_info.get("narration_id")
        if request.match_info.get("session_id"):
            result = jobs.start(
                request.match_info["session_id"], await read_json_body(request)
            )
        elif request.method == "POST":
            if await read_json_body(request):
                raise ValueError("cancel accepts no fields")
            result = await jobs.cancel(key)
        elif request.path.endswith("/audio"):
            data, mime = jobs.audio(key)
            return web.Response(
                body=data,
                content_type=mime,
                headers={"Cache-Control": "private, no-store"},
            )
        else:
            result = jobs.get(key)
        return web.json_response(
            {"narration": result},
            status=202 if request.match_info.get("session_id") else 200,
        )
    except NotFound as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except Conflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


def register_narration(app, store):
    app[JOBS] = get_narration_jobs(store)
    prefix = "/api/capabilities/experience"
    app.router.add_post(prefix + "/sessions/{session_id}/narration", handle)
    app.router.add_get(prefix + "/narrations/{narration_id}", handle)
    app.router.add_get(prefix + "/narrations/{narration_id}/audio", handle)
    app.router.add_post(prefix + "/narrations/{narration_id}/cancel", handle)

    async def cleanup(application):
        await application[JOBS].close()

    app.on_cleanup.append(cleanup)
