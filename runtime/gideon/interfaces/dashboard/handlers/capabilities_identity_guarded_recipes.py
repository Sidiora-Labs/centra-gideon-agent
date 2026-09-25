"""Existing-session guarded recipe dispatch and actual human permission decisions."""

from pathlib import Path

from aiohttp import web

from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.guarded_recipes import GuardedRecipes
from gideon.workspace.capabilities.identity.store import ConflictError

HOME = web.AppKey("identity_guarded_home", Path)
PREFIX = "/api/capabilities/identity/guarded-recipes"


async def handle(request):
    try:
        service = GuardedRecipes(request.app[HOME], request.app.get("state"))
        operation = request.match_info.get("operation", "list")
        if request.method == "GET":
            if operation == "catalog":
                result = await service.catalog(request.query.get("session_key", ""))
            elif operation == "history":
                result = service.store.history(request.match_info["id"])
            elif operation == "runs":
                result = service.get_run(request.match_info["id"])
            else:
                result = service.store.list()
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
            result = await getattr(service, operation)(**body)
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "Guarded recipe not found"}, status=404)
    except (ValueError, TypeError, PermissionError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, home: Path | None = None):
    app[HOME] = home or config_dir()
    app.router.add_get(PREFIX, handle)
    app.router.add_get(PREFIX + "/{operation:catalog}", handle)
    app.router.add_get(PREFIX + "/{operation:runs|history}/{id}", handle)
    app.router.add_post(
        PREFIX + "/{operation:save|restore|begin|advance|decide|cancel}", handle
    )
