from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers.agents import _get_config_lock
from gideon.workspace.capabilities.platform.connections import (
    ConnectionError,
    mutate,
    projection,
)


async def connections(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        if request.method == "GET":
            return web.json_response(
                projection(), headers={"Cache-Control": "no-store"}
            )
        body = (
            {"revision": int(request.query.get("revision", "-1"))}
            if request.method == "DELETE"
            else await read_json_body(request)
        )
        if not isinstance(body, dict):
            raise ConnectionError("JSON object required")
        operation = (
            "delete"
            if request.method == "DELETE"
            else "bind" if "provider" in request.match_info else "save"
        )
        async with _get_config_lock():
            result = mutate(
                request.match_info["id"],
                body,
                operation=operation,
                provider=request.match_info.get("provider"),
            )
        return web.json_response(result)
    except ConnectionError as error:
        return web.json_response({"error": str(error)}, status=error.status)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid connection request"}, status=400)


def register(app):
    prefix = "/api/capabilities/platform/connections"
    app.router.add_get(prefix, connections)
    app.router.add_put(prefix + "/{id}", connections)
    app.router.add_delete(prefix + "/{id}", connections)
    app.router.add_put(prefix + "/{id}/bindings/{provider}", connections)
