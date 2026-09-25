"""Authenticated HTTP projection for personal-domain readiness and scans."""

from aiohttp import web

from gideon.integrations.inbox import live_store

from .domain_alerts import inventory, scan


async def handle(request: web.Request) -> web.Response:
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        if set(request.query) - {"token"}:
            raise ValueError("Unknown domain parameter")
        if request.method == "POST":
            state = request.app.get("state")
            if state is None:
                raise ValueError("Live attention state unavailable")
            scan(state, live_store(state))
        return web.json_response(inventory(), headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError, OSError):
        return web.json_response({"error": "domain_sources_unavailable"}, status=422)


def register(app: web.Application) -> None:
    path = "/api/capabilities/platform/domain-readiness"
    app.router.add_get(
        path, handle, allow_head=False, name="capabilities-platform-domains"
    )
    app.router.add_post(path, handle)
