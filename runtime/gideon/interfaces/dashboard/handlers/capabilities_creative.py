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
from gideon.workspace.capabilities.creative.polishing import PolishingStore
from gideon.workspace.capabilities.creative.stories import StoryStore
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.continuity import ContinuityStore
from gideon.workspace.capabilities.creative.voice import VoiceStore
from gideon.workspace.capabilities.creative.editorial import EditorialStore
EDITORIAL = web.AppKey("creative_editorial", EditorialStore)
VOICE = web.AppKey("creative_voice", VoiceStore)
CONTINUITY = web.AppKey("creative_continuity", ContinuityStore)
SERIES = web.AppKey("creative_series", SeriesStore)
STORIES = web.AppKey("creative_stories", StoryStore)
POLISHING = web.AppKey("creative_polishing", PolishingStore)
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


async def series(request):
    store = request.app[SERIES]
    id = request.match_info.get("id")
    action = request.path.rsplit("/", 1)[-1]
    try:
        if request.method == "GET":
            if set(request.query) - {"q", "offset", "limit", "revision"}:
                raise CatalogError("Unexpected query parameter")
            if action == "export":
                revision = int(request.query["revision"]) if "revision" in request.query else None
                return web.json_response(store.export(id, revision), headers={"Content-Disposition": f'attachment; filename="series-{id}.json"'})
            elif action == "revisions":
                result = {"items": store.revisions(id)}
            elif id:
                result = store.get(id)
            else:
                result = store.list(request.query.get("q", ""), int(request.query.get("offset", 0)), int(request.query.get("limit", 25)))
        else:
            payload = await request.json()
            chapter_id = request.match_info.get("chapter_id")
            if chapter_id:
                result = await store.draft(id, chapter_id, payload) if action == "draft" else getattr(store, action)(id, chapter_id, payload)
            else:
                result = store.restore(id, payload) if action == "restore" else store.update(id, payload) if id else store.create(payload)
        return web.json_response(result, status=201 if request.method == "POST" and not id else 200)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def stories(request):
    store = request.app[STORIES]
    id = request.match_info.get("id")
    action = request.path.rsplit("/", 1)[-1]
    try:
        if request.method == "GET":
            if set(request.query) - {"q", "offset", "limit", "revision"}:
                raise CatalogError("Unexpected query parameter")
            if action == "suggestions":
                result = store.suggestions(id)
            elif action == "export":
                revision = int(request.query["revision"]) if "revision" in request.query else None
                return web.json_response(store.export(id, revision), headers={"Content-Disposition": f'attachment; filename="story-{id}.json"'})
            elif action == "revisions":
                result = {"items": store.revisions(id)}
            elif id:
                result = store.get(id)
            else:
                result = store.list(request.query.get("q", ""), int(request.query.get("offset", 0)), int(request.query.get("limit", 25)))
        else:
            payload = await request.json()
            if action == "suggestions":
                result = await store.suggest(id, payload)
            elif action == "adopt":
                result = store.adopt(id, payload)
            elif action == "work":
                result = store.create_work(id, payload)
            else:
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


async def polishing(request):
    try:
        if request.query:
            raise CatalogError("Unexpected query parameter")
        store = request.app[POLISHING]
        id = request.match_info['id']
        proposal = request.match_info.get('proposal_id')
        if request.method == 'GET':
            result = store.get(id, proposal) if proposal else store.list(id)
        else:
            payload = await request.json()
            result = store.promote(id, proposal, payload) if proposal else await store.propose(id, payload)
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def continuity(request):
    try:
        if request.query:
            raise CatalogError("Unexpected query parameter")
        store = request.app[CONTINUITY]
        id = request.match_info['id']
        if request.method == 'GET':
            result = store.export(id) if request.path.endswith('/export') else store.get(id)
        elif 'proposal_id' in request.match_info:
            result = store.accept(id, request.match_info['proposal_id'], await request.json())
        else:
            result = await store.propose(id, await request.json())
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def voice(request):
    try:
        if request.query:
            raise CatalogError("Unexpected query parameter")
        store = request.app[VOICE]
        id = request.match_info['id']
        result = store.configure(id, await request.json()) if request.method == 'PATCH' else store.report(id)
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def editorial(request):
    try:
        if request.query:
            raise CatalogError("Unexpected query parameter")
        store = request.app[EDITORIAL]
        id = request.match_info['id']
        if request.method == 'GET':
            result = store.get(id)
        elif request.path.endswith('/context'):
            result = store.context.bind(id, await request.json())
        elif 'finding_id' in request.match_info:
            result = await store.repair(id, request.match_info['run_id'], request.match_info['finding_id'], await request.json())
        else:
            result = await store.run_async(id, await request.json())
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


