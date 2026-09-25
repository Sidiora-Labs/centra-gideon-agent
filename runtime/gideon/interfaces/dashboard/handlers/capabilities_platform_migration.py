from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.capabilities.knowledge.typed import BoundHierarchy
from gideon.workspace.capabilities.platform.migration import MigrationError, commit, preview, receipts


async def endpoint(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    store = PeopleStore()
    knowledge = request.app["state"].knowledge_store
    projects = BoundHierarchy(config_dir())
    try:
        if request.method == "GET":
            return web.json_response({"receipts": receipts(store, knowledge, projects), "supported_domains": ["people", "projects", "ideas", "journals", "memories", "links"]})
        body = await request.json()
        action = body.pop("action", None)
        if action == "preview":
            return web.json_response({"preview": preview(body)})
        if action == "commit":
            receipt, created = commit(store, body, knowledge, projects)
            return web.json_response({"receipt": receipt, "created": created}, status=201 if created else 200)
        raise MigrationError("Unknown migration action")
    except MigrationError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    except (ValueError, TypeError, KeyError, UnicodeError):
        return web.json_response({"error": "Invalid migration request"}, status=400)


def register(app):
    app.router.add_get("/api/capabilities/platform/migration", endpoint)
    app.router.add_post("/api/capabilities/platform/migration", endpoint)
