from aiohttp import web

from .downloads import capabilities
from .sketches import SketchError


async def dispatch(request):
    try:
        if request.query:
            raise SketchError("Query parameters are not accepted")
        return web.json_response(capabilities())
    except SketchError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


def register_downloads(app):
    app.router.add_get("/api/capabilities/media/source-download", dispatch)
