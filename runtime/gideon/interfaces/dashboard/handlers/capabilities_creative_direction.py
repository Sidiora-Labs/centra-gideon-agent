"""Owner HTTP surface for creative direction and production plans."""

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.store import CatalogError

PREFIX = "/api/capabilities/creative/direction"


def _owner(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")


def _store(request):
    factory = request.app.get("creative_direction_factory")
    return factory() if factory else DirectionStore(config_dir())


def _error(error):
    return web.json_response({"error": str(error)}, status=error.status)


async def collection(request):
    _owner(request)
    try:
        return web.json_response(
            (
                _store(request).create(await read_json_body(request))
                if request.method == "POST"
                else {"items": _store(request).list()}
            ),
            status=201 if request.method == "POST" else 200,
        )
    except CatalogError as error:
        return _error(error)


async def item(request):
    _owner(request)
    try:
        return web.json_response(_store(request).get(request.match_info["id"]))
    except CatalogError as error:
        return _error(error)


async def mutation(request):
    _owner(request)
    try:
        store = _store(request)
        payload = await read_json_body(request)
        action = request.match_info["action"]
        result = (
            store.control(request.match_info["id"], payload)
            if action == "control"
            else (
                store.replace_plan(request.match_info["id"], payload)
                if action == "plan"
                else store.advance(request.match_info["id"], payload)
            )
        )
        return web.json_response(result)
    except CatalogError as error:
        return _error(error)


def register(app):
    app.router.add_get(PREFIX, collection, name="creative-direction-list")
    app.router.add_post(PREFIX, collection, name="creative-direction-create")
    app.router.add_get(PREFIX + "/{id}", item, name="creative-direction-get")
    app.router.add_post(
        PREFIX + "/{id}/{action:control|plan|advance}",
        mutation,
        name="creative-direction-mutate",
    )
