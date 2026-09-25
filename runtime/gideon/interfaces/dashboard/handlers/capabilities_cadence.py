from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.cadence import mutate, view


async def endpoint(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        result = (
            view()
            if request.method == "GET"
            else mutate(request.match_info["id"], await read_json_body(request))
        )
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except (ValueError, OSError):
        return web.json_response(
            {"error": "Cadence policy invalid or changed; reload before saving"},
            status=409,
        )


def register(app):
    app.router.add_get("/api/capabilities/platform/cadence", endpoint)
    app.router.add_put("/api/capabilities/platform/cadence/{id}", endpoint)
