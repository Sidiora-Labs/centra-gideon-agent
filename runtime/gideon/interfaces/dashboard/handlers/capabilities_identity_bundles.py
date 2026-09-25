"""Human passphrase entry and selective canonical slot transfer."""
import asyncio
from pathlib import Path
from aiohttp import web
from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.bundle_extended import ExtendedBundleService as BundleService
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_bundles", BundleService)
PREFIX = "/api/capabilities/identity/bundles"


async def handle(request):
    try:
        service = request.app[KEY]
        if request.method == "GET":
            result = await asyncio.to_thread(service.inventory)
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            operation = {"export": "export_bundle", "preview": "preview", "apply": "apply_bundle"}[request.match_info["operation"]]
            if operation == "apply_bundle":
                from gideon.interfaces.dashboard.handlers.agents import _get_config_lock
                async with _get_config_lock():
                    result = await asyncio.to_thread(getattr(service, operation), **body)
            else:
                result = await asyncio.to_thread(getattr(service, operation), **body)
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError) as error:
        return web.json_response({"error": str(error)}, status=400, headers={"Cache-Control": "no-store"})


def register(app: web.Application, *, home: Path | None = None):
    app[KEY] = BundleService(home or config_dir())
    app.router.add_get(PREFIX, handle)
    app.router.add_post(PREFIX + "/{operation:export|preview|apply}", handle)
