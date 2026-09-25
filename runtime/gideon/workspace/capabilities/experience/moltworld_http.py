from aiohttp import web
from gideon.core.http_request import read_json_body
from .moltworld import Moltworld, RemoteError
from .store import Conflict, NotFound


def register_moltworld(app, store, service=None):
    service = service or Moltworld(store)

    async def handle(request):
        try:
            action = request.path.rstrip("/").rsplit("/", 1)[-1]
            if request.method == "GET":
                if action == "moltworld": result = {"readiness": service.readiness(), "history": service.history()}
                elif action == "status": result = await service.status()
                elif action == "history": result = {"history": service.history()}
                else: raise ValueError("Unknown Moltworld read operation")
            else:
                body = await read_json_body(request)
                if action == "config": result = {"config": service.configure(body)}
                elif action == "observe": result = await service.observe(body)
                elif action == "actions": result = {"receipt": await service.action(body)}
                else: raise ValueError("Unknown Moltworld operation")
            return web.json_response(result)
        except NotFound as exc: return web.json_response({"error": str(exc)}, status=404)
        except RemoteError as exc: return web.json_response({"error": str(exc), "retry_after": exc.retry_after}, status=exc.status)
        except Conflict as exc: return web.json_response({"error": str(exc)}, status=409)
        except (ValueError, TypeError) as exc: return web.json_response({"error": str(exc)}, status=400)

    prefix = "/api/capabilities/experience/moltworld"
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix + "/config", handle)
    app.router.add_get(prefix + "/status", handle)
    app.router.add_post(prefix + "/observe", handle)
    app.router.add_post(prefix + "/actions", handle)
    app.router.add_get(prefix + "/history", handle)
