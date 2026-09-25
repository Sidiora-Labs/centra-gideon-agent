"""Auth-bound authored stories and deterministic player operations."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.experience import Conflict, ExperienceStore, NotFound

STORE = web.AppKey("experience_store", ExperienceStore)
PREFIX = "/api/capabilities/experience"


async def handle(request):
    store = request.app[STORE]
    resource = request.path.split("/")[4]
    key = request.match_info.get("id")
    try:
        if request.method == "GET":
            if resource == "stories":
                result = {"story": store.story(key)} if key else {"stories": store.stories()}
            else:
                result = store.session(key) if key else {"sessions": store.sessions(request.query.get("story_id"))}
        elif request.method == "DELETE":
            raw = request.query.get("revision", "")
            if not raw.isdecimal():
                raise ValueError("revision query parameter is required")
            store.delete(key, int(raw))
            result = {"deleted": True}
        else:
            body = await read_json_body(request)
            if resource == "stories":
                result = {"story": store.save(body, key)}
            elif key:
                result = store.choose(key, body)
            else:
                result = store.start(body)
        return web.json_response(result, status=201 if request.method == "POST" and key is None else 200)
    except NotFound as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except Conflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


def register(app):
    if STORE not in app:
        app[STORE] = ExperienceStore()
    from gideon.workspace.capabilities.experience.narration_http import register_narration
    from gideon.workspace.capabilities.experience.navigation_http import register_navigation
    register_narration(app, app[STORE])
    register_navigation(app, app[STORE])
    for resource in ("stories", "sessions"):
        path = PREFIX + "/" + resource
        app.router.add_get(path, handle)
        app.router.add_post(path, handle)
        app.router.add_get(path + "/{id}", handle)
        if resource == "stories":
            app.router.add_put(path + "/{id}", handle)
            app.router.add_delete(path + "/{id}", handle)
        else:
            app.router.add_post(path + "/{id}/choices", handle)
