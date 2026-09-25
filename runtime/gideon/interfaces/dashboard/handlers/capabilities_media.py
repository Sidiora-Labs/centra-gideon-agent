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
    from gideon.workspace.capabilities.media.library_http import register_library
    register_library(app, app[STORE_KEY].artifacts)
    from gideon.workspace.capabilities.media.annotations import AnnotationStore
    from gideon.workspace.capabilities.media.annotations_http import register_annotations
    register_annotations(app, AnnotationStore(app[STORE_KEY].path.parent / 'annotations.sqlite3', app[STORE_KEY].artifacts))
    from gideon.workspace.capabilities.media.jobs import MediaJobs
    from gideon.workspace.capabilities.media.jobs_http import register_jobs
    register_jobs(app, MediaJobs(app[STORE_KEY].path.parent / 'jobs.sqlite3', app[STORE_KEY]))

    from gideon.workspace.capabilities.media.readiness import MediaReadiness
    from gideon.workspace.capabilities.media.readiness_http import register_readiness
    register_readiness(app, MediaReadiness(app[STORE_KEY].path.parent / "readiness.sqlite3"))

    from gideon.workspace.capabilities.media.images_http import register_images
    register_images(app)

    from gideon.workspace.capabilities.media.datasets_http import register_datasets
    register_datasets(app)

    from gideon.workspace.capabilities.media.timelines_http import register_timelines
    register_timelines(app)

    from gideon.workspace.capabilities.media.episodes_http import register_episodes
    register_episodes(app)
