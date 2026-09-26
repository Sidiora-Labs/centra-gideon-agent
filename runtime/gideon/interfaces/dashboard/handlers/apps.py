"""App Platform REST API (A4).

The lifecycle layer (A1–A3) exposed over HTTP, plus the backend reverse-proxy:

    GET    /api/apps                      — installed apps + state
    GET    /api/apps/{name}               — manifest + status + config + backend
    POST   /api/apps                      — install from source (path | git URL)
    POST   /api/apps/{name}/enable        — enable (run onEnable, register)
    POST   /api/apps/{name}/disable       — disable
    POST   /api/apps/{name}/update        — atomic update from source
    DELETE /api/apps/{name}               — deactivate | ?remove=1 remove-keep-data
                                            | ?force=1 remove-everything (dep ledger)
    GET    /api/apps/{name}/uninstall-preview — classify shared deps (A3) + data/ facts
    GET    /api/apps/{name}/config        — read config + the configSchema
    PUT    /api/apps/{name}/config        — validate + persist config
    *      /apps/{name}/api/{tail:.*}      — reverse-proxy to the app's backend

Lifecycle routes are SEL-audited inside the manager. Install/update run the
shared scanner gate: a ``dangerous`` verdict is refused (non-overridable); a
``warning`` returns ``needs_consent`` unless the request passes ``confirm:true``
(the install UI's explicit owner consent).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.http_errors import json_error
from gideon.security.security import (
    is_sensitive_path,
    redact_credentials,
    redact_exfiltration_urls,
)

logger = logging.getLogger(__name__)

_LIVE_CHANNEL_CONFIG_APPS = frozenset({
    "matrix-channel",
    "wecom-channel",
    "dingtalk-channel",
    "qq-channel",
    "feishu-channel",
    "mochat-channel",
    "whatsapp-channel",
    "weixin-channel",
})


def _redact(text: str) -> str:
    """Redact exfil URLs + credentials from LLM-derived agent output before it
    crosses back to an app (same two-pass discipline as messaging._redact)."""
    text, _ = redact_exfiltration_urls(text or "")
    text, _ = redact_credentials(text)
    return text


_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
_PROXY_TIMEOUT = 30


def register_app_routes(app: web.Application) -> None:
    """Register the App Platform REST + proxy routes on an aiohttp app.

    Specific sub-paths are registered before the catch-all ``/api/apps/{name}``
    GET/DELETE so routing isn't shadowed."""
    app.router.add_get("/api/apps", api_apps_list)
    app.router.add_post("/api/apps", api_app_install)
    app.router.add_get("/api/apps/catalog", api_app_catalog)
    app.router.add_get("/api/apps/sources", api_app_sources_list)
    app.router.add_post("/api/apps/sources", api_app_sources_add)
    app.router.add_delete("/api/apps/sources", api_app_sources_remove)
    app.router.add_get("/api/apps/local-sources", api_app_local_sources_list)
    app.router.add_post("/api/apps/local-sources", api_app_local_sources_add)
    app.router.add_delete("/api/apps/local-sources", api_app_local_sources_remove)
    app.router.add_post("/api/apps/{name}/enable", api_app_enable)
    app.router.add_post("/api/apps/{name}/disable", api_app_disable)
    app.router.add_post("/api/apps/{name}/update", api_app_update)
    app.router.add_get("/api/apps/{name}/uninstall-preview", api_app_uninstall_preview)
    app.router.add_get("/api/apps/{name}/config", api_app_config_get)
    app.router.add_put("/api/apps/{name}/config", api_app_config_put)
    app.router.add_post("/api/apps/{name}/agent-run", api_app_agent_run)
    app.router.add_get("/api/apps/{name}/agent-run/{run_id}", api_app_agent_run_status)
    app.router.add_post("/api/apps/{name}/token", api_app_token)
    app.router.add_post("/api/apps/message", api_app_message_send)
    app.router.add_get("/api/apps/message", api_app_message_poll)
    app.router.add_get("/api/apps/{name}", api_app_get)
    app.router.add_delete("/api/apps/{name}", api_app_uninstall)
    app.router.add_get("/apps/{name}/ui/{tail:.*}", api_app_ui_asset)
    app.router.add_route("*", "/apps/{name}/api/{tail:.*}", api_app_proxy)


