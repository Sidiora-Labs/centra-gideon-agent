"""Owner-only controls for binding a channel's direct-message identity."""

from __future__ import annotations

from aiohttp import web

from gideon.http_errors import json_error
from gideon.core.config.credentials import owner_id_for
from gideon.integrations import channel_trust
from gideon.integrations.channel_transports import get_transport


def _owner_only(request: web.Request) -> web.Response | None:
    if request.get("app") or not request.get("user"):
        return json_error("owner_required", status=403)
    return None


def _supports_owner_pairing(provider: str) -> bool:
    transport = get_transport(provider)
    if transport is None:
        return False
    capabilities = transport.capabilities()
    return bool(
        getattr(capabilities, "inbound", False)
        and getattr(capabilities, "owner_pairing", False)
    )


async def api_channel_owner(request: web.Request) -> web.Response:
    """Read redacted owner-binding state for one registered provider."""
    denied = _owner_only(request)
    if denied is not None:
        return denied
    provider = request.match_info["provider"]
    supported = _supports_owner_pairing(provider)
    status = channel_trust.owner_pairing_status(provider) if supported else {
        "active": False, "created_at": "", "expires_at": "", "attempts_left": 0, "ended": ""
    }
    return web.json_response(
        {
            "provider": provider,
            "supported": supported,
            "owner_configured": bool(owner_id_for(provider)) if supported else False,
            **(channel_trust.owner_ref(provider) if supported else {"owner_id": "", "owner_name": "", "owner_source": ""}),
            "pairing": status,
        }
    )


async def api_channel_owner_pair(request: web.Request) -> web.Response:
    """Issue a short-lived code. The code is returned once and never stored in cleartext."""
    denied = _owner_only(request)
    if denied is not None:
        return denied
    provider = request.match_info["provider"]
    if not _supports_owner_pairing(provider):
        return json_error("channel_owner_pairing_unavailable", status=409)
    from gideon.interfaces.dashboard.owner_presence import ACTION_CHANNEL_OWNER, require_owner_presence
    refused = require_owner_presence(request, ACTION_CHANNEL_OWNER)
    if refused is not None:
        return refused
    code = channel_trust.create_owner_pairing_code(provider)
    response = web.json_response(
        {"ok": True, "provider": provider, "code": code,
         "expires_in": channel_trust.PAIRING_CODE_TTL_SECS}
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def api_channel_owner_pair_cancel(request: web.Request) -> web.Response:
    denied = _owner_only(request)
    if denied is not None:
        return denied
    provider = request.match_info["provider"]
    if not _supports_owner_pairing(provider):
        return json_error("channel_owner_pairing_unavailable", status=409)
    if not channel_trust.cancel_owner_pairing(provider):
        return json_error("channel_owner_pairing_inactive", status=404)
    return web.json_response({"ok": True, "provider": provider})
