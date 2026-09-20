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
    if type_filter:
        extensions = [e for e in extensions if e.provider_config.type == type_filter]

    from gideon.extensions.providers.loader import load_availability

    result: list[dict[str, Any]] = []
    for ext in extensions:
        available, unavailable_reason = True, ""
        probe = load_availability(ext)
        if probe is not None:
            try:
                available, unavailable_reason = probe()
            except Exception:
                logger.debug("availability() raised for %s", ext.name, exc_info=True)
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

    registry.disable(name)
    return web.json_response({"name": name, "enabled": False})
