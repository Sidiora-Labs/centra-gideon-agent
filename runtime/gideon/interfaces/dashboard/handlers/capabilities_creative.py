"""Creative ingredient HTTP surface; the application binds one runtime home."""

from aiohttp import web

from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.moodboards import BoardStore

STORE = web.AppKey("creative_store", IngredientStore)
PREFIX = "/api/capabilities/creative/ingredients"
from gideon.workspace.capabilities.creative.universes import UniverseStore
from gideon.workspace.capabilities.creative.graph import UniverseGraph
from gideon.workspace.capabilities.creative.authors import AuthorStore
from gideon.workspace.capabilities.creative.works import WorkStore
WORKS = web.AppKey("creative_works", WorkStore)
AUTHORS = web.AppKey("creative_authors", AuthorStore)
GRAPHS = web.AppKey("creative_graphs", UniverseGraph)
UNIVERSES = web.AppKey("creative_universes", UniverseStore)

BOARDS = web.AppKey("creative_boards", BoardStore)


async def boards(request):
    store = request.app[BOARDS]
    id = request.match_info.get("id")
    action = request.path.rsplit("/", 1)[-1]
    try:
        if request.method == "GET":
            if set(request.query) - {"q", "offset", "limit", "revision"}:
                raise CatalogError("Unexpected query parameter")
            if action == "sources":
                result = store.sources(request.query.get("q", ""))
            elif action == "export":
                revision = int(request.query["revision"]) if "revision" in request.query else None
                return web.json_response(store.export(id, revision), headers={"Content-Disposition": f'attachment; filename="moodboard-{id}.json"'})
            elif action == "revisions":
                result = {"items": store.revisions(id)}
            elif id:
                result = store.get(id)
            else:
                result = store.list(request.query.get("q", ""), int(request.query.get("offset", 0)), int(request.query.get("limit", 25)))
        else:
            payload = await request.json()
            result = store.restore(id, payload) if action == "restore" else store.update(id, payload) if id else store.create(payload)
        return web.json_response(result, status=201 if request.method == "POST" and not id else 200)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def universes(request):
    store = request.app[UNIVERSES]
    id = request.match_info.get("id")
    action = request.path.rsplit("/", 1)[-1]
    try:
        if request.method == "GET":
            if set(request.query) - {"q", "offset", "limit", "revision"}:
                raise CatalogError("Unexpected query parameter")
            if action == "export":
                revision = int(request.query["revision"]) if "revision" in request.query else None
                return web.json_response(store.export(id, revision), headers={"Content-Disposition": f'attachment; filename="universe-{id}.json"'})
            elif action == "revisions":
                result = {"items": store.revisions(id)}
            elif id:
                result = store.get(id)
            else:
                result = store.list(request.query.get("q", ""), int(request.query.get("offset", 0)), int(request.query.get("limit", 25)))
        else:
            payload = await request.json()
            result = store.restore(id, payload) if action == "restore" else store.update(id, payload) if id else store.create(payload)
        return web.json_response(result, status=201 if request.method == "POST" and not id else 200)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def works(request):
    store = request.app[WORKS]
    id = request.match_info.get("id")
    action = request.path.rsplit("/", 1)[-1]
    try:
        if request.method == "GET":
            if set(request.query) - {"q", "offset", "limit", "revision"}:
                raise CatalogError("Unexpected query parameter")
            if request.match_info.get("draft_id"):
                result = store.read_draft(id, request.match_info["draft_id"])
            elif action == "drafts":
                result = store.drafts(id)
            elif action == "context":
                result = store.context(id)
            elif action == "export":
                revision = int(request.query["revision"]) if "revision" in request.query else None
                return web.json_response(store.export(id, revision), headers={"Content-Disposition": f'attachment; filename="work-{id}.json"'})
            elif action == "revisions":
                result = {"items": store.revisions(id)}
            elif id:
                result = store.get(id)
            else:
                result = store.list(request.query.get("q", ""), int(request.query.get("offset", 0)), int(request.query.get("limit", 25)))
        else:
            payload = await request.json()
            result = store.draft(id, payload) if action == "drafts" else store.restore(id, payload) if action == "restore" else store.update(id, payload) if id else store.create(payload)
        return web.json_response(result, status=201 if request.method == "POST" and not id else 200)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def authors(request):
    store = request.app[AUTHORS]
    id = request.match_info.get("id")
    action = request.path.rsplit("/", 1)[-1]
    try:
        if request.method == "GET":
            if set(request.query) - {"q", "offset", "limit", "revision"}:
                raise CatalogError("Unexpected query parameter")
            if action == "sources":
                result = store.sources(request.query.get("q", ""))
            elif action == "brief":
                result = store.brief(id, int(request.query["revision"]) if "revision" in request.query else None)
            elif action == "export":
                revision = int(request.query["revision"]) if "revision" in request.query else None
                return web.json_response(store.export(id, revision), headers={"Content-Disposition": f'attachment; filename="author-{id}.json"'})
            elif action == "revisions":
                result = {"items": store.revisions(id)}
            elif id:
                result = store.get(id)
            else:
                result = store.list(request.query.get("q", ""), int(request.query.get("offset", 0)), int(request.query.get("limit", 25)))
        else:
            payload = await request.json()
            result = store.restore(id, payload) if action == "restore" else store.update(id, payload) if id else store.create(payload)
        return web.json_response(result, status=201 if request.method == "POST" and not id else 200)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


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


