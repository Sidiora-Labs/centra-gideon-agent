"""The browse user-browser CONNECTOR endpoint — the non-test writer of the BA-7 seam.

BA-7 shipped ``browse.target.register_connector(device_id, cdp_url)`` as the writer of the
process-global "is the operator's own browser attached right now" registry, and left it
with no non-test caller ON PURPOSE: nothing could honestly answer that question until the
attaching client existed. This module is that caller. It is the loopback surface a paired
device announces its CDP page-target endpoint on, so a ``user_browser`` browse task
(BA-7's second execution target) has somewhere to run and ``resolve_cdp_url`` has an
endpoint to hand the CDP transport.

**Loopback only, and no new listening surface.** The connector is the operator's own
browser on the SAME machine as the gateway, so its endpoint is reached over loopback and
these routes refuse any non-loopback caller — on the raw TCP peer, never an ``X-Real-IP``
a proxy or a caller could spoof. They mount on the gateway's EXISTING dashboard server, so
no second socket is opened, and the endpoint a device announces is validated against the
shipped ``LOOPBACK_INTERNAL`` egress rail before it is stored — so a public ``cdp_url``
cannot be registered even by a loopback caller. That is why registration is the structural
gate: the transport's own ``connect`` is not egress-guarded, so "the connector endpoint is
loopback" has to be made true here or nowhere.

**Paired via the shipped device-session machinery (COMPANION-APPS §C1/C2), not a second
one.** The announcing client is an ordinary paired device: it holds the session cookie
``pair/complete`` minted, and the ``device_id`` handed to :func:`register_connector` is the
one the pairing registry already knows — so the attached browser IS a row in
``GET /api/devices``, listed as a connected device rather than tracked by a parallel
notion. A session with no ``device`` provenance (the owner's own dashboard tab) is refused:
only a paired device may attach as the connector.

**No browser-vendor knowledge in core.** This module names a *paired device* and a *CDP
page-target endpoint*; which browser produced that endpoint, and the extension that speaks
the typed local contract (navigate / read-outline / click / type / close) to it, live
entirely in the removable app bundle (BA-8, apps repo). ``tests/test_browse_connector_route``
rails this module against every browser-vendor name so a future edit cannot leak one in.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.http_errors import json_error
from gideon.integrations.browse import (
    clear_connector,
    connector_status,
    register_connector,
)
from gideon.interfaces.dashboard.origin import is_loopback
from gideon.interfaces.dashboard.session_store import DeviceInfo, device_sessions
from gideon.security.net import LOOPBACK_INTERNAL, evaluate

logger = logging.getLogger(__name__)

_LOOPBACK_WS = LOOPBACK_INTERNAL.with_overrides(allow_schemes=("ws", "wss"))


def _sel() -> Any:
    from gideon.security.sel import sel

    return sel()


def _audit(
    operation: str,
    outcome: str,
    *,
    caller: str = "device",
    resources: str = "",
    error: str = "",
) -> None:
    """One SEL row per connector state change. Never raises — an audit failure must not eat
    the reply (the same posture as the device-pairing routes this sits beside)."""
    try:
        _sel().log_api_access(
            caller=caller,
            operation=operation,
            outcome=outcome,
            source="browse_connector",
            error=error,
            resources=resources,
        )
    except Exception:  # noqa: BLE001
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


def _require_loopback(request: web.Request, operation: str) -> web.Response | None:
    """Refuse a non-loopback caller. The connector is the operator's browser on THIS machine;
    a LAN or remote peer reaching it would be a different device entirely, so the check is on
    the raw TCP peer (``request.remote``) and not on a header a proxy or caller can set.
    """
    if not is_loopback(request.remote or ""):
        _audit(operation, "denied", caller=request.remote or "", error="non-loopback")
        return json_error("browse_connector_loopback_only", status=403)
    return None


def _paired_device(request: web.Request) -> DeviceInfo | None:
    """The paired-device row that authorized this request, or ``None``.

    The middleware has already validated the session and recorded its nonce; a connector
    caller must ADDITIONALLY be a paired device (a ``sessions.json`` row with ``device`` set),
    so its identity in the connector registry is the same ``device_id`` pairing minted and the
    attached browser is a real ``GET /api/devices`` entry. The owner's own browser session has
    no device row and is therefore not eligible.
    """
    nonce = str(request.get("session_nonce") or "")
    record = device_sessions().get(nonce)
    if record is None or record.device is None:
        return None
    return record.device


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await read_json_body(request)
    except Exception:  # noqa: BLE001 — a malformed body is an empty body, not a 500
        return {}
    return body if isinstance(body, dict) else {}


async def api_browse_connector_attach(request: web.Request) -> web.Response:
    """POST /api/browse/connector — record the operator's attached browser.

    Body ``{"cdp_url": "ws://127.0.0.1:.../devtools/page/..."}``, from a paired device over
    loopback. Writes the BA-7 connector registry via :func:`register_connector`; a later
    ``user_browser`` task reads it back through ``resolve_cdp_url``.
    """
    denied = _require_loopback(request, "browse_connector_attached")
    if denied is not None:
        return denied

    device = _paired_device(request)
    if device is None:
        _audit(
            "browse_connector_attached", "denied", error="not a paired device session"
        )
        return json_error("browse_connector_unpaired", status=403)

    body = await _body(request)
    cdp_url = str(body.get("cdp_url") or "").strip()
    if not cdp_url:
        _audit(
            "browse_connector_attached",
            "denied",
            caller=device.id,
            error="missing cdp_url",
        )
        return json_error(
            "browse_connector_endpoint_invalid",
            status=400,
            message="a connector must announce a CDP page-target endpoint",
        )

    decision = evaluate(cdp_url, _LOOPBACK_WS)
    if not decision.allow:
        _audit(
            "browse_connector_attached",
            "denied",
            caller=device.id,
            error="non-loopback endpoint",
        )
        return json_error(
            "browse_connector_endpoint_invalid",
            status=400,
            message=decision.reason or None,
        )

    session = register_connector(device_id=device.id, cdp_url=cdp_url)
    _audit(
        "browse_connector_attached",
        "ok",
        caller=device.id,
        resources=f"device={device.id}",
    )
    return web.json_response({"ok": True, "device_id": session.device_id})


async def api_browse_connector_detach(request: web.Request) -> web.Response:
    """DELETE /api/browse/connector — detach the operator's browser. Idempotent."""
    denied = _require_loopback(request, "browse_connector_detached")
    if denied is not None:
        return denied
    device = _paired_device(request)
    if device is None:
        _audit(
            "browse_connector_detached", "denied", error="not a paired device session"
        )
        return json_error("browse_connector_unpaired", status=403)
    clear_connector()
    _audit(
        "browse_connector_detached",
        "ok",
        caller=device.id,
        resources=f"device={device.id}",
    )
    return web.json_response({"ok": True})


