"""Human-authorized lifecycle control through the existing autonomous engine."""

from pathlib import Path

from aiohttp import web

from gideon.automation.triggers.nudge import get_instance
from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.lifecycle import LifecycleStore
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_lifecycle", LifecycleStore)
PREFIX = "/api/capabilities/identity/lifecycle"


async def handle(request):
    try:
        store = request.app[KEY]
        if request.method == "GET":
            result = store.status()
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
            if {"state", "service"} & body.keys():
                raise ValueError("Runtime bindings are not request fields")
            operation = request.match_info["operation"]
            result = (
                store.request_thinking(**body)
                if operation == "requests"
                else await getattr(store, operation)(
                    **body, state=request.app.get("state"), service=get_instance()
                )
            )
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "Lifecycle record not found"}, status=404)
    except (ValueError, TypeError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, home: Path | None = None):
    app[KEY] = LifecycleStore(home or config_dir())
    app.router.add_get(PREFIX, handle)
    app.router.add_post(
        PREFIX + "/{operation:configure|action|requests|dispatch}", handle
    )