def _sel_log(
    op: str, outcome: str, resources: str, request: web.Request, error: str = ""
) -> None:
    """Append one app-lifecycle row to the security event log.

    🔴 `resources` carries the app SOURCE, which for a git app is a URL — and a URL may carry
    credentials in its userinfo (`https://user:token@host/repo.git`). This wrote it verbatim into an
    HMAC-chained, append-only log (#406), which is the one surface here that CANNOT be cleaned up
    afterwards: rewriting a row breaks the chain, so a leaked secret is in the audit trail
    permanently. That is why this call site is screened even though the source is also refused at
    `catalog.add_git_source` — the refusal stops new ones, and this stops the ones that arrive by
    any other route.

    Screened ONCE, here, at the point of entry to the log. Deliberately not at a shared trailing
    chokepoint: `redact_credentials` is not idempotent over a composed `key: [REDACTED: …]` line —
    it garbles the text and takes the field NAME with it — so a second sweep over already-screened
    text is a corruption, not a belt-and-braces.
    """
    try:
        from gideon.security.security import redact_credentials
        from gideon.security.sel import sel as _s

        safe_resources, _ = redact_credentials(resources)
        safe_error, _ = redact_credentials(error)
        _s().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=op,
            outcome=outcome,
            source="apps",
            resources=safe_resources,
            error=safe_error,
        )
    except Exception:
        pass


def _reconcile_app_crons(request: web.Request) -> None:
    """Re-run app-cron reconciliation after a lifecycle transition (install /
    enable / disable / uninstall / update).

    App-declared manifest crons are otherwise reconciled only once at gateway
    startup, while MCP servers reconcile on every transition — so without this a
    disabled/uninstalled app's cron kept firing agent jobs (and a freshly-enabled
    app's cron didn't register) until the next restart. Reconciliation is
    idempotent + declarative (diffs desired app:* triggers against registered ones),
    so calling it on each transition simply converges the scheduler. Best-effort:
    any error is swallowed, never blocking the lifecycle response.

    Reconciles into the unified TRIGGER STORE (S108). It used to pass `state.crons`, which wrote
    `crons.json` — a file the clock engine does not read — so a freshly enabled app's cron was inert
    until the next boot imported it, which is exactly the restart this seam exists to avoid.
    The `--no-crons` guard moved with it: the store is a file, not a service, so the `state.crons`
    presence check no longer answers the question. `no_crons` on the dashboard state does.
    """
    try:
        state = request.app.get("state")
        if state is not None and getattr(state, "no_crons", False):
            return
        from gideon.automation.triggers.store import TriggerStore
        from gideon.core.config.loader import config_dir
        from gideon.extensions.apps.app_crons import reconcile_app_crons

        reconcile_app_crons(TriggerStore(base_dir=config_dir()))
    except Exception:
        logger.debug(
            "app cron reconcile after lifecycle transition failed", exc_info=True
        )


def _app_status(name: str) -> dict[str, Any]:
    """Runtime status for an app: enabled + backend running/port."""
    from gideon.extensions.apps.backend_runtime import get_backend_supervisor

    rb = get_backend_supervisor().get(name)
    return {
        "backendRunning": rb is not None,
        "backendPort": rb.port if rb else None,
    }


def _quality_wire(raw: Any) -> dict[str, Any]:
    """The DECLARED quality axes, and only those (APE-4).

    Routed through :class:`~gideon.extensions.apps.manifest.QualityDeclaration` rather than
    passed through raw, so the tri-state survives one hop: an axis the app never
    declared is ABSENT here, not ``false``. Passing the raw dict through would work
    today and break the moment a manifest carries a junk axis; parsing keeps the
    Library wire and the Store's catalog wire on one shape.
    """
    from gideon.extensions.apps.manifest import QualityDeclaration

    if not isinstance(raw, dict):
        return {}
    return QualityDeclaration.from_dict(raw).to_dict()


