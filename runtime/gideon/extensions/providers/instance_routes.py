"""HTTP API routes for multi-instance extension management and use-case settings.

Endpoints:
  GET    /api/providers/{name}/instances          — list instances
  POST   /api/providers/{name}/instances          — create instance
  GET    /api/providers/{name}/instances/{id}     — get instance
  PUT    /api/providers/{name}/instances/{id}     — update instance
  DELETE /api/providers/{name}/instances/{id}     — delete instance
  POST   /api/providers/{name}/instances/{id}/test — test instance connectivity

  GET    /api/models/use-cases/{use_case}/settings — get per-use-case settings
  PUT    /api/models/use-cases/{use_case}/settings — update per-use-case settings

Which model serves a use case is the active selection in ``active_models.json``
(``/api/models/active`` — see ``dashboard/handlers/model_registry.py``); these
``/settings`` routes carry only provider-agnostic behavior (e.g. auto-speak).

Every failure answers with the one wire error envelope
(``{"error": {"code", "message"}}`` via :func:`gideon.http_errors.json_error`);
only success keeps its ``{"ok": true, ...}`` shape. A "test connection" failure in
particular is a real 4xx/5xx so the frontend's shared error funnel (which fires on
``!response.ok``) surfaces guidance instead of a raw Python exception.

**A sensitive instance setting is write-only on every one of these routes.** An instance's
``to_dict()`` is the store's DISK serializer, and each of the four config-bearing routes
handed it straight to a response body — so an ``x-meta.sensitive`` field came back in
cleartext on create, on update, on read, and on every list. Measured against a live
gateway with ``openai-models`` (``multiInstance: true``, ``api_key`` sensitive): all four
returned the stored key verbatim, and the list route returned EVERY configured instance's
key in one body. Eleven bundled model apps have exactly that shape.

**Who reaches these routes — measured, not assumed.** A cold Settings → Providers load calls
``GET .../instances`` once per enabled multi-instance provider; measured, that is
``mcp-tools`` and ``openai-tools``. It does NOT call them for a **model** provider, which
renders through ``ModelBackends.tsx`` off ``/api/model-providers`` and never asks for an
instance config. So the eleven model apps leaked to any API client holding a token (CLI,
script, another app) rather than through the shipped dashboard page. That changes who was
exposed, not whether the routes leak — which is why the mask belongs at the route rather
than in a view. The masking policy is
:mod:`gideon.extensions.apps.secret_fields` — the same one the two single-config routes use, not
a second copy — reached here through :func:`~gideon.extensions.apps.secret_fields.mask_instance`.
"""

import logging

import aiohttp
from aiohttp import web

from gideon.extensions.apps.secret_fields import (
    mask_instance,
    preserve_unchanged_secrets,
)
from gideon.extensions.providers import mcp_instances as _mcp
from gideon.extensions.providers.failure_copy import connectivity_guidance
from gideon.http_errors import json_error

logger = logging.getLogger(__name__)


def _rebuild_agent_config_safe() -> None:
    """Best-effort agent-config rebuild after an instance mutation, so the change
    reaches Gideon sessions without a restart. Never raises."""
    try:
        from gideon.engine.agent import rebuild_agent_config

        rebuild_agent_config()
    except Exception:
        logger.warning(
            "rebuild_agent_config failed after instance change", exc_info=True
        )


def _refresh_tool_provider_safe(name: str) -> None:
    """Re-register a generic multiInstance TOOL provider after its instance set
    changed, so newly-added/edited/removed instances become live tool providers
    without a restart. mcp-tools has its own path (live mcp.json registry); this
    covers the OTHER tool apps (e.g. openai-tools) whose ToolTypeHandler.create
    rebuilds one provider per enabled instance. disable→enable re-runs create()
    against the current on-disk instance set (disk = source of truth), then the
    agent config is rebuilt. Best-effort; never raises."""
    try:
        from gideon.extensions.providers.registry import get_provider_registry

        registry = get_provider_registry()
        ext = registry.get(name)
        if (
            not ext
            or ext.provider_config.type != "tool"
            or not ext.provider_config.multiInstance
        ):
            return
        if ext.enabled:
            registry.disable(name)
        registry.enable(name)
        _rebuild_agent_config_safe()
    except Exception:
        logger.warning(
            "tool-provider refresh failed after instance change for %s",
            name,
            exc_info=True,
        )


