from aiohttp import web
from urllib.parse import unquote

from .library import MediaLibrary
from .sketches import SketchError

LIBRARY_KEY = web.AppKey("media_library", MediaLibrary)


async def library_dispatch(request):
    try:
        library = request.app[LIBRARY_KEY]
        artifact_id = request.match_info.get("artifact_id")
        if request.method == "GET":
            if artifact_id and request.query:
                raise SketchError("Detail query parameters are not accepted")
            return web.json_response(library.get(artifact_id) if artifact_id else library.list(dict(request.query)))
        if request.query:
            raise SketchError("Mutation query parameters are not accepted")
        if request.method == "PATCH":
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError("Invalid JSON") from exc
            return web.json_response(library.update(artifact_id, body))
        data = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            data.extend(chunk)
            if len(data) > 16 * 1024 * 1024:
                raise SketchError("Image exceeds 16 MB limit", 413)
        item = library.import_image(bytes(data), filename=unquote(request.headers.get("X-File-Name", "")),
            request_id=request.headers.get("X-Request-ID", ""), name=unquote(request.headers.get("X-Image-Name", "")), mime=request.content_type)
        return web.json_response(item, status=201)
    except SketchError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


def register_library(app, artifacts):
    app[LIBRARY_KEY] = MediaLibrary(artifacts)
    base = "/api/capabilities/media/library"
    app.router.add_get(base, library_dispatch)
    app.router.add_post(base + "/import", library_dispatch)
    app.router.add_get(base + "/{artifact_id}", library_dispatch)
    app.router.add_patch(base + "/{artifact_id}", library_dispatch)