async def api_apps_list(request: web.Request) -> web.Response:
    """GET /api/apps — installed apps with manifest summary + runtime state.

    APE-7: on this existing read path (no polling loop) we also compute which installed
    apps have a newer version available from their local source, tag each such app
    ``updateAvailable`` + ``latestVersion`` for the Library card badge, and emit ONE
    notification per newly-available version (deduped by ``name + latest_version`` in
    ``surface_app_updates`` so re-viewing never re-nags)."""
    from gideon.extensions.apps.catalog import (
        resolve_hero_url,
        source_kind_for_origin,
        surface_app_updates,
    )
    from gideon.extensions.apps.manager import app_dir, list_apps

    updates_by_name: dict[str, dict[str, Any]] = {}
    try:
        state = request.app.get("state")
        updates = await asyncio.to_thread(surface_app_updates, state)
        updates_by_name = {u["name"]: u for u in updates}
    except Exception:
        logger.debug("apps list: update surfacing skipped", exc_info=True)

    out: list[dict[str, Any]] = []
    for app in list_apps():
        manifest = app.get("manifest", {})
        name = app.get("name", "")
        is_provider = bool(manifest.get("provider"))
        config_schema = (manifest.get("setup", {}) or {}).get("configSchema") or {}
        provider_schema = (manifest.get("provider", {}) or {}).get(
            "settingsSchema"
        ) or {}
        has_config = bool(
            config_schema.get("properties") or provider_schema.get("properties")
        )
        ui_pages = [
            {
                "route": p.get("route", ""),
                "label": p.get("label", ""),
                "icon": p.get("icon", ""),
            }
            for p in (manifest.get("ui", {}) or {}).get("pages", [])
            if p.get("route")
        ]
        out.append(
            {
                "name": name,
                "displayName": manifest.get("displayName") or name,
                "version": app.get("version", ""),
                "description": manifest.get("description", ""),
                "enabled": app.get("enabled", False),
                "origin": app.get("origin", ""),
                "sourceKind": source_kind_for_origin(
                    str(app.get("origin", "")),
                    native=bool(manifest.get("native", False)),
                ),
                "native": bool(manifest.get("native", False)),
                "source": app.get("source", ""),
                "icon": manifest.get("icon", ""),
                "heroUrl": resolve_hero_url(
                    app_dir(name), str(manifest.get("heroImage", ""))
                ),
                "hasBackend": bool(manifest.get("backend", {}).get("entryPoint")),
                "hasUI": bool(manifest.get("ui", {}).get("pages")),
                "uiPages": ui_pages,
                "uiComponents": str(
                    (manifest.get("ui", {}) or {}).get("components", "")
                ),
                "uiCapabilities": [
                    str(c) for c in (manifest.get("uiCapabilities") or []) if c
                ],
                "isProvider": is_provider,
                "providerType": (
                    (manifest.get("provider") or {}).get("type", "")
                    if is_provider
                    else ""
                ),
                "hasConfig": has_config,
                "permissions": manifest.get("permissions", {}),
                "tags": [str(t) for t in manifest.get("tags", []) if t],
                "quality": _quality_wire(manifest.get("quality")),
                "installedAt": app.get("installedAt", ""),
                "updatedAt": app.get("updatedAt", ""),
                "updateAvailable": name in updates_by_name,
                "latestVersion": updates_by_name.get(name, {}).get("latestVersion", ""),
                **_app_status(name),
            }
        )

    try:
        from gideon.extensions.providers.registry import get_provider_registry

        have = {a["name"] for a in out}
        if "gideon-filesystem" not in have:
            out.append(
                {
                    "name": "gideon-filesystem",
                    "displayName": "Filesystem & Shell Tools",
                    "version": "1.0.0",
                    "description": "Always-on platform tools — read/write/edit/list/glob/grep/repo_map, "  # noqa: E501
                    "bash, full-result retrieval. Required by the agent.",
                    "enabled": True,
                    "origin": "bundled",
                    "sourceKind": "native",
                    "icon": "FolderCog",
                    "heroUrl": "",
                    "hasBackend": False,
                    "hasUI": False,
                    "uiPages": [],
                    "isProvider": True,
                    "providerType": "tool",
                    "hasConfig": False,
                    "permissions": {},
                    "tags": [],
                    "installedAt": "",
                    "updatedAt": "",
                    "native": True,
                    "status": "running",
                }
            )
            have.add("gideon-filesystem")
        for ext in get_provider_registry().list_extensions():
            if ext.name in have:
                continue
            out.append(
                {
                    "name": ext.name,
                    "displayName": ext.manifest.displayName or ext.name,
                    "version": ext.manifest.version,
                    "description": ext.manifest.description,
                    "enabled": ext.enabled,
                    "origin": "bundled",
                    "sourceKind": "native",
                    "icon": ext.manifest.icon,
                    "heroUrl": resolve_hero_url(
                        app_dir(ext.name), ext.manifest.heroImage
                    ),
                    "hasBackend": False,
                    "hasUI": False,
                    "uiPages": [],
                    "isProvider": True,
                    "providerType": ext.provider_config.type,
                    "hasConfig": bool(
                        (ext.provider_config.settingsSchema or {}).get("properties")
                    ),
                    "permissions": {},
                    "tags": [],
                    "installedAt": "",
                    "updatedAt": "",
                    "native": True,
                    "status": "running" if ext.enabled else "stopped",
                }
            )
    except Exception:
        logger.debug(
            "apps list: bundled provider extensions append skipped", exc_info=True
        )

    return web.json_response({"apps": out})


async def api_app_catalog(request: web.Request) -> web.Response:
    """GET /api/apps/catalog — available-to-install apps (Store): bundled-but-not-
    installed manifests + the configured git source URLs."""
    from gideon.extensions.apps import catalog

    result = await asyncio.to_thread(catalog.available_catalog)
    return web.json_response(result)


async def api_app_sources_list(request: web.Request) -> web.Response:
    """GET /api/apps/sources — the configured git source URLs (defaults + user)."""
    from gideon.extensions.apps import catalog

    return web.json_response({"sources": catalog.list_git_sources()})


async def api_app_sources_add(request: web.Request) -> web.Response:
    """POST /api/apps/sources — add a user git source URL ``{url}``."""
    from gideon.extensions.apps import catalog

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    url = str(body.get("url", "")).strip()
    if not url:
        return web.json_response({"error": "url is required"}, status=400)
    try:
        catalog.add_git_source(url)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    _sel_log("apps.source_add", "ok", url, request)
    return web.json_response({"ok": True, "sources": catalog.list_git_sources()})


