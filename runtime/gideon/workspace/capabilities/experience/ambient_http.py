from aiohttp import web

from gideon.core.http_request import read_json_body

from .ambient import AmbientDisplay
from .store import Conflict

AMBIENT = web.AppKey("experience_ambient", AmbientDisplay)


async def handle(request):
    ambient = request.app[AMBIENT]
    try:
        if request.method == "PUT":
            return web.json_response(
                {"preferences": ambient.save(await read_json_body(request))}
            )
        return web.json_response(await ambient.snapshot())
    except Conflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


def register_ambient(app, store):
    app[AMBIENT] = AmbientDisplay(store)
    app.router.add_get("/api/capabilities/experience/ambient", handle)
    app.router.add_put("/api/capabilities/experience/ambient", handle)