def register_instance_routes(app: web.Application) -> None:
    """Register instance CRUD and per-use-case settings routes."""
    app.router.add_get("/api/providers/{name}/instances", handle_list_instances)
    app.router.add_post("/api/providers/{name}/instances", handle_create_instance)
    app.router.add_get("/api/providers/{name}/instances/{id}", handle_get_instance)
    app.router.add_put("/api/providers/{name}/instances/{id}", handle_update_instance)
    app.router.add_delete(
        "/api/providers/{name}/instances/{id}", handle_delete_instance
    )
    app.router.add_post(
        "/api/providers/{name}/instances/{id}/test", handle_test_instance
    )

    app.router.add_get(
        "/api/models/use-cases/{use_case}/settings", handle_get_use_case_settings
    )
    app.router.add_put(
        "/api/models/use-cases/{use_case}/settings", handle_set_use_case_settings
    )


async def handle_list_instances(request: web.Request) -> web.Response:
    """GET /api/providers/{name}/instances"""
    from gideon.extensions.providers.instances import list_instances
    from gideon.extensions.providers.registry import get_provider_registry

    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found",
            message="No provider is registered under that name.",
            status=404,
        )
    if not ext.provider_config.multiInstance:
        return json_error(
            "bad_request",
            message="This provider does not support multiple instances.",
            status=400,
        )

    schema = ext.provider_config.settingsSchema

    if name == _mcp.MCP_TOOLS_EXTENSION:
        return web.json_response(
            {"instances": [mask_instance(i, schema) for i in _mcp.list_instances()]}
        )

    instances = list_instances(name)
    return web.json_response(
        {
            "instances": [mask_instance(inst, schema) for inst in instances],
        }
    )


async def handle_create_instance(request: web.Request) -> web.Response:
    """POST /api/providers/{name}/instances"""
    from gideon.extensions.providers.instances import create_instance
    from gideon.extensions.providers.registry import get_provider_registry
    from gideon.extensions.providers.settings import ProviderSettings

    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found",
            message="No provider is registered under that name.",
            status=404,
        )
    if not ext.provider_config.multiInstance:
        return json_error(
            "bad_request",
            message="This provider does not support multiple instances.",
            status=400,
        )

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)

    display_name = str(body.get("display_name", "")).strip()
    config = body.get("config", {})
    if not isinstance(config, dict):
        return json_error(
            "invalid_body",
            message="The 'config' field must be a JSON object.",
            status=400,
        )

    schema = ext.provider_config.settingsSchema
    config = preserve_unchanged_secrets(config, {}, schema)

    errors = ProviderSettings.validate(config, schema)
    if errors:
        return json_error(
            "invalid_request",
            message="The instance configuration failed validation.",
            status=422,
            error_extra={"details": errors},
        )

    if name == _mcp.MCP_TOOLS_EXTENSION:
        try:
            inst = _mcp.create_instance(display_name, config)
        except ValueError as exc:
            return json_error("bad_request", message=str(exc), status=400)
        _rebuild_agent_config_safe()
        return web.json_response({"instance": mask_instance(inst, schema)}, status=201)

    inst = create_instance(name, display_name=display_name, config=config)
    _refresh_tool_provider_safe(name)
    return web.json_response({"instance": mask_instance(inst, schema)}, status=201)


async def handle_get_instance(request: web.Request) -> web.Response:
    """GET /api/providers/{name}/instances/{id}"""
    from gideon.extensions.providers.instances import get_instance
    from gideon.extensions.providers.registry import get_provider_registry

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found",
            message="No provider is registered under that name.",
            status=404,
        )
    schema = ext.provider_config.settingsSchema

    if name == _mcp.MCP_TOOLS_EXTENSION:
        inst = _mcp.get_instance(instance_id)
        if not inst:
            return json_error(
                "not_found", message="No instance exists with that id.", status=404
            )
        return web.json_response({"instance": mask_instance(inst, schema)})
    inst = get_instance(name, instance_id)
    if not inst:
        return json_error(
            "not_found", message="No instance exists with that id.", status=404
        )
    return web.json_response({"instance": mask_instance(inst, schema)})