async def api_app_sources_remove(request: web.Request) -> web.Response:
    """DELETE /api/apps/sources?url=… — remove a user git source URL."""
    from gideon.extensions.apps import catalog

    url = request.query.get("url", "").strip()
    if not url:
        return web.json_response({"error": "url is required"}, status=400)
    catalog.remove_git_source(url)
    _sel_log("apps.source_remove", "ok", url, request)
    return web.json_response({"ok": True, "sources": catalog.list_git_sources()})


async def api_app_local_sources_list(request: web.Request) -> web.Response:
    """GET /api/apps/local-sources — the configured local app-source directories."""
    from gideon.extensions.apps import catalog

    return web.json_response({"sources": catalog.list_local_sources()})


async def api_app_local_sources_add(request: web.Request) -> web.Response:
    """POST /api/apps/local-sources — add a local app-source dir ``{path}`` (a
    directory of app subdirs; its apps then surface in the Store catalog)."""
    from gideon.extensions.apps import catalog

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    path = str(body.get("path", "")).strip()
    if not path:
        return web.json_response({"error": "path is required"}, status=400)
    try:
        catalog.add_local_source(path)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    _sel_log("apps.local_source_add", "ok", path, request)
    return web.json_response({"ok": True, "sources": catalog.list_local_sources()})


async def api_app_local_sources_remove(request: web.Request) -> web.Response:
    """DELETE /api/apps/local-sources?path=… — remove a local app-source dir."""
    from gideon.extensions.apps import catalog

    path = request.query.get("path", "").strip()
    if not path:
        return web.json_response({"error": "path is required"}, status=400)
    catalog.remove_local_source(path)
    _sel_log("apps.local_source_remove", "ok", path, request)
    return web.json_response({"ok": True, "sources": catalog.list_local_sources()})


async def api_app_get(request: web.Request) -> web.Response:
    """GET /api/apps/{name} — full manifest + status + saved config."""
    from gideon.extensions.apps.app_config import read_config
    from gideon.extensions.apps.app_manager import _manifest_of
    from gideon.extensions.apps.manager import _read_installed
    from gideon.extensions.apps.secret_fields import mask_secrets

    name = request.match_info["name"]
    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    manifest = _manifest_of(name)
    schema = _effective_config_schema(manifest) if manifest else {}
    masked, secret_set = mask_secrets(read_config(name), schema)
    return web.json_response(
        {
            "name": name,
            "installed": meta.to_dict(),
            "manifest": manifest.to_dict() if manifest else None,
            "config": masked,
            "configSchema": schema,
            "_secret_set": secret_set,
            **_app_status(name),
        }
    )


async def api_app_install(request: web.Request) -> web.Response:
    """POST /api/apps — install from ``{source, confirm?}``.

    ``source`` is a local directory path or a git URL. ``confirm:true`` consents
    to a ``warning`` scan verdict; a ``dangerous`` verdict is always refused."""
    from gideon.extensions.apps import app_manager
    from gideon.extensions.apps import source as app_source

    try:
        body: dict[str, Any] = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    src = str(body.get("source", "")).strip()
    if not src:
        return web.json_response({"error": "source is required"}, status=400)
    confirm = bool(body.get("confirm", False))

    try:
        resolved = await asyncio.to_thread(app_source.resolve, src)
    except app_source.SourceError as exc:
        _sel_log("apps.install", "error", src, request, error=str(exc))
        return web.json_response({"error": str(exc)}, status=400)

    def _do_install():
        try:
            return app_manager.install(
                resolved.path,
                origin=resolved.origin,
                confirm=confirm,
                caller=request.get("user", "dashboard"),
                source_ref=src,
            )
        finally:
            if resolved.cleanup:
                app_source._rmtree(resolved.cleanup_path)

    result = await asyncio.to_thread(_do_install)

    if result.ok:
        status = 201
    elif result.needs_consent:
        status = 409
    elif result.needs_client_install:
        status = 200
    else:
        status = 400
    _sel_log(
        "apps.install",
        "ok" if result.ok else "refused",
        result.name or src,
        request,
        error=result.error,
    )
    if result.ok:
        _reconcile_app_crons(request)
    return web.json_response(result.to_dict(), status=status)


