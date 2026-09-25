import asyncio

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers.agents import _get_config_lock
from gideon.workspace.capabilities.platform.references import (
    ReferenceError,
    add,
    change,
    view,
)


async def references(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        if request.method != "GET":
            body = await read_json_body(request) if request.method == "POST" else {}
            async with _get_config_lock():
                if "id" not in request.match_info:
                    await asyncio.to_thread(add, body)
                else:
                    await asyncio.to_thread(
                        change,
                        request.match_info["id"],
                        (
                            "remove"
                            if request.method == "DELETE"
                            else request.match_info["action"]
                        ),
                        body,
                    )
        return web.json_response(view(), headers={"Cache-Control": "no-store"})
    except ReferenceError as error:
        return web.json_response({"error": str(error)}, status=error.status)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid reference request"}, status=400)


def register(app):
    prefix = "/api/capabilities/platform/references"
    app.router.add_get(prefix, references)
    app.router.add_post(prefix, references)
    app.router.add_post(prefix + "/{id}/{action}", references)
    app.router.add_delete(prefix + "/{id}", references)