async def handle_update_instance(request: web.Request) -> web.Response:
    """PUT /api/providers/{name}/instances/{id}"""
    from gideon.extensions.providers.instances import get_instance, update_instance
    from gideon.extensions.providers.registry import get_provider_registry
    from gideon.extensions.providers.settings import ProviderSettings

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found",
            message="No provider is registered under that name.",
            status=404,
        )

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)

    schema = ext.provider_config.settingsSchema
    is_mcp = name == _mcp.MCP_TOOLS_EXTENSION

    config = body.get("config")
    if config is not None:
        if not isinstance(config, dict):
            return json_error(
                "invalid_body",
                message="The 'config' field must be a JSON object.",
                status=400,
            )
        existing = (
            _mcp.get_instance(instance_id)
            if is_mcp
            else get_instance(name, instance_id)
        )
        config = preserve_unchanged_secrets(
            config, existing.config if existing else {}, schema
        )
        errors = ProviderSettings.validate(config, schema)
        if errors:
            return json_error(
                "invalid_request",
                message="The instance configuration failed validation.",
                status=422,
                error_extra={"details": errors},
            )

    if is_mcp:
        inst = _mcp.update_instance(
            instance_id, config=config, enabled=body.get("enabled")
        )
        if not inst:
            return json_error(
                "not_found", message="No instance exists with that id.", status=404
            )
        _rebuild_agent_config_safe()
        return web.json_response({"instance": mask_instance(inst, schema)})

    inst = update_instance(
        name,
        instance_id,
        display_name=body.get("display_name"),
        config=config,
        enabled=body.get("enabled"),
    )
    if not inst:
        return json_error(
            "not_found", message="No instance exists with that id.", status=404
        )
    _refresh_tool_provider_safe(name)
    return web.json_response({"instance": mask_instance(inst, schema)})


async def handle_delete_instance(request: web.Request) -> web.Response:
    """DELETE /api/providers/{name}/instances/{id}"""
    from gideon.extensions.providers.instances import delete_instance

    name = request.match_info["name"]
    instance_id = request.match_info["id"]
    if name == _mcp.MCP_TOOLS_EXTENSION:
        if not _mcp.delete_instance(instance_id):
            return json_error(
                "not_found", message="No instance exists with that id.", status=404
            )
        _rebuild_agent_config_safe()
        return web.json_response({"ok": True})
    deleted = delete_instance(name, instance_id)
    if not deleted:
        return json_error(
            "not_found", message="No instance exists with that id.", status=404
        )
    _refresh_tool_provider_safe(name)
    return web.json_response({"ok": True})


def _probe_failure(exc: BaseException, *, context: str) -> web.Response:
    """Turn a connectivity-probe exception into the one wire error envelope.

    The raw exception is logged for the operator; it is NEVER placed in the user
    message (a truncated ``ConnectionRefusedError(...)`` is noise, not guidance).
    Failing to reach the endpoint is an upstream problem (502 ``provider_unreachable``);
    an unusable instance config is the caller's (400 ``provider_config_invalid``); any
    other error is an unexpected, retryable test failure (502 ``provider_test_failed``).
    """
    logger.warning("provider connectivity test failed (%s)", context, exc_info=True)
    if isinstance(exc, (ValueError, KeyError)):
        return json_error(
            "provider_config_invalid",
            message="This instance's configuration is incomplete or invalid, so it could "
            "not be tested. Check its settings and try again.",
            status=400,
        )
    guidance = connectivity_guidance(exc)
    if guidance is not None:
        return json_error("provider_unreachable", message=guidance, status=502)
    return json_error(
        "provider_test_failed",
        message="The connection test failed unexpectedly. Check the instance configuration "
        "and try again.",
        status=502,
    )


