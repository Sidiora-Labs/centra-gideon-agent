"""Versioned read recipes over actual native identity tool operations."""
from pathlib import Path
from aiohttp import web
from gideon.core.config import config_dir
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.identity.recipes import RecipeStore, READ_TOOLS
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_recipes", RecipeStore)
PROVIDER = web.AppKey("identity_recipe_provider", IdentityToolProvider)
PREFIX = "/api/capabilities/identity/recipes"


async def handle(request):
    store = request.app[KEY]
    operation = request.match_info.get("operation", "recipes")
    identifier = request.match_info.get("id")
    try:
        if request.method == "GET":
            if operation == "catalog":
                result = {"tools": sorted(READ_TOOLS), "max_steps": 5}
            elif operation == "history":
                result = store.history(identifier)
            elif operation == "runs":
                result = store.get_run(identifier) if identifier else store.list_runs()
            else:
                result = store.get(identifier) if identifier else store.list()
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if operation == "advance":
                if set(body) != {"run_id", "expected_index"}:
                    raise ValueError("Expected run_id and expected_index")
                token = set_current_session_key("dashboard:identity-recipes")
                try:
                    result = await store.advance(**body, provider=request.app[PROVIDER])
                finally:
                    reset_current_session_key(token)
            else:
                result = getattr(store, "save" if operation == "recipes" else operation)(**body)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "Recipe record not found"}, status=404)
    except (ValueError, TypeError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, home: Path | None = None):
    home = home or config_dir()
    app[KEY] = RecipeStore(home / "capabilities/identity/recipes.sqlite3")
    app[PROVIDER] = IdentityToolProvider(home)
    app.router.add_get(PREFIX, handle)
    app.router.add_post(PREFIX, handle)
    app.router.add_get(PREFIX + "/{operation:catalog|runs}", handle)
    app.router.add_get(PREFIX + "/{operation:runs}/{id}", handle)
    app.router.add_post(PREFIX + "/{operation:restore|begin|advance|cancel}", handle)
    app.router.add_get(PREFIX + "/{id}/{operation:history}", handle)
    app.router.add_get(PREFIX + "/{id}", handle)