async def api_browse_connector_status(request: web.Request) -> web.Response:
    """GET /api/browse/connector — whether a browser is attached right now.

    Loopback-only like its siblings. Returns the two sentences ``ConnectorStatus`` already
    carries, so the announcing client can show the same reason/fix the browse provider would.
    """
    denied = _require_loopback(request, "browse_connector_status")
    if denied is not None:
        return denied
    status = connector_status()
    return web.json_response(
        {
            "connected": status.connected,
            "device_id": status.device_id,
            "reason": status.reason,
            "fix": status.fix,
        }
    )


async def api_browse_grants(request: web.Request) -> web.Response:
    """GET /api/browse/grants — pending per-task grants a human can answer.

    This is an owner-dashboard surface, not a connector announcement, so ordinary dashboard
    authentication is the boundary.  In particular it does not require paired-device provenance:
    the owner tab that can see the prompt must also be able to answer it.
    """
    from gideon.integrations.browse.grant import pending_grants
    from gideon.integrations.browse.plans import reachable_scope

    effective_scope = reachable_scope()
    grants = [
        {**pending, "reachable_scope": effective_scope} for pending in pending_grants()
    ]
    return web.json_response({"grants": grants})


async def api_browse_grant_answer(request: web.Request) -> web.Response:
    """POST /api/browse/grants/{request_id} — approve or reject exactly one task."""
    from gideon.integrations.browse.grant import approve_grant, reject_grant

    request_id = str(request.match_info.get("request_id") or "").strip()
    body = await _body(request)
    decision = str(body.get("decision") or "").strip().lower()
    if decision not in {"approve", "reject"}:
        return json_error(
            "browse_grant_decision_invalid",
            status=400,
            message="decision must be 'approve' or 'reject'",
        )

    resolve = approve_grant if decision == "approve" else reject_grant
    if not request_id or not resolve(request_id):
        return json_error(
            "browse_grant_not_pending",
            status=409,
            message="this browser grant is no longer waiting for an answer",
        )
    return web.json_response(
        {"ok": True, "request_id": request_id, "decision": decision}
    )


def register_browse_connector_routes(app: web.Application) -> None:
    """Wire the connector routes onto the EXISTING dashboard server — no new listener.

    Registered beside the device routes because the connector IS a paired device: it
    authenticates with the session ``pair/complete`` minted and is listed by the same
    registry.
    """
    app.router.add_post("/api/browse/connector", api_browse_connector_attach)
    app.router.add_delete("/api/browse/connector", api_browse_connector_detach)
    app.router.add_get("/api/browse/connector", api_browse_connector_status)
    app.router.add_get("/api/browse/grants", api_browse_grants)
    app.router.add_post("/api/browse/grants/{request_id}", api_browse_grant_answer)
