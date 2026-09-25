from aiohttp import web

from gideon.core.http_request import read_json_body

from .game_assets import GameAssets
from .store import Conflict, NotFound


def register_game_assets(app, store, artifacts=None):
    service = GameAssets(store, artifacts)

    async def handle(request):
        try:
            key, action = (
                request.match_info.get("id"),
                request.path.rstrip("/").rsplit("/", 1)[-1],
            )
            if request.method == "GET":
                result = service.get(key) if key else service.list()
            else:
                body = await read_json_body(request)
                if action == "projects":
                    result = {"project": service.create(body)}
                elif action == "bindings":
                    result = {"project": service.bind(key, body)}
                elif action in ("compile", "publish"):
                    if not isinstance(body, dict) or set(body) != {"revision"}:
                        raise ValueError("Expected revision only")
                    result = {action: getattr(service, action)(key, body["revision"])}
                else:
                    raise ValueError("Unknown game asset operation")
            return web.json_response(
                result,
                status=(
                    201 if request.method == "POST" and action == "projects" else 200
                ),
            )
        except NotFound as exc:
            return web.json_response({"error": str(exc)}, status=404)
        except Conflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    prefix = "/api/capabilities/experience/game-assets/projects"
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_get(prefix + "/{id}", handle)
    app.router.add_post(prefix + "/{id}/bindings", handle)
    app.router.add_post(prefix + "/{id}/compile", handle)
    app.router.add_post(prefix + "/{id}/publish", handle)
