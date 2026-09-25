"""HTTP surface for durable bounded creative production runs."""

from aiohttp import web

from gideon.workspace.capabilities.creative.production import SeriesProductionStore
from gideon.workspace.capabilities.creative.store import CatalogError

PRODUCTION = web.AppKey("creative_production", SeriesProductionStore)


async def production(request):
    try:
        if request.query:
            raise CatalogError("Unexpected query parameter")
        store = request.app[PRODUCTION]
        series_id = request.match_info["series_id"]
        run_id = request.match_info.get("run_id")
        action = request.match_info.get("action")
        if request.method == "GET":
            result = store.get(series_id, run_id) if run_id else store.list(series_id)
        elif not run_id:
            result = store.start(series_id, await request.json())
        elif action == "advance":
            result = await store.advance(series_id, run_id)
        elif action == "submit":
            result = store.submit(series_id, run_id, await request.json())
        elif action == "approve":
            result = store.approve(series_id, run_id, await request.json())
        elif action == "rollback":
            if (await request.json()) != {}:
                raise CatalogError("Rollback accepts no fields")
            result = store.rollback(series_id, run_id)
        else:
            if (await request.json()) != {}:
                raise CatalogError("Production control accepts no fields")
            result = store.control(series_id, run_id, action)
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return web.json_response(
            {"error": str(exc), "code": "creative_production_invalid"},
            status=getattr(exc, "status", 400),
        )


def register(app, home=None):
    app[PRODUCTION] = SeriesProductionStore(home)
    root = "/api/capabilities/creative/series/{series_id}/production"
    app.router.add_get(root, production)
    app.router.add_post(root, production)
    app.router.add_get(root + "/{run_id}", production)
    app.router.add_post(
        root + "/{run_id}/{action:advance|submit|approve|rollback|pause|resume|cancel}",
        production,
    )
