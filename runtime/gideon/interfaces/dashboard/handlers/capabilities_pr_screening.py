import asyncio

import aiohttp
from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform import pr_screening as screening


async def endpoint(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        if request.method == "GET":
            result = screening.view()
        else:
            body = await read_json_body(request)
            actor = "user:" + str(request["user"])
            identifier = request.match_info.get("id")
            if not identifier:
                result = await screening.capture(body, actor)
            elif not isinstance(body, dict) or set(body) != {"action", "revision"}:
                raise ValueError("Action and observed revision required")
            elif body["action"] == "screen":
                result = await screening.screen(identifier, body["revision"], actor)
            elif body["action"] == "authorize":
                result = await screening.submit(identifier, body["revision"], actor)
            else:
                raise ValueError("Unsupported review action")
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
        return web.json_response(
            {
                "error": "PR screening unavailable; verify credential, complete source and current revision"
            },
            status=409,
        )


def register(app):
    app.router.add_get("/api/capabilities/platform/pr-screening", endpoint)
    app.router.add_post("/api/capabilities/platform/pr-screening", endpoint)
    app.router.add_post("/api/capabilities/platform/pr-screening/{id}", endpoint)
