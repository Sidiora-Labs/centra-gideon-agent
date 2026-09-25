"""Human planning API and standard calendar export."""
from pathlib import Path
from aiohttp import web
from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_goals", GoalStore)
PREFIX = "/api/capabilities/identity/goals"


async def handle(request):
    store = request.app[KEY]
    kind = request.match_info["kind"]
    identifier = request.match_info.get("id")
    try:
        if kind == "calendar":
            return web.Response(text=store.calendar(), content_type="text/calendar", headers={"Content-Disposition": 'attachment; filename="human-plans.ics"'})
        singular = "goal" if kind == "goals" else "session"
        if request.method == "GET":
            result = getattr(store, "get_" + singular)(identifier) if identifier else getattr(store, "list_" + kind)()
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            result = getattr(store, "save_" + singular)(**body)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "Human planning record not found"}, status=404)
    except (TypeError, ValueError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, store_path: Path | None = None):
    app[KEY] = GoalStore(store_path or config_dir() / "capabilities/identity/goals.sqlite3")
    app.router.add_get(PREFIX + "/{kind:goals|sessions|calendar}", handle)
    app.router.add_get(PREFIX + "/{kind:goals|sessions}/{id}", handle)
    app.router.add_post(PREFIX + "/{kind:goals|sessions}", handle)
