"""Human hierarchy, milestones and source-linked metric observations."""

from pathlib import Path

from aiohttp import web

from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.goal_plans import GoalPlanStore
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_goal_plans", GoalPlanStore)
PREFIX = "/api/capabilities/identity/goal-plans"


async def handle(request):
    try:
        store = request.app[KEY]
        if request.method == "GET":
            result = (
                store.get(request.match_info["goal_id"])
                if request.match_info.get("goal_id")
                else store.list()
            )
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            result = getattr(
                store,
                (
                    "checkin"
                    if request.match_info["operation"] == "checkins"
                    else "configure"
                ),
            )(**body)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response(
            {"error": "Local goal or source not found"}, status=404
        )
    except (TypeError, ValueError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, home: Path | None = None):
    app[KEY] = GoalPlanStore(
        (home or config_dir()) / "capabilities/identity/goals.sqlite3"
    )
    app.router.add_get(PREFIX, handle)
    app.router.add_post(PREFIX + "/{operation:configure|checkins}", handle)
    app.router.add_get(PREFIX + "/{goal_id}", handle)
