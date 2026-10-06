"""Channel sender-trust API — the owner's read/revoke surface over the allowlist (EA-7).

``channel_trust`` has shipped the store, the pairing codes and the CLI (``gideon pair
<provider>``) for some time, and an Allow/Deny action on the unknown-sender notification.
What it never had was a way to answer *"who can talk to my agent right now?"* — the
allowlist was writable from two places and readable from none. An access-control list you
cannot enumerate is one you cannot audit, and the notification that granted access is long
gone from the inbox by the time you want to review it.

Two routes, deliberately only two:

* ``GET /api/channels/trust`` — every provider the store knows, with its policy posture,
  its paired senders and its tracked channels. No secret is projected (see
  :func:`~gideon.integrations.channel_trust.provider_trust`).
* ``DELETE /api/channels/trust/{provider}/senders/{sender_id}`` — revoke one sender.

Revoke is deliberately NOT idempotent at this layer even though
:func:`~gideon.integrations.channel_trust.deny_sender` is: a request to revoke a sender who is not
on the list answers ``404 channel_trust_sender_unknown`` so the UI learns its list is stale
instead of reporting a successful revoke of something that was never there. Granting access
stays where it already is — the pairing code and the notification's Allow — because a
grant deserves a deliberate act, not a text field on a settings page.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote

from aiohttp import web

from gideon.http_errors import json_error
from gideon.integrations import channel_trust

logger = logging.getLogger(__name__)


async def api_channel_trust(request: web.Request) -> web.Response:
    """GET /api/channels/trust — the whole sender-trust posture, per provider."""
    denied = _owner_request(request)
    if denied:
        return denied
    from gideon.integrations.channel_transports import list_transports

    providers = sorted(
        set(channel_trust.list_providers())
        | {name for name in list_transports() if _registered_inbound(name)}
    )
    providers = [
        channel_trust.provider_trust(p) for p in providers
    ]
    for row in providers:
        row["seen_channels"] = channel_trust.list_seen_channels(row["provider"])
        from gideon.integrations.channel_transports import get_transport
        transport = get_transport(row["provider"])
        try:
            capabilities = transport.capabilities() if transport else None
            row["groups"] = bool(getattr(capabilities, "groups", False))
            row["speaks_as_owner"] = bool(getattr(capabilities, "speaks_as_owner", False))
            row["pairing_hint"] = str(transport.sender_pairing_hint() or "") if transport else ""
        except Exception:
            row.update(groups=True, speaks_as_owner=False, pairing_hint="")
    return web.json_response(
        {
            "providers": providers,
            "dm_policies": list(channel_trust.DM_POLICIES),
            "group_policies": list(channel_trust.GROUP_POLICIES),
            "default_dm_policy": channel_trust.DEFAULT_DM_POLICY,
            "default_group_policy": channel_trust.DEFAULT_GROUP_POLICY,
        }
    )


async def api_channel_trust_revoke(request: web.Request) -> web.Response:
    """DELETE /api/channels/trust/{provider}/senders/{sender_id} — revoke one sender.

    ``sender_id`` is percent-decoded because a provider's sender id is opaque to core and
    may legitimately contain characters (an email address, say) that must be escaped in a
    path segment.
    """
    denied = _owner_request(request)
    if denied:
        return denied
    provider = request.match_info["provider"]
    sender_id = unquote(request.match_info["sender_id"])

    if not channel_trust.is_allowed_sender(provider, sender_id):
        return json_error("channel_trust_sender_unknown", status=404)

    channel_trust.deny_sender(provider, sender_id)
    logger.info("channel trust: revoked sender on provider=%s", provider)
    return web.json_response({"ok": True, "provider": provider, "sender_id": sender_id})


def _owner_request(request: web.Request) -> web.Response | None:
    if request.get("app"):
        return json_error("owner_required", status=403)
    return None


def _registered_inbound(provider: str) -> bool:
    from gideon.integrations.channel_transports import get_transport

    transport = get_transport(provider)
    capability = getattr(transport, "capabilities", lambda: None)() if transport else None
    return bool(transport and getattr(capability, "inbound", False))


async def api_channel_trust_policy(request: web.Request) -> web.Response:
    denied = _owner_request(request)
    if denied:
        return denied
    provider = request.match_info["provider"]
    if not _registered_inbound(provider):
        return json_error("channel_provider_unavailable", status=404)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
        dm, group = body.get("dm"), body.get("group")
        if dm is not None and not isinstance(dm, str):
            raise ValueError
        if group is not None and not isinstance(group, str):
            raise ValueError
        policies = channel_trust.set_trust_policies(
            provider, dm=dm, group=group, confirm_open=body.get("confirm_open") is True
        )
    except PermissionError as exc:
        return json_error(str(exc), status=409)
    except (ValueError, TypeError):
        return json_error("invalid_channel_trust_policy", status=400)
    return web.json_response({"ok": True, "provider": provider, "policies": policies})


async def api_channel_trust_track(request: web.Request) -> web.Response:
    denied = _owner_request(request)
    if denied:
        return denied
    provider = request.match_info["provider"]
    if not _registered_inbound(provider):
        return json_error("channel_provider_unavailable", status=404)
    try:
        body = await request.json()
        channel_id = str(body.get("channel_id") or "").strip()
        name = str(body.get("name") or "").strip()
        if not channel_id or len(channel_id) > 256 or len(name) > 128:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        return json_error("invalid_channel", status=400)
    channel_trust.track(provider, channel_id, name)
    return web.json_response({"ok": True, "provider": provider, "channel_id": channel_id})


async def api_channel_trust_untrack(request: web.Request) -> web.Response:
    denied = _owner_request(request)
    if denied:
        return denied
    provider = request.match_info["provider"]
    channel_id = unquote(request.match_info["channel_id"])
    if not _registered_inbound(provider):
        return json_error("channel_provider_unavailable", status=404)
    if not channel_trust.is_tracked_channel(provider, channel_id):
        return json_error("channel_trust_channel_unknown", status=404)
    channel_trust.untrack(provider, channel_id)
    return web.json_response({"ok": True, "provider": provider, "channel_id": channel_id})


async def api_channel_trust_pairing(request: web.Request) -> web.Response:
    denied = _owner_request(request)
    if denied:
        return denied
    provider = request.match_info["provider"]
    if not _registered_inbound(provider):
        return json_error("channel_provider_unavailable", status=404)
    code = channel_trust.create_pairing_code(provider)
    response = web.json_response({"code": code, "expires_in": channel_trust.PAIRING_CODE_TTL_SECS})
    response.headers["Cache-Control"] = "no-store"
    return response


async def api_channel_trust_pairing_cancel(request: web.Request) -> web.Response:
    denied = _owner_request(request)
    if denied:
        return denied
    provider = request.match_info["provider"]
    if not _registered_inbound(provider):
        return json_error("channel_provider_unavailable", status=404)
    if not channel_trust.cancel_pairing_code(provider):
        return json_error("channel_pairing_not_active", status=404)
    return web.json_response({"ok": True, "provider": provider})


async def api_telegram_pairing(request: web.Request) -> web.Response:
    if request.get("app"):
        return json_error("owner_required", status=403)
    code = channel_trust.create_pairing_code("telegram")
    response = web.json_response(
        {"code": code, "expires_in": channel_trust.PAIRING_CODE_TTL_SECS}
    )
    response.headers["Cache-Control"] = "no-store"
    return response
