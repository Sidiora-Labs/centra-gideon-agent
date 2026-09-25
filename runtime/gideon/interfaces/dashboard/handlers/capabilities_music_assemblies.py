"""Authored assembly schemas, diagnostics, refinement and exports."""

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.assembly import AssemblyStore
from gideon.workspace.capabilities.music.store import DomainError


def register(app, store=None):
    home = config_dir()
    store = store or AssemblyStore(
        home, NativeArtifactProvider(root=home / "artifacts")
    )

    async def source(request):
        artifact = store.artifacts.get(
            request.match_info["slug"], version=int(request.match_info["version"])
        )
        if not artifact or artifact.kind != "text":
            raise web.HTTPNotFound()
        return web.Response(
            text=artifact.content,
            content_type="text/javascript",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    async def handle(request):
        try:
            item_id = request.match_info.get("id")
            if request.path.endswith("/history"):
                return web.json_response({"items": store.history(item_id)})
            if request.path.endswith("/export"):
                return web.json_response(
                    store.export(item_id, await read_json_body(request))
                )
            if request.path.endswith("/refine"):
                return web.json_response(
                    {"item": store.refine(item_id, await read_json_body(request))}
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

    prefix = "/api/capabilities/music/assemblies"
    app.router.add_get(prefix + "/artifacts/{slug}/{version}/source", source)
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_get(prefix + "/{id}", handle)
    app.router.add_patch(prefix + "/{id}", handle)
    app.router.add_get(prefix + "/{id}/history", handle)
    app.router.add_post(prefix + "/{id}/refine", handle)
    app.router.add_post(prefix + "/{id}/export", handle)
