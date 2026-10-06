"""Settings → External Access — the inbound seam's operator surface (EA-1, §1.5).

Read-mostly. The panel needs four things and this module is careful about which of
them it will hand back:

* **Per-surface state** — enabled, whether a valid token exists, and remote posture.
  Whether a token EXISTS is reported; the token itself never is. `token_problem`
  already returns a reason rather than a bool, so "why is this surface off?" is
  answerable without the credential ever entering a response body.
* **Per-client records** — labels and bindings, with the token *hash* elided too.
  A hash is not a credential, but publishing it over HTTP hands an offline
  guesser the exact target it needs, for no operator benefit.
* **Derived activity** — last-seen and request counts computed FROM
  `inbound_audit.jsonl`, not from a second counter kept beside it. The guardrails
  health-view pattern: a count maintained alongside a table is two things that can
  disagree, and the table is the one that is true.
* **Kill switches** — flipped through the existing `_EDITABLE_CONFIG` PATCH path,
  NOT here. `public_url`, `allow_remote` and the tokens are deliberately unreachable
  from any write path on this surface.
"""

from __future__ import annotations

import logging

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.http_errors import json_error

logger = logging.getLogger(__name__)


def _surface_rows() -> list[dict]:
    """One row per surface: its switches and whether a usable token is configured."""
    from gideon.core.config.loader import AppConfig
    from gideon.integrations.inbound import auth

    try:
        ea = AppConfig.load().external_access
    except Exception:  # noqa: BLE001
        logger.debug("external-access: config unreadable", exc_info=True)
        return []
    rows: list[dict] = []
    from gideon.integrations.inbound import tokens as token_lifetimes

    for surface in auth.surfaces():
        surface_cfg = getattr(ea, surface, None)
        problem = auth.token_problem(surface)
        configured = auth.load_surface_token(surface)
        try:
            lifetime = (
                token_lifetimes.surface_token(surface, configured)
                if configured
                else None
            )
        except token_lifetimes.RegistryUnavailable:
            lifetime = None
        rows.append(
            {
                "surface": surface,
                "enabled": bool(getattr(surface_cfg, "enabled", False)),
                "allow_remote": bool(getattr(surface_cfg, "allow_remote", False)),
                "token_configured": problem is None,
                "token_problem": problem or "",
                "token_issued_at": float(lifetime["issued_at"]) if lifetime else 0.0,
                "token_expires_at": float(lifetime["expires_at"]) if lifetime else 0.0,
                "token_state": str(lifetime["state"]) if lifetime else "unconfigured",
                "loopback_only": surface == auth.BRIDGE_SURFACE,
            }
        )
    return rows


def _unknown_provider(name: str) -> str | None:
    """``None`` when ``name`` is an acceptable ProviderEntry, else the configured names.

    Deliberately asymmetric: it refuses only on POSITIVE knowledge that the name is
    absent. An unreadable registry returns ``None`` (accept), because the alternative —
    refusing every client creation whenever provider enumeration fails — turns an
    unrelated fault into "you cannot register an integration", and buys no safety: this
    value is a provider name, never a host, and the forward it selects is pre-flighted
    against the operator's egress allow-list before any socket opens.
    """
    try:
        from gideon.integrations.llm.registry import get_default_registry

        configured = [e.name for e in get_default_registry().list_entries()]
    except Exception:  # noqa: BLE001 — cannot enumerate ⇒ cannot call it unknown
        logger.debug("external-access: provider enumeration failed", exc_info=True)
        return None
    if name in configured:
        return None
    return ", ".join(sorted(configured))


def _client_rows() -> list[dict]:
    """One row per client, with activity derived from the audit trail."""
    from gideon.integrations.inbound import audit as audit_mod
    from gideon.integrations.inbound import clients as clients_mod

    counts: dict[str, int] = {}
    refusals: dict[str, int] = {}
    try:
        for row in audit_mod.recent(limit=2000):
            cid = str(row.get("client_id") or "")
            if not cid:
                continue
            counts[cid] = counts.get(cid, 0) + 1
            if row.get("refused_reason"):
                refusals[cid] = refusals.get(cid, 0) + 1
    except Exception:  # noqa: BLE001 — an unreadable trail means "no activity yet"
        logger.debug("external-access: audit read failed", exc_info=True)
    out: list[dict] = []
    for client in clients_mod.load_clients().values():
        out.append(
            {
                "client_id": client.client_id,
                "label": client.label,
                "surfaces": list(client.surfaces),
                "agent": client.agent,
                "tools": list(client.tools),
                "scope": dict(client.scope),
                "upstream": client.upstream,
                "rate_overrides": dict(client.rate_overrides),
                "persistent_sessions": client.persistent_sessions,
                "disabled": bool(client.disabled),
                "created_at": client.created_at,
                "expires_at": float(client.expires_at or 0.0),
                "token_state": clients_mod_state(client),
                "last_seen_at": client.last_seen_at,
                "requests_seen": counts.get(client.client_id, 0),
                "refusals_seen": refusals.get(client.client_id, 0),
            }
        )
    out.sort(key=lambda r: (r["label"].lower(), r["client_id"]))
    return out


