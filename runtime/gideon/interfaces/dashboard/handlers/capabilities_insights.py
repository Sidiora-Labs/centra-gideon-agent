from aiohttp import web

from gideon.workspace.capabilities.platform import insights
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


async def endpoint(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        if request.method == "POST":
            value = insights.save(await request.json())
        elif request.query.get("slug"):
            value = insights.read_narrative(
                request.query["slug"],
                int(request.query["version"]) if "version" in request.query else None,
            )
        else:
            value = insights.view()
        return web.json_response(value, headers={"Cache-Control": "no-store"})
    except MeasurementError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    except (ValueError, TypeError, OSError):
        return web.json_response(
            {"error": "Invalid or unavailable scorecard"}, status=400
        )


def register(app):
    app.router.add_get("/api/capabilities/platform/insights", endpoint)
    app.router.add_post("/api/capabilities/platform/insights", endpoint)
