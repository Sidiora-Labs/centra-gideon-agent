"""Music video project and render routes using the shared per-home renderer."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.music.store import DomainError
from gideon.workspace.capabilities.music.video import default_store


def register(app, store=None):
    store = store or default_store()

    async def startup(_):
        if not store.tasks:
            store.recover()

    async def cleanup(_):
        await store.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)

    async def handle(request):
        try:
            item_id = request.match_info.get("id")
            is_job = "/jobs" in request.path
            if request.path.endswith("/cancel"):
                if await read_json_body(request) != {}:
                    raise DomainError("Cancel takes no fields")
                return web.json_response({"job": await store.cancel(item_id)})
            if is_job:
                if request.method == "POST":
                    return web.json_response(
                        {"job": await store.submit(await read_json_body(request))},
                        status=202,
                    )
                return web.json_response(
                    {"job": store.get_job(item_id)}
                    if item_id
                    else {"jobs": store.jobs()}
                )
            if request.method == "GET":
                return web.json_response(
                    {"item": store.get(item_id)}
                    if item_id
                    else {
                        "items": store.list(
                            int(request.query.get("offset", 0)),
                            int(request.query.get("limit", 50)),
                        )
                    }
                )
            data = await read_json_body(request)
            return web.json_response(
                {
                    "item": (
                        store.update(item_id, data) if item_id else store.create(data)
                    )
                },
                status=200 if item_id else 201,
            )
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response(
                {"error": getattr(exc, "code", "invalid_input"), "message": str(exc)},
                status=getattr(exc, "status", 400),
            )

    prefix = "/api/capabilities/music/videos"
    app.router.add_get(prefix + "/jobs", handle)
    app.router.add_post(prefix + "/jobs", handle)
    app.router.add_get(prefix + "/jobs/{id}", handle)
    app.router.add_post(prefix + "/jobs/{id}/cancel", handle)
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_get(prefix + "/{id}", handle)
    app.router.add_patch(prefix + "/{id}", handle)