async def api_app_update(request: web.Request) -> web.Response:
    """POST /api/apps/{name}/update — atomic update from ``{source, confirm?}``."""
    from gideon.extensions.apps import app_manager
    from gideon.extensions.apps import source as app_source

    name = request.match_info["name"]
    try:
        body = await read_json_body(request)
    except Exception:
        body = {}
    src = str(body.get("source", "")).strip()
    if not src:
        return web.json_response({"error": "source is required"}, status=400)
    confirm = bool(body.get("confirm", False))

    try:
        resolved = await asyncio.to_thread(app_source.resolve, src)
    except app_source.SourceError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    def _do_update():
        try:
            return app_manager.update(
                resolved.path,
                name,
                origin=resolved.origin,
                confirm=confirm,
                caller=request.get("user", "dashboard"),
            )
        finally:
            if resolved.cleanup:
                app_source._rmtree(resolved.cleanup_path)

    result = await asyncio.to_thread(_do_update)

    status = 200 if result.ok else (409 if result.needs_consent else 400)
    _sel_log(
        "apps.update", "ok" if result.ok else "error", name, request, error=result.error
    )
    if result.ok:
        _reconcile_app_crons(request)
    return web.json_response(result.to_dict(), status=status)


async def api_app_enable(request: web.Request) -> web.Response:
    from gideon.extensions.apps import app_manager

    name = request.match_info["name"]
    ok = await asyncio.to_thread(
        app_manager.enable,
        name,
        caller=request.get("user", "dashboard"),
    )
    _sel_log("apps.enable", "ok" if ok else "error", name, request)
    if not ok:
        return web.json_response({"error": f"enable failed for {name!r}"}, status=400)
    _reconcile_app_crons(request)
    return web.json_response({"ok": True, "name": name, "enabled": True})


async def api_app_disable(request: web.Request) -> web.Response:
    from gideon.extensions.apps import app_manager

    name = request.match_info["name"]
    ok = await asyncio.to_thread(
        app_manager.disable,
        name,
        caller=request.get("user", "dashboard"),
    )
    _sel_log("apps.disable", "ok" if ok else "error", name, request)
    if not ok:
        return web.json_response({"error": f"disable failed for {name!r}"}, status=400)
    _reconcile_app_crons(request)
    return web.json_response({"ok": True, "name": name, "enabled": False})


async def api_app_uninstall(request: web.Request) -> web.Response:
    """remove an app: no flag deactivates, ``?remove=1`` keeps ``data/``, ``?force=1`` wipes.

    The first line is the operator-facing summary in ``reference/routes.md`` (the
    generator takes it verbatim), so it names all three rungs on its own.

    * (no flag)     — uninstall = DEACTIVATE. Nothing leaves disk.
    * ``?remove=1`` — remove the app's files, KEEP its ``data/`` (issue #2541).
    * ``?force=1``  — remove everything, ``data/`` included.

    ``force`` is read first, so ``?force=1&remove=1`` wipes: when a request asks for
    two different promises about the user's data, the destructive one is the one that
    was explicitly confirmed, and honouring the weaker flag would silently keep data a
    caller asked to destroy. The default stays DEACTIVATE — an unflagged DELETE has
    never removed files and must not start now.
    """
    from gideon.extensions.apps import app_manager

    name = request.match_info["name"]
    force = request.query.get("force") in ("1", "true", "yes")
    remove = not force and request.query.get("remove") in ("1", "true", "yes")
    caller = request.get("user", "dashboard")
    if force:
        fn, op = app_manager.force_uninstall, "apps.force_uninstall"
    elif remove:
        fn, op = app_manager.uninstall_keep_data, "apps.uninstall_keep_data"
    else:
        fn, op = app_manager.uninstall, "apps.uninstall"
    ok = await asyncio.to_thread(fn, name, caller=caller)
    _sel_log(op, "ok" if ok else "error", name, request)
    if not ok:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    _reconcile_app_crons(request)
    return web.json_response(
        {
            "ok": True,
            "name": name,
            "forced": force,
            "removed": remove,
            "dataPreserved": remove,
        }
    )


async def api_app_uninstall_preview(request: web.Request) -> web.Response:
    """classify shared deps (A3) and report what the app's ``data/`` holds.

    The ``data`` block lets the removal-confirm dialogs name the trade the user is about
    to make instead of describing it in the abstract."""
    from gideon.extensions.apps import app_manager

    name = request.match_info["name"]
    classifications = app_manager.preview_uninstall(name)
    return web.json_response(
        {
            "name": name,
            "dependencies": [c.to_dict() for c in classifications],
            "data": app_manager.describe_app_data(name),
        }
    )


def _effective_config_schema(manifest) -> dict[str, Any]:
    """The schema that drives an app's config UI: an explicit ``setup.configSchema``
    if declared, else a provider app's ``provider.settingsSchema`` (where a
    pluggable provider declares its user-configurable settings). Lets a
    provider-only app expose its settings without duplicating the schema."""
    schema = manifest.setup.configSchema
    if schema:
        return schema
    if manifest.provider and manifest.provider.settingsSchema:
        return manifest.provider.settingsSchema
    return {}


async def api_app_config_get(request: web.Request) -> web.Response:
    from gideon.extensions.apps.app_config import read_config
    from gideon.extensions.apps.app_manager import _manifest_of
    from gideon.extensions.apps.secret_fields import mask_secrets

    name = request.match_info["name"]
    manifest = _manifest_of(name)
    if manifest is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    schema = _effective_config_schema(manifest)
    masked, secret_set = mask_secrets(read_config(name), schema)
    return web.json_response(
        {
            "name": name,
            "config": masked,
            "schema": schema,
            "_secret_set": secret_set,
        }
    )


