"""Repertoire HTTP boundary; the parent application's auth remains authoritative."""
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music import DomainError, RepertoireStore


def register(app: web.Application, store: RepertoireStore | None = None):
    store = store or RepertoireStore(config_dir() / "capabilities" / "music", NativeArtifactProvider())

    async def handle(request):
        try:
            item_id = request.match_info.get("id")
            if request.method == "GET":
                if item_id:
                    return web.json_response({"item": store.get(item_id)})
                return web.json_response({"items": store.list(offset=int(request.query.get("offset", 0)), limit=int(request.query.get("limit", 50)))})
            data = await read_json_body(request)
            if request.path.endswith("/practice"):
                return web.json_response(store.practice(item_id, data))
            if request.method == "PATCH":
                return web.json_response({"item": store.update(item_id, data)})
            return web.json_response({"item": store.create(data)}, status=201)
        except DomainError as exc:
            return web.json_response({"error": exc.code, "message": str(exc)}, status=exc.status)
        except (ValueError, TypeError):
            return web.json_response({"error": "invalid_input", "message": "Invalid request body or pagination"}, status=400)

    prefix = "/api/capabilities/music/items"
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_get(prefix + "/{id}", handle)
    app.router.add_patch(prefix + "/{id}", handle)
    app.router.add_post(prefix + "/{id}/practice", handle)
