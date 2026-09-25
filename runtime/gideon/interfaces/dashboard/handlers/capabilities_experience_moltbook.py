"""Authenticated dashboard routes for the Moltbook adapter."""

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.experience.moltbook import (
    MoltbookAdapter,
    MoltbookError,
)

PREFIX = "/api/capabilities/experience/moltbook"


def owner(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")


def store(request):
    factory = request.app.get("moltbook_factory")
    return factory() if factory else MoltbookAdapter(config_dir())


def error(exc):
    return web.json_response(
        {"error": str(exc), "code": exc.code, "retry_after": exc.retry_after},
        status=exc.status,
    )


async def config(request):
    owner(request)
    try:
        return web.json_response(
            store(request).configure(await read_json_body(request))
            if request.method == "POST"
            else store(request).config()
        )
    except MoltbookError as exc:
        return error(exc)


async def reads(request):
    owner(request)
    try:
        action = request.match_info["action"]
        service = store(request)
        if action == "history":
            result = {"items": service.history()}
        elif action == "feed":
            result = await service.read(
                "feed",
                sort=request.query.get("sort", "new"),
                limit=int(request.query.get("limit", "15")),
            )
        else:
            result = await service.read(action)
        return web.json_response(result)
    except (MoltbookError, ValueError) as exc:
        return error(
            exc if isinstance(exc, MoltbookError) else MoltbookError("Invalid query")
        )


async def comments(request):
    owner(request)
    try:
        return web.json_response(
            await store(request).read("comments", post_id=request.match_info["post_id"])
        )
    except MoltbookError as exc:
        return error(exc)


async def write(request):
    owner(request)
    try:
        body = await read_json_body(request)
        approved = body.pop("approved", False)
        body["kind"] = request.match_info["kind"]
        return web.json_response(
            await store(request).write(body, approved=approved), status=202
        )
    except MoltbookError as exc:
        return error(exc)


def register(app):
    app.router.add_get(PREFIX + "/config", config, name="moltbook-config-get")
    app.router.add_post(PREFIX + "/config", config, name="moltbook-config-save")
    app.router.add_get(
        PREFIX + "/{action:profile|status|feed|history}", reads, name="moltbook-read"
    )
    app.router.add_get(
        PREFIX + "/posts/{post_id}/comments", comments, name="moltbook-comments"
    )
    app.router.add_post(PREFIX + "/{kind:post|comment}", write, name="moltbook-write")