async def api_app_config_put(request: web.Request) -> web.Response:
    from gideon.extensions.apps.app_config import (
        AppConfigError,
        read_config,
        write_config,
    )
    from gideon.extensions.apps.app_manager import _manifest_of
    from gideon.extensions.apps.secret_fields import (
        mask_secrets,
        preserve_unchanged_secrets,
    )

    name = request.match_info["name"]
    manifest = _manifest_of(name)
    if manifest is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    try:
        values = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    schema = _effective_config_schema(manifest)
    values = preserve_unchanged_secrets(values, read_config(name), schema)
    try:
        saved = write_config(name, values, schema)
    except AppConfigError as exc:
        _sel_log("apps.config", "error", name, request, error=str(exc))
        return web.json_response({"error": str(exc)}, status=400)
    if name in _LIVE_CHANNEL_CONFIG_APPS:
        from gideon.extensions.providers.registry import get_provider_registry

        registry = get_provider_registry()
        extension = registry.get(name)
        if extension is not None and extension.enabled:
            current = extension.provider_instance
            services = getattr(current, "services", None)
            if current is not None:
                await current.disconnect()
            registry.disable(name)
            if not registry.enable(name):
                return web.json_response({"error": extension.error or "Channel could not restart"}, status=500)
            updated = registry.get(name)
            if services is not None and updated is not None and updated.provider_instance is not None:
                try:
                    await updated.provider_instance.start_inbound(services)
                except Exception as exc:
                    logger.exception("Could not start pairing channel %s after configuration", name)
                    return web.json_response({"error": str(exc)}, status=500)
    _sel_log("apps.config", "ok", name, request)
    masked, secret_set = mask_secrets(saved, schema)
    return web.json_response(
        {"ok": True, "name": name, "config": masked, "_secret_set": secret_set}
    )


def _app_agent_allowed(name: str) -> bool:
    """Whether the app may run an agent: installed, enabled, and declaring `agent`.

    The lifecycle half is not redundant with ``app_permission_middleware``. That runs
    only for a request carrying an app identity, and ``_agent_run_identity`` falls back
    to the path segment for OWNER-initiated calls — which the dashboard makes whenever
    app-token minting failed, and minting is exactly what refuses a disabled app. So the
    fallback was the one way to start an agent run for an app the owner had switched off.
    """
    from gideon.extensions.apps.permissions import app_lifecycle_denial, checker_for

    if app_lifecycle_denial(name):
        return False
    checker = checker_for(name)
    return checker is not None and checker.can_use_agent()


def _agent_run_identity(request: web.Request) -> tuple[str, str]:
    """Return ``(request_app, name)`` — the caller's verified app identity, and the
    app identity these routes must gate on.

    The URL's ``{name}`` is caller-chosen, so gating on it checks the WRONG app: an
    app that legitimately declares ``api: ["/api/apps"]`` (prefix-matched by
    ``app_permission_middleware``) could name any agent-permitted app in the path and
    borrow its permission. ``request["app"]`` is the verified identity from the
    app-scoped token, so it wins whenever it is present. ``request_app`` is empty for
    owner-initiated calls (dashboard / CLI), which fall back to the path segment —
    the only identity they carry."""
    request_app = request.get("app", "")
    return request_app, (request_app or request.match_info["name"])


async def api_app_agent_run(request: web.Request) -> web.Response:
    """POST /api/apps/{name}/agent-run — start a background agent task.

    Body: ``{task, agent?, max_turns?}``. Runs a headless subagent (auto-approve,
    silent) on behalf of the app and returns its ``{id}``; the app polls
    ``/agent-run/{id}`` for the result. Requires the ``agent`` permission of the
    CALLING app, not of the app named in the path."""
    _, name = _agent_run_identity(request)
    if not _app_agent_allowed(name):
        _sel_log(
            "apps.agent_run",
            "denied",
            name,
            request,
            error="agent permission not granted",
        )
        return web.json_response(
            {"error": f"app {name!r} does not declare the 'agent' permission"},
            status=403,
        )

    state = request.app["state"]
    if not getattr(state, "subagents", None):
        return web.json_response({"error": "subagents not available"}, status=503)
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if body is not None and not isinstance(body, dict):
        return json_error(
            "invalid_body", message="JSON body must be an object", status=400
        )
    body = body or {}
    task = str(body.get("task", "")).strip()
    if not task:
        return web.json_response({"error": "task is required"}, status=400)
    agent = str(body.get("agent", "")) or ""
    try:
        max_turns = int(body.get("max_turns", 0) or 0)
    except (TypeError, ValueError):
        max_turns = 0

    info = state.subagents.spawn(
        task,
        parent_session_key=f"app:{name}",
        agent=agent,
        max_turns=max_turns,
        approval_mode="auto",
        capability_class="mutating",
        silent=True,
    )
    if not info:
        return web.json_response(
            {"error": f"capacity reached ({state.subagents.max_concurrent})"},
            status=429,
        )
    if info.done and info.error:
        _sel_log("apps.agent_run", "error", name, request, error=info.error)
        return web.json_response({"error": info.error}, status=400)
    _sel_log("apps.agent_run", "ok", name, request, error="")
    return web.json_response(
        {"id": info.id, "task": task, "status": "running"}, status=202
    )