async def editorial_controls(request):
    try:
        if request.query:
            raise CatalogError("Unexpected query parameter")
        store = request.app[EDITORIAL]
        id = request.match_info['id']
        path = request.path
        payload = await request.json()
        if path.endswith('/policy'):
            result = store.controls.configure(id, payload, {row['id'] for row in store.catalog(id)})
        elif '/custom-checks' in path:
            operation = 'delete' if request.method == 'DELETE' else 'update' if 'custom_id' in request.match_info else 'create'
            result = store.controls.custom(id, {**payload, 'operation': operation, 'id': request.match_info.get('custom_id')})
        elif path.endswith('/reviews'):
            result = await store.controls.review(id, payload)
        elif 'finding_id' in request.match_info:
            result = await store.controls.cut(store, id, request.match_info['run_id'], request.match_info['finding_id'], payload)
        elif path.endswith('/apply'):
            result = store.controls.apply_cut(id, request.match_info['cut_id'], payload)
        else:
            result = store.controls.undo_cut(id, request.match_info['cut_id'], payload)
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc), "code": "creative_invalid"}, status=getattr(exc, "status", 400))


def register(app):
    if STORE not in app:
        app[STORE] = IngredientStore()
    from gideon.interfaces.dashboard.handlers.capabilities_creative_peer_feedback import register as register_peer_feedback
    register_peer_feedback(app, app[STORE].home)
    app[SERIES] = SeriesStore(app[STORE].home)
    app[VOICE] = VoiceStore(app[SERIES])
    app.router.add_get("/api/capabilities/creative/series/{id}/voice", voice)
    app.router.add_patch("/api/capabilities/creative/series/{id}/voice", voice)
    app.router.add_get("/api/capabilities/creative/series/{id}/voice/export", voice)
    app.router.add_get("/api/capabilities/creative/series", series)
    app.router.add_post("/api/capabilities/creative/series", series)
    app.router.add_get("/api/capabilities/creative/series/{id}", series)
    app.router.add_patch("/api/capabilities/creative/series/{id}", series)
    app.router.add_get("/api/capabilities/creative/series/{id}/revisions", series)
    app.router.add_post("/api/capabilities/creative/series/{id}/restore", series)
    app.router.add_get("/api/capabilities/creative/series/{id}/export", series)
    app.router.add_post("/api/capabilities/creative/series/{id}/chapters/{chapter_id}/prepare", series)
    app.router.add_post("/api/capabilities/creative/series/{id}/chapters/{chapter_id}/draft", series)
    app.router.add_post("/api/capabilities/creative/series/{id}/chapters/{chapter_id}/review", series)
    app[STORIES] = StoryStore(app[STORE].home)
    app.router.add_get("/api/capabilities/creative/stories", stories)
    app.router.add_post("/api/capabilities/creative/stories", stories)
    app.router.add_get("/api/capabilities/creative/stories/{id}", stories)
    app.router.add_patch("/api/capabilities/creative/stories/{id}", stories)
    app.router.add_get("/api/capabilities/creative/stories/{id}/revisions", stories)
    app.router.add_post("/api/capabilities/creative/stories/{id}/restore", stories)
    app.router.add_get("/api/capabilities/creative/stories/{id}/export", stories)
    app.router.add_get("/api/capabilities/creative/stories/{id}/suggestions", stories)
    app.router.add_post("/api/capabilities/creative/stories/{id}/suggestions", stories)
    app.router.add_post("/api/capabilities/creative/stories/{id}/adopt", stories)
    app.router.add_post("/api/capabilities/creative/stories/{id}/work", stories)
    app[WORKS] = WorkStore(app[STORE].home)
    app[EDITORIAL] = EditorialStore(app[WORKS])
    app.router.add_get("/api/capabilities/creative/works/{id}/editorial", editorial)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/context", editorial)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/runs", editorial)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/runs/{run_id}/findings/{finding_id}/repair", editorial)
    app.router.add_patch("/api/capabilities/creative/works/{id}/editorial/policy", editorial_controls)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/custom-checks", editorial_controls)
    app.router.add_patch("/api/capabilities/creative/works/{id}/editorial/custom-checks/{custom_id}", editorial_controls)
    app.router.add_delete("/api/capabilities/creative/works/{id}/editorial/custom-checks/{custom_id}", editorial_controls)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/reviews", editorial_controls)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/runs/{run_id}/findings/{finding_id}/cut", editorial_controls)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/cuts/{cut_id}/apply", editorial_controls)
    app.router.add_post("/api/capabilities/creative/works/{id}/editorial/cuts/{cut_id}/undo", editorial_controls)
    app[CONTINUITY] = ContinuityStore(app[WORKS])
    app.router.add_get("/api/capabilities/creative/works/{id}/continuity", continuity)
    app.router.add_get("/api/capabilities/creative/works/{id}/continuity/export", continuity)
    app.router.add_post("/api/capabilities/creative/works/{id}/continuity/proposals", continuity)
    app.router.add_post("/api/capabilities/creative/works/{id}/continuity/proposals/{proposal_id}/accept", continuity)
    app[POLISHING] = PolishingStore(app[WORKS])
    app.router.add_get("/api/capabilities/creative/works/{id}/polishing", polishing)
    app.router.add_post("/api/capabilities/creative/works/{id}/polishing", polishing)
    app.router.add_get("/api/capabilities/creative/works/{id}/polishing/{proposal_id}", polishing)
    app.router.add_post("/api/capabilities/creative/works/{id}/polishing/{proposal_id}/promote", polishing)
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