async def handle_test_instance(request: web.Request) -> web.Response:
    """POST /api/providers/{name}/instances/{id}/test — test connectivity."""
    from gideon.extensions.providers.instances import get_instance
    from gideon.extensions.providers.registry import get_provider_registry

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found",
            message="No provider is registered under that name.",
            status=404,
        )

    if name == _mcp.MCP_TOOLS_EXTENSION:
        from gideon.integrations.mcp_discovery import list_servers, probe_server

        target = next((s for s in list_servers() if s.name == instance_id), None)
        if target is None:
            return json_error(
                "not_found", message="No instance exists with that id.", status=404
            )
        try:
            probed = await probe_server(target)
        except Exception as exc:
            return _probe_failure(exc, context=f"mcp-tools/{instance_id}")
        if probed.status in ("ok", "ready", "connected"):
            return web.json_response(
                {"ok": True, "message": f"Connected — {len(probed.tools)} tool(s)"}
            )
        logger.warning(
            "mcp-tools instance %s probed not-ready: status=%s error=%s",
            instance_id,
            probed.status,
            probed.error,
        )
        return json_error(
            "provider_test_failed",
            message="Reached the MCP server, but it did not report ready. Check its command "
            "and configuration.",
            status=502,
        )

    inst = get_instance(name, instance_id)
    if not inst:
        return json_error(
            "not_found", message="No instance exists with that id.", status=404
        )

    endpoint = inst.config.get("endpoint", "")
    if endpoint and ext.provider_config.type == "model":
        return await _test_model_connectivity(endpoint)

    try:
        from gideon.extensions.providers.loader import load_factory

        factory = load_factory(ext)
        provider = factory(inst.config)
        if hasattr(provider, "is_available"):
            available = await provider.is_available()
            if available:
                return web.json_response({"ok": True, "message": "Provider available"})
            return json_error(
                "provider_test_failed",
                message="The provider was created but reports it is not available. Check its "
                "configuration and that any backing service is running.",
                status=502,
            )
        return web.json_response(
            {"ok": True, "message": "Provider created successfully"}
        )
    except Exception as exc:
        return _probe_failure(exc, context=f"{name}/{instance_id}")


async def _test_model_connectivity(endpoint: str) -> web.Response:
    """Test connectivity to a model endpoint (Ollama, vLLM, etc.)."""
    endpoint = endpoint.rstrip("/")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{endpoint}/api/tags",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    models = data.get("models", [])
                    return web.json_response(
                        {
                            "ok": True,
                            "message": f"Connected — {len(models)} model(s) available",
                        }
                    )
                logger.warning("model endpoint probe returned HTTP %s", r.status)
                return json_error(
                    "provider_test_failed",
                    message=f"The endpoint is reachable but returned HTTP {r.status}. Check "
                    "the URL and that it is a compatible model server.",
                    status=502,
                )
    except Exception as exc:
        return _probe_failure(exc, context="model-endpoint")


async def handle_get_use_case_settings(request: web.Request) -> web.Response:
    """GET /api/models/use-cases/{use_case}/settings"""
    from gideon.extensions.providers.use_cases import (
        VALID_USE_CASES,
        load_use_case_settings,
    )

    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return json_error(
            "bad_request", message="That is not a recognized use case.", status=400
        )

    settings = load_use_case_settings(use_case)
    return web.json_response({"use_case": use_case, "settings": settings})


async def handle_set_use_case_settings(request: web.Request) -> web.Response:
    """PUT /api/models/use-cases/{use_case}/settings"""
    from gideon.extensions.providers.use_cases import (
        VALID_USE_CASES,
        save_use_case_settings,
    )

    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return json_error(
            "bad_request", message="That is not a recognized use case.", status=400
        )

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)

    if not isinstance(body, dict):
        return json_error(
            "invalid_body",
            message="The request body must be a JSON object.",
            status=400,
        )

    save_use_case_settings(use_case, body)
    return web.json_response({"ok": True, "use_case": use_case, "settings": body})
