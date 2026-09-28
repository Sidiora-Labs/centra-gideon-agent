"""HTTP API routes for the extension system.

Provides endpoints for:
- Listing extensions with status and type filtering
- Reading/writing per-extension config
- Fetching settings schemas for dynamic UI rendering
- Enabling/disabling extensions at runtime
"""

import logging
from typing import Any

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.extensions.apps.secret_fields import (
    mask_secrets,
    preserve_unchanged_secrets,
)
from gideon.extensions.providers.registry import get_provider_registry
from gideon.extensions.providers.settings import ProviderSettings

logger = logging.getLogger(__name__)


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/providers", handle_list_extensions)
    app.router.add_post("/api/providers/{name}/availability", handle_refresh_availability)
    app.on_startup.append(_warm_availability)
    app.on_cleanup.append(_shutdown_availability)
    app.router.add_get("/api/providers/{name}", handle_get_extension)
    app.router.add_get("/api/providers/{name}/schema", handle_get_schema)
    app.router.add_get("/api/providers/{name}/config", handle_get_config)
    app.router.add_patch("/api/providers/{name}/config", handle_patch_config)
    app.router.add_post("/api/providers/{name}/enable", handle_enable)
    app.router.add_post("/api/providers/{name}/disable", handle_disable)


async def handle_list_extensions(request: web.Request) -> web.Response:
    registry = get_provider_registry()
    type_filter = request.query.get("type")

    extensions = registry.list_extensions()
    request_app = request.get("app", "")
    if request_app:
        extensions = [extension for extension in extensions if extension.name == request_app]
    if type_filter:
        extensions = [e for e in extensions if e.provider_config.type == type_filter]

    from gideon.extensions.providers.availability import get_availability_board

    board = get_availability_board()
    result: list[dict[str, Any]] = []
    for ext in extensions:
        availability = board.read(ext.name, ext.provider_config.implementation)
        available = availability.state not in {"unavailable", "unknown"}
        unavailable_reason = availability.reason
        result.append(
            {
                "name": ext.name,
                "displayName": ext.manifest.displayName,
                "description": ext.manifest.description,
                "version": ext.manifest.version,
                "author": ext.manifest.author,
                "enabled": ext.enabled,
                "error": ext.error,
                "available": available,
                "unavailableReason": unavailable_reason,
                "availability": availability.to_wire(),
                "managed": not bool(ext.manifest.native),
                "provider": {
                    "type": ext.provider_config.type,
                    "entity": ext.provider_config.entity,
                    "capabilities": ext.provider_config.capabilities,
                    "multiInstance": ext.provider_config.multiInstance,
                    "hasConfigSchema": bool(
                        (ext.provider_config.settingsSchema or {}).get("properties")
                    ),
                },
                "tags": ext.manifest.tags,
            }
        )

    if not type_filter or type_filter == "tool":
        result.append(
            {
                "name": "gideon-filesystem",
                "displayName": "Filesystem & Shell Tools",
                "description": "The always-on platform tools — read/write/edit/list/glob/grep/repo_map, "  # noqa: E501
                "bash, and full-result retrieval. Required by the agent; can't be disabled.",
                "version": "1.0.0",
                "author": "Gideon",
                "enabled": True,
                "error": "",
                "available": True,
                "unavailableReason": "",
                "managed": False,
                "platform": True,
                "provider": {
                    "type": "tool",
                    "entity": "tool",
                    "capabilities": ["filesystem", "shell"],
                    "multiInstance": False,
                    "hasConfigSchema": False,
                },
                "tags": ["tool", "bundled", "platform"],
            }
        )

    return web.json_response({"providers": result})


async def _warm_availability(app: web.Application) -> None:
    from gideon.extensions.providers.availability import get_availability_board

    names = [ext.name for ext in get_provider_registry().list_extensions()]
    get_availability_board().warm(names)


async def _shutdown_availability(app: web.Application) -> None:
    from gideon.extensions.providers.availability import get_availability_board

    await get_availability_board().shutdown()


