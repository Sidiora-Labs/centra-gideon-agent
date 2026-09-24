"""Authenticated projection of the running API surface."""

from aiohttp import web

from gideon.workspace.capabilities.platform.catalog import CATALOG_PATH, build_catalog


async def api_catalog(request: web.Request) -> web.Response:
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        offset = int(request.query.get("offset", "0"))
        limit = int(request.query.get("limit", "100"))
        catalog = build_catalog(request.app, offset=offset, limit=limit)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(catalog, headers={"Cache-Control": "no-store"})


def register(app: web.Application) -> None:
    app.router.add_get(CATALOG_PATH, api_catalog, name="capabilities-platform-catalog")
