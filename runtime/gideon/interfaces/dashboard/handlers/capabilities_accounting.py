from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.operations.usage_ledger import import_cli_usage
from gideon.workspace.capabilities.platform.accounting import view


async def endpoint(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        return web.json_response(
            view(days=int(request.query.get("days", 30))),
            headers={"Cache-Control": "no-store"},
        )
    except (ValueError, OSError):
        return web.json_response(
            {"error": "Usage accounting unavailable or invalid window"}, status=400
        )


async def import_endpoint(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    try:
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {
            "format",
            "content",
            "source_name",
        }:
            raise ValueError("Format, content and source filename required")
        return web.json_response(
            import_cli_usage(body["format"], body["content"], body["source_name"]),
            status=201,
            headers={"Cache-Control": "no-store"},
        )
    except (ValueError, OSError, UnicodeError):
        return web.json_response(
            {"error": "CLI usage import unavailable or invalid"}, status=400
        )


def register(app):
    app.router.add_get("/api/capabilities/platform/accounting", endpoint)
    app.router.add_post("/api/capabilities/platform/accounting/import", import_endpoint)
