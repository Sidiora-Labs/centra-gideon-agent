from aiohttp import web

from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore

STORE_KEY = web.AppKey("media_sketch_store", SketchStore)


async def dispatch(request):
    try:
        if request.query:
            raise SketchError("Query parameters are not accepted")
        store = request.app[STORE_KEY]
        sketch_id = request.match_info.get("id")
        action = request.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            if action == "source":
                data, mime = store.source(store.get(sketch_id))
                return web.Response(body=data, content_type=mime)
            return web.json_response(store.get(sketch_id) if sketch_id else {"items": store.list()})
        if request.content_length and request.content_length > 1024 * 1024:
            raise SketchError("Request too large", 413)
        try:
            body = await request.json()
        except (ValueError, TypeError) as exc:
            raise SketchError("Invalid JSON") from exc
        if action == "export":
            return web.json_response(store.export(sketch_id, body))
        if request.method == "PUT":
            return web.json_response(store.update(sketch_id, body))
        return web.json_response(store.create(body), status=201)
    except SketchError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


def register(app):
    if STORE_KEY not in app:
        from gideon.core.config.loader import config_dir
        from gideon.workspace.artifacts.registry import get_provider
        app[STORE_KEY] = SketchStore(config_dir() / "capabilities/media/sketches.sqlite3", get_provider())
    prefix = "/api/capabilities/media/sketches"
    app.router.add_get(prefix, dispatch)
    app.router.add_post(prefix, dispatch)
    app.router.add_get(prefix + "/{id}", dispatch)
    app.router.add_put(prefix + "/{id}", dispatch)
    app.router.add_get(prefix + "/{id}/source", dispatch)
    app.router.add_post(prefix + "/{id}/export", dispatch)