async def handle_refresh_availability(request: web.Request) -> web.Response:
    from gideon.extensions.providers.availability import get_availability_board
    from gideon.http_errors import json_error

    name = request.match_info["name"]
    request_app = request.get("app", "")
    if request_app and request_app != name:
        return json_error("forbidden", message="provider is outside this app's authority", status=403)
    ext = get_provider_registry().get(name)
    if ext is None:
        return json_error("not_found", message="provider not found", status=404)
    board = get_availability_board()
    board.recheck(name)
    availability = board.read(name, ext.provider_config.implementation)
    return web.json_response({"availability": availability.to_wire()}, status=202)


async def handle_get_extension(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    return web.json_response(
        {
            "name": ext.name,
            "displayName": ext.manifest.displayName,
            "description": ext.manifest.description,
            "version": ext.manifest.version,
            "author": ext.manifest.author,
            "enabled": ext.enabled,
            "error": ext.error,
            "provider": ext.provider_config.to_dict(),
            "manifest": ext.manifest.to_dict(),
        }
    )


async def handle_get_schema(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    return web.json_response(
        {
            "name": name,
            "schema": ext.provider_config.settingsSchema,
        }
    )


async def handle_get_config(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    config, secret_set = mask_secrets(
        ProviderSettings.load(name), ext.provider_config.settingsSchema
    )
    return web.json_response(
        {"name": name, "config": config, "_secret_set": secret_set}
    )


async def handle_patch_config(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    schema = ext.provider_config.settingsSchema
    body = preserve_unchanged_secrets(body, ProviderSettings.load(name), schema)
    errors = ProviderSettings.validate(body, schema)
    if errors:
        return web.json_response(
            {"error": "Validation failed", "details": errors}, status=422
        )

    updated = ProviderSettings.update(name, body)

    if ext.enabled:
        registry.disable(name)
        registry.enable(name)
    try:
        from gideon.interfaces.dashboard.handlers.providers import (
            _refresh_media_registries,
        )

        _refresh_media_registries()
    except Exception:  # noqa: BLE001 — refresh is best-effort, never block a save
        pass
    masked, secret_set = mask_secrets(updated, schema)
    return web.json_response(
        {"name": name, "config": masked, "_secret_set": secret_set}
    )


async def handle_enable(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    app_name = _installed_app_for_provider(ext)
    if app_name is not None:
        from gideon.extensions.apps.app_manager import enable as enable_app

        success = enable_app(app_name, caller=request.get("user", "provider-route"))
    else:
        success = registry.enable(name)
    if not success:
        return web.json_response(
            {"error": f"Failed to enable: {ext.error}"}, status=500
        )

    return web.json_response({"name": name, "enabled": True})


async def handle_disable(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    app_name = _installed_app_for_provider(ext)
    if app_name is not None:
        from gideon.extensions.apps.app_manager import disable as disable_app

        if not disable_app(app_name, caller=request.get("user", "provider-route")):
            return web.json_response({"error": f"Failed to disable {name!r}"}, status=400)
    else:
        registry.disable(name)
    return web.json_response({"name": name, "enabled": False})


def _installed_app_for_provider(ext):
    """Resolve a registered provider back to an installed manifest before lifecycle delegation."""
    manifest = getattr(ext, "manifest", None)
    app_name = str(getattr(manifest, "name", "") or "")
    if not app_name or ext.name != app_name or not getattr(manifest, "all_providers", None):
        return None
    try:
        from gideon.extensions.apps.app_manager import _manifest_of
        from gideon.extensions.apps.manager import _read_installed

        installed = _read_installed(app_name)
        current = _manifest_of(app_name)
        if installed is None or current is None or current.name != app_name:
            return None
        if not any(
            cfg.type == ext.provider_config.type
            and cfg.implementation == ext.provider_config.implementation
            for cfg in current.all_providers()
        ):
            return None
        return app_name
    except Exception:
        logger.debug("provider %s app lifecycle mapping failed", ext.name, exc_info=True)
        return None