async def universe_graph(request):
    try:
        if request.query:
            raise CatalogError("Unexpected query parameter")
        graph = request.app[GRAPHS]
        id = request.match_info['id']
        action = request.path.rsplit('/', 1)[-1]
        result = graph.graph(id) if action == 'graph' else getattr(graph, action.replace('-', '_'))(id, await request.json())
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


def register(app):
    if STORE not in app:
        app[STORE] = IngredientStore()
    app[WORKS] = WorkStore(app[STORE].home)
    app.router.add_get("/api/capabilities/creative/works", works)
    app.router.add_post("/api/capabilities/creative/works", works)
    app.router.add_get("/api/capabilities/creative/works/{id}", works)
    app.router.add_patch("/api/capabilities/creative/works/{id}", works)
    app.router.add_get("/api/capabilities/creative/works/{id}/revisions", works)
    app.router.add_post("/api/capabilities/creative/works/{id}/restore", works)
    app.router.add_get("/api/capabilities/creative/works/{id}/export", works)
    app.router.add_get("/api/capabilities/creative/works/{id}/context", works)
    app.router.add_get("/api/capabilities/creative/works/{id}/drafts", works)
    app.router.add_post("/api/capabilities/creative/works/{id}/drafts", works)
    app.router.add_get("/api/capabilities/creative/works/{id}/drafts/{draft_id}", works)
    app[AUTHORS] = AuthorStore(app[STORE].home)
    app.router.add_get("/api/capabilities/creative/authors", authors)
    app.router.add_post("/api/capabilities/creative/authors", authors)
    app.router.add_get("/api/capabilities/creative/authors/sources", authors)
    app.router.add_get("/api/capabilities/creative/authors/{id}", authors)
    app.router.add_patch("/api/capabilities/creative/authors/{id}", authors)
    app.router.add_get("/api/capabilities/creative/authors/{id}/revisions", authors)
    app.router.add_post("/api/capabilities/creative/authors/{id}/restore", authors)
    app.router.add_get("/api/capabilities/creative/authors/{id}/export", authors)
    app.router.add_get("/api/capabilities/creative/authors/{id}/brief", authors)
    app[UNIVERSES] = UniverseStore(app[STORE].home)
    app[GRAPHS] = UniverseGraph(app[UNIVERSES])
    app.router.add_get("/api/capabilities/creative/universes/{id}/graph", universe_graph)
    app.router.add_post("/api/capabilities/creative/universes/{id}/merge-preview", universe_graph)
    app.router.add_post("/api/capabilities/creative/universes/{id}/merge", universe_graph)
    app.router.add_get("/api/capabilities/creative/universes", universes)
    app.router.add_post("/api/capabilities/creative/universes", universes)
    app.router.add_get("/api/capabilities/creative/universes/{id}", universes)
    app.router.add_patch("/api/capabilities/creative/universes/{id}", universes)
    app.router.add_get("/api/capabilities/creative/universes/{id}/revisions", universes)
    app.router.add_post("/api/capabilities/creative/universes/{id}/restore", universes)
    app.router.add_get("/api/capabilities/creative/universes/{id}/export", universes)
    app[BOARDS] = BoardStore(app[STORE].home)
    app.router.add_get("/api/capabilities/creative/boards", boards)
    app.router.add_post("/api/capabilities/creative/boards", boards)
    app.router.add_get("/api/capabilities/creative/boards/sources", boards)
    app.router.add_get("/api/capabilities/creative/boards/{id}", boards)
    app.router.add_patch("/api/capabilities/creative/boards/{id}", boards)
    app.router.add_get("/api/capabilities/creative/boards/{id}/revisions", boards)
    app.router.add_post("/api/capabilities/creative/boards/{id}/restore", boards)
    app.router.add_get("/api/capabilities/creative/boards/{id}/export", boards)
    app.router.add_get(PREFIX, handle)
    app.router.add_post(PREFIX, handle)
    app.router.add_get(PREFIX + "/{id}", handle)
    app.router.add_patch(PREFIX + "/{id}", handle)
    app.router.add_get(PREFIX + "/{id}/revisions", handle)
    app.router.add_post(PREFIX + "/{id}/restore", handle)
