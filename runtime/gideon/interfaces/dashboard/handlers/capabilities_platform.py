"""Authenticated projection of the running API surface."""

from aiohttp import web

from gideon.workspace.capabilities.platform.catalog import CATALOG_PATH, bind_application, build_catalog


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
    bind_application(app)
    app.router.add_get(CATALOG_PATH, api_catalog, name="capabilities-platform-catalog")
    from gideon.interfaces.dashboard.handlers.capabilities_integration_apps import register as register_integration_apps
    from gideon.interfaces.dashboard.handlers.capabilities_peers import register as register_peers
    from gideon.interfaces.dashboard.handlers.capabilities_platform_migration import register as register_migration
    from gideon.interfaces.dashboard.handlers.capabilities_platform_quotas import register as register_quotas
    from gideon.interfaces.dashboard.handlers.capabilities_platform_remote_sessions import register as register_remote_sessions
    from gideon.interfaces.dashboard.handlers.capabilities_remote_media import register as register_remote_media
    from gideon.interfaces.dashboard.handlers.capabilities_replication import register as register_replication
    from gideon.workspace.capabilities.platform.media_shares_http import register_media_shares
    from gideon.workspace.capabilities.platform.remote_media import create_remote_media
    from gideon.workspace.capabilities.platform.domain_alerts_http import register as register_domains

    register_quotas(app)
    register_remote_sessions(app)
    register_integration_apps(app)
    register_peers(app)
    register_replication(app)
    register_media_shares(app)
    register_remote_media(app, create_remote_media())
    register_migration(app)
    register_domains(app)
