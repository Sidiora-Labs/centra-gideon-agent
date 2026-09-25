"""Progress profile editing and direct source projections."""

from pathlib import Path

from aiohttp import web

from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.progress import ProgressStore
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_progress", ProgressStore)
PREFIX = "/api/capabilities/identity/progress"


async def handle(request):
    try:
        store = request.app[KEY]
        if request.method == "GET":
            result = store.sheet(as_of=request.query.get("as_of"))
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            result = store.configure(**body)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except (ValueError, TypeError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, store_path: Path | None = None):
    app[KEY] = ProgressStore(
        store_path or config_dir() / "capabilities/identity/progress.sqlite3"
    )
    app.router.add_get(PREFIX, handle)
    app.router.add_put(PREFIX, handle)