async def api_app_agent_run_status(request: web.Request) -> web.StreamResponse:
    """GET /api/apps/{name}/agent-run/{run_id} — poll a background agent task.

    Returns ``{id, done, result?, error?, turns?, elapsed?}``. Requires the ``agent``
    permission of the CALLING app, and the run must belong to that app."""
    request_app, name = _agent_run_identity(request)
    run_id = request.match_info["run_id"]
    if not _app_agent_allowed(name):
        return web.json_response(
            {"error": f"app {name!r} does not declare the 'agent' permission"},
            status=403,
        )

    state = request.app["state"]
    if not getattr(state, "subagents", None):
        return web.json_response({"error": "subagents not available"}, status=503)
    info = state.subagents.get(run_id)
    if not info:
        return web.json_response({"error": "not found"}, status=404)
    # DelegationSupervisor keeps ONE flat run table shared by every spawner (chat, cron,
    if request_app and info.parent_session_key != f"app:{request_app}":
        _sel_log(
            "apps.agent_run_status",
            "denied",
            f"{name}:{run_id}",
            request,
            error="run not owned by this app",
        )
        return web.json_response({"error": "not found"}, status=404)
    data: dict[str, Any] = {
        "id": info.id,
        "task": info.task,
        "done": info.done,
        "turns": info.turns,
        "elapsed": round(time.time() - info.started),
    }
    if info.done:
        result = info.result
        if getattr(info, "result_path", "") and not is_sensitive_path(info.result_path):
            try:
                result = await asyncio.to_thread(
                    Path(info.result_path).read_text, encoding="utf-8", errors="replace"
                )
            except OSError:
                pass
        data["result"] = _redact(result or "")
        data["error"] = _redact(info.error) if info.error else ""
    return web.json_response(data)


_APP_TOKEN_TTL_SECS = 3600


async def api_app_token(request: web.Request) -> web.Response:
    """POST /api/apps/{name}/token — mint an app-scoped identity token.

    An installed app's SDK calls this on mount; the returned token carries an
    ``app`` claim so every subsequent app request (fetch ``Authorization: Bearer`` +
    the ``/api/ws?app_token=`` handshake) is attributable to THIS app. The token
    auth middleware sets ``request["app"]`` from the claim, and the app-permission
    middleware + WS event filter gate on it. Bound to the current owner user (an app
    never exceeds the owner's own reach) and short-lived.

    Only the OWNER (a non-app request) may mint an app token — an app can't mint a
    token for a different app to escalate."""
    from gideon.extensions.apps.manager import _read_installed
    from gideon.interfaces.dashboard.token_auth import generate_token

    name = request.match_info["name"]
    if request.get("app", ""):
        return web.json_response({"error": "apps may not mint tokens"}, status=403)

    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    if not meta.enabled:
        return web.json_response({"error": f"app {name!r} is disabled"}, status=403)

    user_id = request.get("user", "dashboard")
    token = generate_token(user_id, ttl_seconds=_APP_TOKEN_TTL_SECS, app=name)
    return web.json_response({"token": token, "expires_in": _APP_TOKEN_TTL_SECS})


async def api_app_message_send(request: web.Request) -> web.Response:
    """POST /api/apps/message — send a typed message ``{to, type, payload}`` to
    another app.

    The SENDER is ``request["app"]`` (the verified app-scoped token identity), NOT a
    body field — so an app cannot claim to be a different sender. Requires the
    sender's ``appMessaging`` grant for the target: an undeclared pair is refused
    403 AND written to the SEL audit chain (fail closed). The payload is size-capped
    and fenced as untrusted before it reaches the target."""
    from gideon.extensions.apps.messaging import AppMessageError, send_message

    sender = request.get("app", "")
    if not sender:
        return web.json_response(
            {"error": "app-scoped identity required to send an app message"}, status=403
        )
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    target = str(body.get("to", "")).strip()
    msg_type = str(body.get("type", "")).strip()
    payload = body.get("payload", "")
    if not target:
        return web.json_response({"error": "'to' (target app) is required"}, status=400)
    if not msg_type:
        return web.json_response({"error": "'type' is required"}, status=400)
    try:
        msg = send_message(
            sender=sender, target=target, msg_type=msg_type, payload=payload
        )
    except AppMessageError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    return web.json_response({"ok": True, "id": msg.id, "to": target}, status=202)


