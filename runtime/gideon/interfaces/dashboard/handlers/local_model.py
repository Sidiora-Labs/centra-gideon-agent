"""Owner onboarding discovery and explicit bind-or-add local provider setup."""

import asyncio

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.security.approval_answer import OWNER, of_request


def _owner(request):
    if of_request(request).kind != OWNER:
        raise web.HTTPForbidden(
            text="Only the owner can discover or set up a model provider."
        )


async def api_local_model_detect(request):
    _owner(request)
    from gideon.operations.local_model_detect import detect_localhost

    found = await asyncio.to_thread(detect_localhost)
    return web.json_response(
        {"detected": True, **found.to_dict()} if found else {"detected": False}
    )


async def api_local_model_scan(request):
    _owner(request)
    from gideon.operations.local_model_detect import scan_local_network

    try:
        found = await asyncio.to_thread(scan_local_network)
    except Exception:
        return web.json_response(
            {
                "error": {
                    "code": "local_model_scan_failed",
                    "message": "The local network scan could not finish. Try again, or add an endpoint in Settings → Providers.",
                }
            },
            status=503,
        )
    return web.json_response({"endpoints": [row.to_dict() for row in found]})


async def api_local_model_bind(request):
    _owner(request)
    from gideon.operations.local_model_detect import endpoint_is_local
    from gideon.operations.seed_local_model import add_local_model, bind_local_model

    try:
        body = await read_json_body(request)
    except Exception:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("bind_chat"), bool):
        return web.json_response(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "Say whether this becomes the chat model: bind_chat must be true or false.",
                }
            },
            status=400,
        )
    endpoint = body.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint_is_local(endpoint):
        return web.json_response(
            {
                "error": {
                    "code": "invalid_endpoint",
                    "message": "Use a loopback or RFC1918 private IP endpoint, without credentials or redirects.",
                }
            },
            status=400,
        )
    from gideon.extensions.providers.use_cases import active_model_refs

    embedding_before = active_model_refs("embedding")
    result = await asyncio.to_thread(
        bind_local_model if body["bind_chat"] else add_local_model, endpoint=endpoint
    )
    if not result.ok:
        return web.json_response(
            {"error": {"code": result.status, "message": result.detail}}, status=409
        )
    from gideon.integrations.llm.registry import sync_entries_from_config

    sync_entries_from_config()
    state = request.app.get("state")
    if (
        body["bind_chat"]
        and embedding_before != active_model_refs("embedding")
        and state is not None
    ):
        from gideon.interfaces.dashboard.handlers.embedding_reindex import (
            start_reindex_for_current_binding,
        )

        start_reindex_for_current_binding(state)
    if state is not None:
        state.broadcast_ws("refresh", {"sections": ["models", "providers"]})
    return web.json_response(
        {
            "ok": True,
            "status": result.status,
            "provider": result.provider_name,
            "model": result.model,
            "endpoint": result.endpoint,
        }
    )


def register_local_model_routes(app):
    app.router.add_get("/api/onboarding/local-model", api_local_model_detect)
    app.router.add_post("/api/onboarding/local-model/scan", api_local_model_scan)
    app.router.add_post("/api/onboarding/local-model/bind", api_local_model_bind)