def clients_mod_state(client) -> str:
    from gideon.integrations.inbound import tokens

    state, _row = tokens.validate_client(client)
    return state


async def api_external_access(request: web.Request) -> web.Response:
    """GET /api/external-access — the whole operator view of the inbound seam."""
    from gideon.core.config.loader import AppConfig

    try:
        ea = AppConfig.load().external_access
        master = bool(ea.enabled)
        caps = {
            "rate_rps": float(ea.rate_rps),
            "rate_burst": int(ea.rate_burst),
            "rate_concurrent": int(ea.rate_concurrent),
            "auto_disable_after_breaches": int(ea.auto_disable_after_breaches),
            "capture_retention_days": int(ea.capture_retention_days),
            "capture_upstream_allowlist": list(ea.capture.upstream_allowlist),
        }
        public_url = str(ea.public_url or "")
    except Exception:  # noqa: BLE001
        logger.debug("external-access: config unreadable", exc_info=True)
        master, caps, public_url = False, {}, ""
    incident = False
    try:
        from gideon.security.guardrails.incident import incident_active

        incident = bool(incident_active())
    except Exception:  # noqa: BLE001
        incident = True
    from gideon.integrations.inbound import tokens as token_lifetimes

    try:
        integration_tokens = _integration_token_rows()
    except token_lifetimes.RegistryUnavailable:
        return json_error("integration_token_registry_unavailable", status=503)
    return web.json_response(
        {
            "enabled": master,
            "incident_active": incident,
            "public_url": public_url,
            "caps": caps,
            "surfaces": _surface_rows(),
            "clients": _client_rows(),
            "integration_tokens": integration_tokens,
        }
    )


def _integration_token_rows() -> list[dict]:
    from gideon.integrations.inbound import tokens
    from gideon.integrations.inbound import clients
    from gideon.integrations.inbound import auth

    for surface in auth.surfaces():
        current = auth.load_surface_token(surface)
        if current:
            auth.token_problem(surface)
    return tokens.surface_rows() + tokens.client_rows(clients.load_clients())