async def api_app_message_poll(request: web.Request) -> web.Response:
    """GET /api/apps/message — drain THIS app's inbox (read-once).

    Scoped to ``request["app"]`` — an app reads only its OWN queue, never another
    app's. Each message carries the (verified) sender, the typed discriminator, and
    the fenced payload."""
    from gideon.extensions.apps.messaging import drain_queue

    reader = request.get("app", "")
    if not reader:
        return web.json_response(
            {"error": "app-scoped identity required to read app messages"}, status=403
        )
    return web.json_response({"messages": drain_queue(reader)})


async def api_app_proxy(request: web.Request) -> web.StreamResponse:
    """Reverse-proxy a request to an app's backend subprocess.

    Matches ``/apps/{name}/api/{tail:.*}`` for any method. 404 if the app isn't
    installed, 502 if its backend isn't running, 403 if the app is disabled.

    The owner's session credential (cookie / bearer) is STRIPPED before forwarding —
    an app backend must never receive the owner's token (it could replay it against
    the full gateway API). Instead we forward a fresh app-scoped token so the backend
    has an identity bounded to its own declared permissions."""
    import aiohttp

    from gideon.extensions.apps.backend_runtime import get_backend_supervisor
    from gideon.extensions.apps.manager import _read_installed
    from gideon.interfaces.dashboard.token_auth import generate_token

    name = request.match_info["name"]
    tail = request.match_info.get("tail", "")

    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    if not meta.enabled:
        return web.json_response({"error": f"app {name!r} is disabled"}, status=403)

    rb = get_backend_supervisor().get(name)
    if rb is None:
        return web.json_response(
            {
                "error": "The app's backend is not running. Check the app's logs and try again."
            },
            status=502,
        )

    from yarl import URL

    from gideon.extensions.apps.app_secret import read_app_secret
    from gideon.sdk.security import PROXY_SIGNATURE_HEADER, sign_proxy_request

    target_url = (
        URL(rb.base_url).with_path(f"/{tail}").with_query(request.rel_url.query)
    )
    path_qs = target_url.raw_path_qs

    proxy_secret = read_app_secret(name)
    if not proxy_secret:
        logger.warning(
            "app %s proxy: secret missing; refusing to forward unsigned", name
        )
        return web.json_response({"error": "app backend not available"}, status=502)

    _STRIP = _HOP_BY_HOP | {"cookie", "authorization", "x-gideon-app"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    user_id = request.get("user", "dashboard")
    fwd_headers["Authorization"] = (
        f"Bearer {generate_token(user_id, ttl_seconds=_APP_TOKEN_TTL_SECS, app=name)}"
    )
    fwd_headers["X-Gideon-App"] = name
    body = await request.read()
    fwd_headers[PROXY_SIGNATURE_HEADER] = sign_proxy_request(
        proxy_secret, request.method, path_qs, body
    )
    timeout = aiohttp.ClientTimeout(total=_PROXY_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                request.method,
                target_url,
                headers=fwd_headers,
                data=body or None,
                allow_redirects=False,
            ) as upstream:
                resp = web.StreamResponse(status=upstream.status)
                for k, v in upstream.headers.items():
                    if k.lower() not in _HOP_BY_HOP:
                        resp.headers[k] = v
                await resp.prepare(request)
                async for chunk in upstream.content.iter_chunked(8192):
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.warning("app %s proxy failed: %s", name, exc)
        copy = (
            "The app's backend timed out. Check the app's logs and try again."
            if isinstance(exc, TimeoutError)
            else "The app's backend could not be reached. Check the app's logs and try again."
        )
        return web.json_response({"error": copy}, status=502)
    except Exception as exc:  # noqa: BLE001
        logger.warning("app %s proxy failed: %s", name, exc)
        return web.json_response(
            {
                "error": (
                    "The request to the app's backend failed unexpectedly. "
                    "Check the app's logs and try again."
                )
            },
            status=502,
        )


_UI_CONTENT_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".map": "application/json",
}


async def api_app_ui_asset(request: web.Request) -> web.StreamResponse:
    """Serve an installed app's contributed UI bundle file (the ESM the frontend
    code-splits in to mount the app's page). Confined to the app's ``ui/`` dir
    with a path-traversal guard; only enabled apps serve UI."""
    from gideon.extensions.apps.manager import _read_installed, app_dir

    name = request.match_info["name"]
    tail = request.match_info.get("tail", "")
    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    if not meta.enabled:
        return web.json_response({"error": f"app {name!r} is disabled"}, status=403)

    ui_root = (app_dir(name) / "ui").resolve()
    target = (ui_root / tail).resolve()
    if not target.is_relative_to(ui_root) or not target.is_file():
        return web.json_response({"error": "not found"}, status=404)

    ctype = _UI_CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return web.FileResponse(target, headers={"Content-Type": ctype})
