from aiohttp import web

from .readiness import MediaReadiness
from .sketches import SketchError

READINESS_KEY = web.AppKey("media_readiness", MediaReadiness)


async def dispatch(request):
    try:
        if request.query:
            raise SketchError("Query parameters are not accepted")
        service = request.app[READINESS_KEY]
        if request.method == "GET":
            result = service.get()
        else:
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError("Invalid JSON") from exc
            if body != {}:
                raise SketchError("Refresh accepts an empty object only")
            result = await service.refresh()
        return web.json_response(result)
    except SketchError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


def register_readiness(app, service):
    app[READINESS_KEY] = service
    app.router.add_get("/api/capabilities/media/readiness", dispatch)
    app.router.add_post("/api/capabilities/media/readiness", dispatch)
