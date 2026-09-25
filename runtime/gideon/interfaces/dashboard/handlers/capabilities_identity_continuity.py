"""Human controls for heartbeat policy and canonical memory slots."""
from pathlib import Path
from aiohttp import web
from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.continuity import ContinuityStore
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_continuity", ContinuityStore)
PREFIX = "/api/capabilities/identity/continuity"


async def handle(request):
    try:
        store = request.app[KEY]
        if request.method == "GET":
            result = store.status()
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if request.method == "POST" and set(body) != {"slot", "text"}:
                raise ValueError("Expected only slot and text")
            operation = "configure" if request.method == "PUT" else "remove_anchor" if request.path.endswith("/remove") else "append_anchor"
            result = getattr(store, operation)(**body)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except (ValueError, TypeError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, home: Path | None = None):
    app[KEY] = ContinuityStore(home or config_dir())
    app.router.add_get(PREFIX, handle)
    app.router.add_put(PREFIX, handle)
    app.router.add_post(PREFIX + "/anchors", handle)
    app.router.add_post(PREFIX + "/anchors/remove", handle)
