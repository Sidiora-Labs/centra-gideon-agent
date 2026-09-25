from aiohttp import web
from gideon.core.http_request import read_json_body
from .native_duplex import NativeDuplex
from .store import Conflict, NotFound


def register_native_duplex(app, store):
    service = NativeDuplex(store)
    prefix = "/api/capabilities/experience/native-duplex"

    async def handle(request):
        try:
            identity = request.match_info.get("id")
            action = request.match_info.get("action")
            if request.method == "GET":
                return web.json_response({"readiness": service.readiness(), "sessions": service.list()} if not identity else service.get(identity))
            body = await read_json_body(request)
            if not identity: result = service.start(body)
            elif action == "capture": result = await service.capture(identity, body.get("revision"))
            elif action == "speak": result = await service.speak(identity, body.get("revision"), body.get("text"))
            elif action == "stop": result = service.stop(identity, body.get("revision"))
            else: raise NotFound("Native duplex action not found")
            return web.json_response(result)
        except NotFound as exc: return web.json_response({"error": str(exc)}, status=404)
        except Conflict as exc: return web.json_response({"error": str(exc)}, status=409)
        except (ValueError, TypeError) as exc: return web.json_response({"error": str(exc)}, status=400)

    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_get(prefix + "/{id}", handle)
    app.router.add_post(prefix + "/{id}/{action}", handle)