async def api_external_access_client(request: web.Request) -> web.Response:
    """POST /api/external-access/clients — create; DELETE …/{client_id} — revoke.

    The token is in the CREATE response and nowhere else, ever: only its hash is
    stored, so this is the single moment it can be shown. Revocation deletes the
    record, which is what kills the token — there is no separate revocation list to
    fall out of sync with the registry.
    """
    from gideon.integrations.inbound import auth
    from gideon.integrations.inbound import clients as clients_mod

    if request.method == "DELETE":
        client_id = str(request.match_info.get("client_id", "") or "")
        if not clients_mod.revoke_client(client_id):
            return json_error(
                "not_found", message=f"unknown client {client_id!r}", status=404
            )
        return web.json_response({"ok": True, "revoked": client_id})

    try:
        body = await read_json_body(request)
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        return json_error(
            "invalid_body", message="body must be a JSON object", status=400
        )
    label = str(body.get("label", "") or "").strip()
    if not label:
        return json_error("invalid_request", message="label is required", status=400)
    raw_surfaces = body.get("surfaces")
    if not isinstance(raw_surfaces, list) or not raw_surfaces:
        return json_error(
            "invalid_request", message="surfaces must be a non-empty list", status=400
        )
    known = set(auth.surfaces())
    requested = [str(s) for s in raw_surfaces]
    unknown = sorted(set(requested) - known)
    if unknown:
        return json_error(
            "invalid_request",
            message=(
                f"unknown surfaces: {', '.join(unknown)} (known: {', '.join(sorted(known))})"
            ),
            status=400,
        )
    persistent = body.get("persistent_sessions", False)
    if type(persistent) is not bool:
        return json_error("invalid_request", message="persistent_sessions must be a JSON boolean", status=400)
    if persistent and "openai" not in requested:
        return _no_conversation_to_keep()
    tools = body.get("tools")
    scope = body.get("scope")
    rate_overrides = body.get("rate_overrides")
    upstream = str(body.get("upstream", "") or "").strip()
    if upstream:
        unknown_upstream = _unknown_provider(upstream)
        if unknown_upstream is not None:
            return json_error(
                "invalid_request",
                message=(
                    f"unknown upstream provider {upstream!r} "
                    f"(configured: {unknown_upstream or 'none'})"
                ),
                status=400,
            )
    ttl = str(body.get("ttl", "90d") or "90d")
    if clients_mod_ttl_invalid(ttl):
        return json_error(
            "invalid_request", message="ttl must be a positive duration no longer than 90d", status=400
        )
    from gideon.interfaces.dashboard.owner_presence import ACTION_INTEGRATION_TOKEN, require_owner_presence
    refused = require_owner_presence(request, ACTION_INTEGRATION_TOKEN)
    if refused is not None:
        return refused
    try:
        client, token = clients_mod.create_client(
        label,
        surfaces=requested,
        agent=str(body.get("agent", "") or ""),
        tools=[str(t) for t in tools] if isinstance(tools, list) else None,
        scope=scope if isinstance(scope, dict) else None,
        upstream=upstream,
        rate_overrides=rate_overrides if isinstance(rate_overrides, dict) else None,
        ttl=ttl,
        persistent_sessions=persistent,
        )
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return web.json_response(
        {
            "ok": True,
            "client_id": client.client_id,
            "label": client.label,
            "surfaces": list(client.surfaces),
            "persistent_sessions": client.persistent_sessions,
            "expires_at": client.expires_at,
            "token": token,
            "token_notice": (
                "Copy this now — it is stored only as a hash and cannot be shown again."
            ),
        }
    )


def clients_mod_ttl_invalid(ttl: str) -> bool:
    from gideon.integrations.inbound import tokens

    return tokens.parse_ttl(ttl) is None


def _no_conversation_to_keep() -> web.Response:
    return json_error("invalid_request", message="Only an OpenAI-compatible client has a conversation to keep.", status=400)


async def api_external_access_client_persistent_sessions(request: web.Request) -> web.Response:
    from gideon.integrations.inbound import clients as clients_mod
    from gideon.interfaces.dashboard.owner_presence import require_owner_presence
    refused = require_owner_presence(request, "change an integration's conversation setting")
    if refused is not None:
        return refused
    try:
        body = await read_json_body(request)
    except Exception:
        body = None
    if not isinstance(body, dict) or type(body.get("persistent_sessions")) is not bool:
        return json_error("invalid_request", message="persistent_sessions must be a JSON boolean", status=400)
    persistent = body["persistent_sessions"]
    client_id = str(request.match_info.get("client_id", "") or "")
    current = clients_mod.load_clients().get(client_id)
    if current is None:
        return json_error("not_found", message="Unknown client", status=404)
    if persistent and not current.may_use("openai"):
        return _no_conversation_to_keep()
    client = clients_mod.set_persistent_sessions(client_id, persistent, actor=str(request.get("user") or "owner"))
    if client is None:
        return json_error("not_found", message="Unknown client", status=404)
    return web.json_response({"ok": True, "client_id": client_id, "persistent_sessions": client.persistent_sessions})


async def api_external_access_client_toggle(request: web.Request) -> web.Response:
    """POST /api/external-access/clients/{client_id}/disabled — kill-switch layer (c).

    Body ``{disabled: bool}``. Separate from the create/revoke route because
    "switch this integration off for an hour" and "destroy its credential" are
    different decisions, and collapsing them makes the reversible one feel final.
    """
    from gideon.integrations.inbound import clients as clients_mod

    client_id = str(request.match_info.get("client_id", "") or "")
    try:
        body = await read_json_body(request)
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict) or not isinstance(body.get("disabled"), bool):
        return json_error(
            "invalid_body", message="body must be {disabled: bool}", status=400
        )
    if not clients_mod.set_disabled(
        client_id, bool(body["disabled"]), reason="operator action"
    ):
        return json_error(
            "not_found", message=f"unknown client {client_id!r}", status=404
        )
    return web.json_response(
        {"ok": True, "client_id": client_id, "disabled": body["disabled"]}
    )
