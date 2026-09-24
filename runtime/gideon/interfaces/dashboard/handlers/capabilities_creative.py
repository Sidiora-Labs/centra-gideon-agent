"""Creative ingredient HTTP surface; the application binds one runtime home."""

from aiohttp import web

from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore

STORE = web.AppKey("creative_store", IngredientStore)
PREFIX = "/api/capabilities/creative/ingredients"


async def handle(request):
    store = request.app[STORE]
    try:
        id = request.match_info.get("id")
        action = request.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            if action == "revisions":
                return web.json_response({"items": store.revisions(id)})
            if id:
                record = store.get(id)
            else:
                if set(request.query) - {"q", "type", "tag", "offset", "limit"}:
                    raise CatalogError("Unexpected query parameter")
                query = dict(request.query)
                for key in ("offset", "limit"):
                    if key in query:
                        query[key] = int(query[key])
                result = store.list(**query)
                result["items"] = [{**item, "source_status": store.source_status(item)} for item in result["items"]]
                return web.json_response(result)
        else:
            payload = await request.json()
            if action == "restore":
                record = store.restore(id, payload)
            elif id:
                record = store.update(id, payload)
            else:
                record = store.create(payload)
        return web.json_response({**record, "source_status": store.source_status(record)}, status=201 if request.method == "POST" and not id else 200)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


def register(app):
    if STORE not in app:
        app[STORE] = IngredientStore()
    app.router.add_get(PREFIX, handle)
    app.router.add_post(PREFIX, handle)
    app.router.add_get(PREFIX + "/{id}", handle)
    app.router.add_patch(PREFIX + "/{id}", handle)
    app.router.add_get(PREFIX + "/{id}/revisions", handle)
    app.router.add_post(PREFIX + "/{id}/restore", handle)
