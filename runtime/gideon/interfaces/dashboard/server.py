"""Dashboard aiohttp application factory and startup."""

import asyncio
import errno
import logging
import os
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from gideon.cognition.suggestions import api_suggestions
from gideon.core.config import config_dir
from gideon.core.layout import package_path
from gideon.engine.hooks import ScriptHookStore, set_global_hook_store
from gideon.interfaces.dashboard import chat, handlers, handlers_inbox, ws
from gideon.interfaces.dashboard.handlers.knowledge import setup_knowledge_routes
from gideon.interfaces.dashboard.handlers.research_reports import (
    setup_research_report_routes,
)
from gideon.interfaces.dashboard.origin import (
    allowed_cors_origin,
    build_allowed_origins,
    check_origin,
    resolve_bind_host,
)
from gideon.interfaces.dashboard.state import _DEFAULT_PORT, ConsoleState
from gideon.interfaces.dashboard.token_auth import token_auth_middleware

if TYPE_CHECKING:
    from gideon.interfaces.dashboard._types import (
        ConversationDirectory,
        ConversationLog,
        DelegationSupervisor,
        HistoryConsolidator,
        PromptAssembler,
    )

logger = logging.getLogger(__name__)


def _new_gateway_id() -> str:
    return f"{os.getpid()}-{secrets.token_hex(8)}"


def _log_auth_mode(auth_cfg: Any) -> None:
    """Log both sides of auth resolution without changing the selected middleware."""
    state = auth_cfg.mode_state()
    if auth_cfg.fell_back_from_unauthorable_mode:
        logger.warning(
            "Auth mode fallback: %s; requested mode is not authorable by this runtime",
            state,
        )
    else:
        logger.info("Auth mode: %s", state)


def _single_post_ceiling() -> int:
    """Body-size ceiling for the MAIN + API apps.

    These apps carry only small single-POST uploads (≤ the policy's single-POST
    threshold) + every non-upload endpoint, so their ceiling tracks the threshold
    + multipart overhead — kept deliberately tight. Large media uploads go through
    the resumable protocol on the dedicated 2 GB upload sub-app, never these apps."""
    from gideon.workspace.uploads import single_post_threshold

    return single_post_threshold() + 16 * 1024 * 1024


_DIST_DIR = package_path("static", "dist")

_SEL_PRUNE_INTERVAL_SECS = 6 * 60 * 60


_UPLOAD_SWEEP_INTERVAL_SECS = 60 * 60


async def _upload_sweep_loop() -> None:
    """Periodically delete abandoned resumable-upload session dirs (partial parts).

    A partial 2 GB upload the client never finishes would otherwise pin disk
    forever. Sweeps sessions idle past the store TTL, at startup then hourly."""
    from gideon import shutdown_event
    from gideon.interfaces.dashboard.handlers.files import _upload_dir
    from gideon.workspace.uploads.store import UploadStore

    store = UploadStore(Path(_upload_dir()) / ".parts")
    first = True
    while not shutdown_event.is_set():
        if not first:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=_UPLOAD_SWEEP_INTERVAL_SECS
                )
                return
            except asyncio.TimeoutError:
                pass
        first = False
        try:
            swept = await asyncio.get_running_loop().run_in_executor(None, store.sweep)
            if swept:
                logger.info("Upload sweep removed %d abandoned session(s)", swept)
        except Exception:
            logger.debug("upload sweep skipped", exc_info=True)


async def _sel_prune_loop() -> None:
    """Periodically trim the SEL audit log so it can't grow unbounded.

    Every gateway/channel/mcp action (incl. dashboard polls) appends an entry, so
    without this the file grows to millions of lines and the audit reads/verify
    crawl. Prunes once at startup, then every few hours, on an executor thread
    (the prune rewrites the whole file)."""
    from gideon import shutdown_event

    first = True
    while not shutdown_event.is_set():
        if not first:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=_SEL_PRUNE_INTERVAL_SECS
                )
                return
            except asyncio.TimeoutError:
                pass
        first = False
        try:
            from gideon.security.sel import SecurityEventLog

            removed = await asyncio.get_running_loop().run_in_executor(
                None, SecurityEventLog().prune
            )
            if removed:
                logger.info("SEL prune removed %d entries", removed)
        except Exception:
            logger.debug("SEL prune skipped", exc_info=True)


def _precompute_telemetry(state: "ConsoleState") -> None:
    """Pre-compute telemetry data (blocking I/O — call before server starts)."""
    from gideon.interfaces.dashboard.handlers_system import (
        _get_owner_hash,
        _get_static_system_info,
    )

    _log = logging.getLogger(__name__)
    try:
        _get_owner_hash(state)
    except Exception:
        _log.warning("Failed to pre-compute owner hash", exc_info=True)
    try:
        _get_static_system_info()
    except Exception:
        _log.warning("Failed to pre-compute system info", exc_info=True)


def _register_upload_routes(app: web.Application) -> None:
    """Register the resumable large-file upload protocol routes."""
    from gideon.interfaces.dashboard.handlers import uploads as _up

    app.router.add_get("/api/uploads/limits", _up.api_uploads_limits)
    app.router.add_post("/api/uploads/init", _up.api_uploads_init)
    app.router.add_put("/api/uploads/{id}/part", _up.api_uploads_part)
    app.router.add_get("/api/uploads/{id}", _up.api_uploads_status)
    app.router.add_post("/api/uploads/{id}/complete", _up.api_uploads_complete)


def _register_mcp_routes(app: web.Application) -> None:
    """Register API routes used by MCP tools (spawn, lessons, crons, etc.)."""
    app.router.add_post("/api/spawn", handlers.api_spawn)
    app.router.add_post("/api/spawn/cancel-fanout", handlers.api_spawn_cancel_fanout)
    app.router.add_get("/api/spawn", handlers.api_spawn_list)
    app.router.add_get("/api/spawn/{agent_id}", handlers.api_spawn_status)
    app.router.add_delete("/api/spawn/{agent_id}", handlers.api_spawn_delete)
    app.router.add_delete("/api/spawn", handlers.api_spawn_clear)
    app.router.add_get("/api/lessons", handlers.api_lessons)
    app.router.add_post("/api/lessons", handlers.api_lessons_create)
    app.router.add_delete("/api/lessons", handlers.api_lessons_delete)
    from gideon.interfaces.dashboard.handlers.triggers import register_trigger_routes

    register_trigger_routes(app)
    app.router.add_post("/api/send-message", handlers.api_send_message)
    app.router.add_post("/api/session-keepalive", handlers.api_session_keepalive)
    app.router.add_get("/api/session-tool-policy", handlers.api_session_tool_policy)
    app.router.add_post("/api/channel/profile", handlers.api_channel_profile)
    app.router.add_get("/api/notifications", handlers.api_notifications)
    app.router.add_post("/api/notifications/clear", handlers.api_notifications_clear)

    from gideon.interfaces.dashboard.handlers.autonudge import (
        api_autonudge_delete,
        api_autonudge_get,
        api_autonudge_list,
        api_autonudge_start,
        api_autonudge_update,
    )

    app.router.add_get("/api/autonudge", api_autonudge_list)
    app.router.add_post("/api/autonudge", api_autonudge_start)
    app.router.add_get("/api/autonudge/session/{session_name}", api_autonudge_get)
    app.router.add_patch("/api/autonudge/{loop_id}", api_autonudge_update)
    app.router.add_delete("/api/autonudge/{loop_id}", api_autonudge_delete)


async def _start_site(site: web.TCPSite, port: int) -> None:
    """Start *site*, translating EADDRINUSE into an actionable message."""
    try:
        await site.start()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            hint = (
                f"Port {port} already in use — is another Gideon gateway running?\n"
                f"Stop it with: gideon stop  or  sudo systemctl stop gideon"
            )
            logger.error(hint)
            raise SystemExit(1) from exc
        raise


def _write_secret_file(secret_path: Path, secret: str) -> None:
    """Write *secret* to *secret_path* with mode 0o600.

    On failure the (possibly truncated) file is removed and the original
    ``OSError`` is re-raised.  Caller is responsible for any further
    cleanup (e.g. tearing down the app runner).
    """
    try:
        fd = os.open(str(secret_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secret)
    except OSError:
        try:
            secret_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _apply_startup_yolo(state: ConsoleState, cfg: Any) -> None:
    """Enable dashboard YOLO at startup if ``agent.yolo=true`` in config.

    Mirrors the channel gateway's startup behavior and
    emits an SEL audit event so config-driven permission changes are captured
    in the audit trail, matching the UI-toggle path in ``chat.py``.
    """
    if not cfg.agent.yolo:
        return
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller="dashboard:startup",
            operation="mode_change:yolo",
            outcome="enabled",
            resources="config:agent.yolo",
        )
    except Exception:
        logger.error(
            "SEL audit failed; refusing to enable YOLO mode from config", exc_info=True
        )
        return
    state.enable_yolo(from_config=True)
    logger.info("YOLO mode enabled at startup (agent.yolo=true)")


def _ws_csp_sources() -> str:
    """Extra `connect-src` entries for an internet-exposed instance (T4.1).

    Returns "" unless `dashboard.public_url` is set, so a normal local install keeps a
    byte-identical CSP. When set, both `wss://host` and `https://host` are added: the page is
    served over TLS through the tunnel, so the browser opens the WebSocket against the public
    origin rather than localhost, and a policy that omits it produces a dashboard that renders
    but never receives an event.
    """
    try:
        from gideon.security.exposure import public_host

        host = public_host()
        if not host:
            return ""
        return f" wss://{host} https://{host}"
    except Exception:  # noqa: BLE001
        logger.debug("could not resolve the public host for the CSP", exc_info=True)
        return ""


def _dashboard_csp() -> str:
    """Return the dashboard policy, including its explicit framing boundary."""
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' blob: "
        "https://cdn.tailwindcss.com https://cdn.jsdelivr.net "
        "https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com "
        "https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' data:; "
        f"connect-src 'self' ws://localhost:* ws://127.0.0.1:*{_ws_csp_sources()}; "
        "frame-src 'self' blob:; "
        "frame-ancestors 'self'; "
        "worker-src 'self' blob:; "
        "object-src 'none'; base-uri 'self'"
    )


def _append_vary(headers: Any, name: str) -> None:
    values = [value.strip() for value in headers.get("Vary", "").split(",") if value]
    if name.lower() not in {value.lower() for value in values}:
        values.append(name)
    headers["Vary"] = ", ".join(values)


def _apply_response_policies(
    request: web.Request, response: web.StreamResponse
) -> None:
    """Declare dashboard framing and the Agent Card's browser-origin boundary."""
    response.headers.setdefault("Content-Security-Policy", _dashboard_csp())
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

    if request.path != "/a2a/agent-card":
        return
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers.pop("Access-Control-Allow-Origin", None)
    origin = allowed_cors_origin(request)
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
    _append_vary(response.headers, "Origin")


@web.middleware  # type: ignore[misc]
async def spa_fallback(
    request: web.Request,
    handler: object,
) -> web.StreamResponse:
    try:
        return await handler(request)  # type: ignore[operator]
    except web.HTTPNotFound:
        if request.path.startswith("/api/"):
            from gideon.http_errors import json_error

            return json_error("not_found", status=404)
        if request.method == "GET" and not request.path.startswith(
            ("/assets/", "/icons/", "/sprites/", "/vendor/")
        ):
            return await handlers.index(request)
        raise
    except web.HTTPMethodNotAllowed as exc:
        if request.path.startswith("/api/"):
            from gideon.http_errors import json_error

            allow = exc.headers.get("Allow")
            return json_error(
                "method_not_allowed",
                status=405,
                headers={"Allow": allow} if allow else None,
            )
        raise


async def start_dashboard(
    sessions: "ConversationDirectory",
    port: int = _DEFAULT_PORT,
    subagents: "DelegationSupervisor | None" = None,
    context_builder: "PromptAssembler | None" = None,
    conversation_log: "ConversationLog | None" = None,
    consolidator: "HistoryConsolidator | None" = None,
    local_only: bool = True,
    configured_host: str = "",
    dashboard_url: str = "",
    owner_id: str = "",
) -> tuple[web.AppRunner, ConsoleState]:
    """Start the dashboard web server.  Returns ``(runner, state)``."""
    if consolidator is None and conversation_log is not None:
        try:
            from gideon.cognition import history as _hist_mod
            from gideon.cognition.memory import MemoryJournal

            memory = context_builder.memory if context_builder else MemoryJournal()
            if not context_builder:
                memory.init()
            consolidator = _hist_mod.HistoryConsolidator(
                log=conversation_log,
                memory=memory,
                sessions=sessions,
            )
            logger.info("Auto-created HistoryConsolidator for dashboard")
        except Exception:
            logger.debug("Could not create consolidator", exc_info=True)

    if sessions is not None:
        from gideon.integrations.mcp_client import with_mcp_session_eviction

        prior = consolidator.consolidate_session if consolidator is not None else None
        sessions.set_session_expire_callback(with_mcp_session_eviction(prior))

    state = ConsoleState(
        sessions=sessions,
        start_time=time.time(),
        subagents=subagents,
        context_builder=context_builder,
        conversation_log=conversation_log,
        consolidator=consolidator,
        owner_id=owner_id,
    )
    from gideon.integrations.mcp_client import set_mcp_elicitation_handler

    set_mcp_elicitation_handler(state.request_mcp_elicitation)

    state._hook_store = ScriptHookStore()
    set_global_hook_store(state._hook_store)

    from gideon.integrations.inbox_providers.native_source import (
        set_dashboard_state as _set_inbox_state,
    )

    _set_inbox_state(state)

    # create-task reach ConsoleState + a tracked background-spawn through it).
    def _spawn_background(coro: Any) -> Any:
        import asyncio as _asyncio

        task = _asyncio.ensure_future(coro)
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        return task

    from gideon.integrations.action_providers.services import (
        ActionServices,
        set_action_services,
    )

    set_action_services(
        ActionServices(
            state=state,
            spawn_background=_spawn_background,
            subagents=state.subagents,
        )
    )

    if state.subagents is not None:
        state.subagents.hook_store = state._hook_store

    state.wire_session_compact_callback()

    app = web.Application(client_max_size=_single_post_ceiling())
    app["state"] = state
    app["gateway_id"] = _new_gateway_id()
    state.load_folders()
    state.load_tags()
    app["port"] = port
    from gideon.security.auth.modes import AuthConfig as _AuthConfig

    app["auth_cfg"] = _AuthConfig.from_env()
    _log_auth_mode(app["auth_cfg"])

    _precompute_telemetry(state)

    _register_mcp_routes(app)

    ring_handler = handlers.install_log_ring_handler()
    if ring_handler:
        ring_handler.set_state(state)

    app.router.add_get("/", handlers.index)
    app.router.add_get("/gideon.svg", handlers.favicon)
    app.router.add_get("/manifest.webmanifest", handlers.manifest_webmanifest)
    app.router.add_get("/sw.js", handlers.service_worker)

    from gideon.interfaces.dashboard.handlers import auth as _auth_h

    app.router.add_get("/login", _auth_h.login_page)
    app.router.add_post("/api/auth/login", _auth_h.api_auth_login)
    app.router.add_get("/api/auth/status", _auth_h.api_login_status)
    app.router.add_post("/api/auth/logout", _auth_h.api_auth_logout)
    app.router.add_get("/api/auth/session", _auth_h.api_auth_session)
    app.router.add_post("/api/auth/password", _auth_h.api_auth_set_password)
    app.router.add_post("/api/auth/enroll/start", _auth_h.api_auth_enroll_start)
    app.router.add_post("/api/auth/enroll/complete", _auth_h.api_auth_enroll_complete)

    from gideon.interfaces.dashboard.handlers.devices import register_device_routes

    register_device_routes(app)

    from gideon.interfaces.dashboard.handlers.browse_connector import (
        register_browse_connector_routes,
    )

    register_browse_connector_routes(app)

    from gideon.interfaces.dashboard.handlers.push import register_push_routes

    register_push_routes(app)

    from gideon.interfaces.dashboard.handlers.browse_mirror import (
        register_browse_mirror_routes,
    )

    register_browse_mirror_routes(app)

    app.router.add_get("/api/ws", ws.api_ws)

    try:
        from gideon.integrations.inbound.mcp_http import mount as _mount_inbound_mcp

        _mount_inbound_mcp(app)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning("inbound: /mcp mount failed", exc_info=True)

    try:
        from gideon.integrations.inbound.capture_proxy import (
            register_routes as _register_capture,
        )

        _register_capture(app)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning(
            "inbound: /capture mount failed", exc_info=True
        )

    try:
        from gideon.integrations.inbound.openai_dialect import (
            register_routes as _register_openai,
        )
        from gideon.interfaces.dashboard.chat_handlers import _run_chat_scoped

        _register_openai(app, turn_runner=_run_chat_scoped)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning("inbound: /v1 mount failed", exc_info=True)
    try:
        from gideon.integrations.inbound.a2a import register_routes as _register_a2a

        _register_a2a(app)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning("inbound: /a2a mount failed", exc_info=True)

    app.router.add_get("/api/healthz", handlers.api_healthz)
    app.router.add_get("/api/status", handlers.api_status)
    app.router.add_get("/api/system", handlers.api_system)
    app.router.add_get("/api/auth-status", handlers.api_auth_status)
    app.router.add_get("/api/onboarding", handlers.api_onboarding)
    app.router.add_post("/api/onboarding/state", handlers.api_onboarding_state)
    from gideon.interfaces.dashboard.handlers.onboarding_import import (
        register_onboarding_import_routes,
    )

    register_onboarding_import_routes(app)
    app.router.add_get("/api/durability/status", handlers.api_durability_status)
    app.router.add_post("/api/durability/run", handlers.api_durability_run)
    app.router.add_post("/api/durability/export", handlers.api_durability_export)
    app.router.add_post("/api/durability/import", handlers.api_durability_import)
    app.router.add_get("/api/durability/archive", handlers.api_durability_archive)
    app.router.add_post(
        "/api/durability/archive/{id}/restore", handlers.api_durability_archive_restore
    )
    app.router.add_get("/api/durability/conflicts", handlers.api_durability_conflicts)
    app.router.add_post(
        "/api/durability/conflicts/{id}/resolve",
        handlers.api_durability_conflict_resolve,
    )
    app.router.add_get("/api/durability/history", handlers.api_durability_history)
    app.router.add_get(
        "/api/durability/history/{root}/timeline",
        handlers.api_durability_history_timeline,
    )
    app.router.add_post(
        "/api/durability/history/{root}/{op}", handlers.api_durability_history_operate
    )
    app.router.add_post("/api/desktop/register", handlers.api_desktop_register)
    app.router.add_post("/api/desktop/unregister", handlers.api_desktop_unregister)
    app.router.add_get("/api/desktop/state", handlers.api_desktop_state)
    app.router.add_post("/api/desktop/state", handlers.api_desktop_state_push)
    app.router.add_get(
        "/api/desktop/capabilities/{cap}", handlers.api_desktop_capability
    )
    app.router.add_get("/api/doctor", handlers.api_doctor)
    app.router.add_get("/api/doctor/fixes", handlers.api_doctor_fixes)
    app.router.add_get("/api/doctor/crash/{filename}", handlers.api_doctor_crash)
    app.router.add_get("/api/doctor/remediation", handlers.api_doctor_remediation)
    app.router.add_get("/api/doctor/{capability}", handlers.api_doctor_capability)
    app.router.add_get("/api/resilience/degraded", handlers.api_degraded)
    from gideon.interfaces.dashboard.handlers.feedback import register_feedback_routes

    register_feedback_routes(app)
    from gideon.interfaces.dashboard.handlers.packs import register_pack_routes

    register_pack_routes(app)
    from gideon.interfaces.dashboard.handlers.usage import register_usage_routes

    register_usage_routes(app)
    from gideon.interfaces.dashboard.handlers.model_telemetry import (
        register_model_telemetry_routes,
    )

    register_model_telemetry_routes(app)
    from gideon.interfaces.dashboard.handlers.learning import register_learning_routes

    register_learning_routes(app)
    from gideon.interfaces.dashboard.handlers.evals import register_evals_routes

    register_evals_routes(app)
    from gideon.interfaces.dashboard.handlers.investigate import (
        register_investigate_routes,
    )

    register_investigate_routes(app)
    app.router.add_post("/api/doctor/fix/{fix_id}", handlers.api_doctor_fix_apply)
    app.router.add_post(
        "/api/doctor/simulate/surfacing", handlers.api_doctor_simulate_surfacing
    )
    app.router.add_post(
        "/api/doctor/simulate/automation", handlers.api_doctor_simulate_automation
    )
    app.router.add_post(
        "/api/doctor/remediation/run", handlers.api_doctor_remediation_run
    )
    from gideon.interfaces.dashboard.handlers.skills import (
        api_ephemeral_skill_discard,
        api_ephemeral_skill_promote,
        api_ephemeral_skills_list,
        api_skill_files,
        api_skill_overlay_revert,
        api_skill_proposal_accept,
        api_skill_proposal_detail,
        api_skill_proposal_reject,
        api_skill_proposals_list,
        api_skill_verify,
        api_skills_delete,
        api_skills_install,
        api_skills_list,
        api_skills_marketplace_detail,
        api_skills_marketplaces,
        api_skills_search,
    )

    app.router.add_get("/api/skills", api_skills_list)
    app.router.add_get("/api/skills/marketplaces", api_skills_marketplaces)
    app.router.add_get("/api/skills/search", api_skills_search)
    app.router.add_get("/api/skills/marketplace/detail", api_skills_marketplace_detail)
    app.router.add_post("/api/skills/install", api_skills_install)
    app.router.add_get("/api/skills/ephemeral/{session}", api_ephemeral_skills_list)
    app.router.add_post(
        "/api/skills/ephemeral/{session}/promote", api_ephemeral_skill_promote
    )
    app.router.add_delete(
        "/api/skills/ephemeral/{session}/{slug}", api_ephemeral_skill_discard
    )
    app.router.add_get("/api/skills/proposals", api_skill_proposals_list)
    app.router.add_get("/api/skills/proposals/{id}", api_skill_proposal_detail)
    app.router.add_post("/api/skills/proposals/{id}/accept", api_skill_proposal_accept)
    app.router.add_delete("/api/skills/proposals/{id}", api_skill_proposal_reject)
    app.router.add_post("/api/skills/overlay/revert", api_skill_overlay_revert)
    app.router.add_get("/api/skills/{name}/files", api_skill_files)
    app.router.add_post("/api/skills/{name}/verify", api_skill_verify)
    app.router.add_delete("/api/skills/{name}", api_skills_delete)

    from gideon.interfaces.dashboard.handlers.apps import register_app_routes

    register_app_routes(app)
    from gideon.interfaces.dashboard.handlers.providers import (
        api_agent_provider_agents,
        api_agent_providers_list,
        api_agent_runners_list,
        api_provider_create,
        api_provider_delete,
        api_provider_model_delete,
        api_provider_model_pull,
        api_provider_model_search,
        api_provider_model_show,
        api_provider_models,
        api_provider_test,
        api_provider_types,
        api_provider_update,
        api_providers_list,
    )

    app.router.add_get("/api/model-providers", api_providers_list)
    app.router.add_get("/api/model-provider-types", api_provider_types)
    app.router.add_get("/api/agent-providers", api_agent_providers_list)
    app.router.add_get("/api/agent-providers/{id}/agents", api_agent_provider_agents)
    app.router.add_get("/api/agent-runners", api_agent_runners_list)
    app.router.add_post("/api/model-providers", api_provider_create)
    app.router.add_put("/api/model-providers/{name}", api_provider_update)
    app.router.add_delete("/api/model-providers/{name}", api_provider_delete)
    app.router.add_post("/api/model-providers/{name}/test", api_provider_test)
    app.router.add_get("/api/model-providers/{name}/models", api_provider_models)
    app.router.add_get("/api/model-providers/{name}/search", api_provider_model_search)
    app.router.add_get("/api/model-providers/{name}/show", api_provider_model_show)
    app.router.add_post("/api/model-providers/{name}/pull", api_provider_model_pull)
    app.router.add_post(
        "/api/model-providers/{name}/models/delete", api_provider_model_delete
    )

    from gideon.interfaces.dashboard.handlers.model_registry import (
        register_model_registry_routes,
    )

    register_model_registry_routes(app)

    from gideon.interfaces.dashboard.handlers.search_registry import (
        register_search_registry_routes,
    )

    register_search_registry_routes(app)

    from gideon.interfaces.dashboard.handlers.model_downloads import (
        register_model_download_routes,
    )

    register_model_download_routes(app)

    from gideon.interfaces.dashboard.handlers.embedding_reindex import (
        register_embedding_reindex_routes,
    )

    register_embedding_reindex_routes(app)

    app.router.add_get("/api/suggestions", api_suggestions)

    app.router.add_get("/api/memory/preferences", handlers.api_memory_preferences)
    app.router.add_put("/api/memory/preferences", handlers.api_memory_preferences)
    app.router.add_get("/api/memory/projects", handlers.api_memory_projects)
    app.router.add_put("/api/memory/projects", handlers.api_memory_projects)
    app.router.add_get("/api/memory/history", handlers.api_memory_history)
    app.router.add_put("/api/memory/history", handlers.api_memory_history)
    app.router.add_get("/api/memory/settings", handlers.api_memory_settings)
    app.router.add_put("/api/memory/settings", handlers.api_memory_settings)

    app.router.add_post("/api/stt/transcribe", handlers.api_stt_transcribe)

    from gideon.integrations.stt.handlers import register_stt_routes

    register_stt_routes(app)

    from gideon.cognition.lexicon.handlers import register_lexicon_routes

    register_lexicon_routes(app)

    app.router.add_get("/api/proactive/digest", handlers.api_proactive_digest)
    app.router.add_post("/api/proactive/digest/reply", handlers.api_proactive_reply)
    app.router.add_post("/api/proactive/install", handlers.api_proactive_install)

    app.router.add_get("/api/knowledge/decisions", handlers.api_decision_journal)

    app.router.add_get("/api/memory/approval-rules", handlers.api_memory_approval_rules)
    app.router.add_post(
        "/api/memory/approval-rules", handlers.api_memory_approval_rule_add
    )
    app.router.add_delete(
        "/api/memory/approval-rules/{key:.+}", handlers.api_memory_approval_rule_delete
    )
    app.router.add_get("/api/memory/semantic", handlers.api_memory_semantic)
    app.router.add_put("/api/memory/semantic", handlers.api_memory_semantic_write)
    app.router.add_delete(
        "/api/memory/semantic/{key:.+}", handlers.api_memory_semantic_delete
    )
    app.router.add_get("/api/memory/events", handlers.api_memory_events)
    app.router.add_post(
        "/api/memory/events/{event_id}/undo", handlers.api_memory_event_undo
    )
    app.router.add_get("/api/memory/lint", handlers.api_memory_lint)
    app.router.add_get(
        "/api/memory/episodic/search", handlers.api_memory_episodic_search
    )
    app.router.add_get("/api/memory/recall", handlers.api_memory_recall)
    app.router.add_get("/api/memory/episodic", handlers.api_memory_episodic_list)
    app.router.add_delete(
        "/api/memory/episodic/{id}", handlers.api_memory_episodic_delete
    )
    app.router.add_get("/api/memory/stats", handlers.api_memory_stats)
    app.router.add_get("/api/memory/vault", handlers.api_memory_vault_status)
    app.router.add_post("/api/memory/vault/sync", handlers.api_memory_vault_sync)
    app.router.add_get("/api/memory/daily-digests", handlers.api_memory_daily_digests)
    app.router.add_post("/api/memory/migrate", handlers.api_memory_migrate)
    app.router.add_post("/api/memory/import", handlers.api_memory_import)
    app.router.add_get(
        "/api/memory/context-preview", handlers.api_memory_context_preview
    )
    app.router.add_post("/api/memory/consolidate", handlers.api_memory_consolidate)
    app.router.add_get("/api/session/archive", handlers.api_session_archive_list)
    app.router.add_get("/api/session/archive/{name}", handlers.api_session_archive_read)
    app.router.add_get("/api/memory/observability", handlers.api_memory_observability)
    app.router.add_get("/api/memory/graph", handlers.api_memory_graph)
    app.router.add_post("/api/memory/promote", handlers.api_memory_promote)
    app.router.add_get("/api/memory/entities", handlers.api_memory_entities)
    app.router.add_post("/api/memory/entities", handlers.api_memory_entity_create)
    app.router.add_post(
        "/api/memory/entities/proposals", handlers.api_memory_entity_proposals
    )
    app.router.add_get(
        "/api/memory/entities/proposals", handlers.api_memory_entity_proposals_list
    )
    app.router.add_get(
        "/api/memory/entities/{entity_id}/backlinks",
        handlers.api_memory_entity_backlinks,
    )
    app.router.add_post("/api/memory/graph/rebuild", handlers.api_memory_graph_rebuild)
    app.router.add_get(
        "/api/memory/volunteer-stats", handlers.api_memory_volunteer_stats
    )
    app.router.add_get("/api/memory/graph/entities", handlers.api_memory_entity_graph)
    app.router.add_get("/api/memory/record-links", handlers.api_memory_record_links)
    app.router.add_get("/api/memory/graph/export", handlers.api_memory_graph_export)
    app.router.add_get("/api/memory/slots", handlers.api_memory_slots)
    app.router.add_post(
        "/api/memory/slots/{name}/lines", handlers.api_memory_slot_append
    )
    app.router.add_post(
        "/api/memory/slots/{name}/lines/retire", handlers.api_memory_slot_line_retire
    )

    app.router.add_get("/api/action-providers", handlers.api_action_providers)
    app.router.add_get("/api/agent-hooks", handlers.api_agent_hooks)

    app.router.add_get("/api/prompts", handlers.api_prompts)
    app.router.add_post("/api/prompts", handlers.api_prompt_create)
    app.router.add_get("/api/prompts/bindings", handlers.api_prompt_bindings)
    app.router.add_put("/api/prompts/bindings", handlers.api_prompt_bindings_save)
    app.router.add_post("/api/prompts/preview", handlers.api_prompt_preview)
    app.router.add_get("/api/prompts/syntax", handlers.api_prompt_syntax)
    app.router.add_post("/api/prompts/{name:.+}/render", handlers.api_prompt_render)
    app.router.add_post(
        "/api/prompts/{name:.+}/launch", handlers.api_campaign_template_launch
    )
    app.router.add_put("/api/prompts/{name:.+}", handlers.api_prompt_save)
    app.router.add_delete("/api/prompts/{name:.+}", handlers.api_prompt_delete)
    app.router.add_get("/api/prompts/{name:.+}", handlers.api_prompt_detail)

    app.router.add_get("/api/prompt-snippets", handlers.api_snippets)
    app.router.add_post("/api/prompt-snippets", handlers.api_snippet_create)
    app.router.add_post(
        "/api/prompt-snippets/{name:.+}/render", handlers.api_snippet_render
    )
    app.router.add_put("/api/prompt-snippets/{name:.+}", handlers.api_snippet_save)
    app.router.add_delete("/api/prompt-snippets/{name:.+}", handlers.api_snippet_delete)
    app.router.add_get("/api/prompt-snippets/{name:.+}", handlers.api_snippet_detail)

    app.router.add_post("/api/skills", handlers.api_skills_create)
    app.router.add_get("/api/skills/{name:.+}", handlers.api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", handlers.api_skill_detail)

    app.router.add_get("/api/themes", handlers.api_themes)
    app.router.add_post("/api/themes", handlers.api_themes_create)
    app.router.add_get("/api/themes/{slug}", handlers.api_theme_detail)
    app.router.add_put("/api/themes/{slug}", handlers.api_theme_detail)
    app.router.add_delete("/api/themes/{slug}", handlers.api_theme_detail)

    app.router.add_get("/api/agent/config", handlers.api_agent_config)
    app.router.add_put("/api/agent/config", handlers.api_agent_config)
    app.router.add_get("/api/config/default-agent", handlers.api_default_agent)
    app.router.add_put("/api/config/default-agent", handlers.api_default_agent)
    app.router.add_get("/api/config/schema", handlers.api_config_schema)
    app.router.add_get("/api/config/gideon", handlers.api_gideon_config)
    app.router.add_put("/api/config/gideon", handlers.api_gideon_config)
    app.router.add_patch("/api/config/gideon", handlers.api_gideon_config_patch)
    app.router.add_get("/api/config/settings", handlers.api_settings_config)
    app.router.add_get("/api/companion/discovery", handlers.api_companion_discovery)
    app.router.add_get("/api/incident", handlers.api_incident)
    app.router.add_post("/api/incident", handlers.api_incident)
    app.router.add_post("/api/incident/resume", handlers.api_incident_resume)
    app.router.add_get("/api/guardrails/project-trust", handlers.api_project_trust)
    app.router.add_post("/api/guardrails/project-trust", handlers.api_project_trust)
    app.router.add_get("/api/external-access", handlers.api_external_access)
    app.router.add_post(
        "/api/external-access/clients", handlers.api_external_access_client
    )
    app.router.add_delete(
        "/api/external-access/clients/{client_id}", handlers.api_external_access_client
    )
    app.router.add_post(
        "/api/external-access/clients/{client_id}/disabled",
        handlers.api_external_access_client_toggle,
    )
    app.router.add_get("/api/models/health", handlers.api_models_health)
    app.router.add_get("/api/autonomy", handlers.api_autonomy)
    app.router.add_post("/api/autonomy/grant", handlers.api_autonomy_grant)
    app.router.add_post("/api/autonomy/demote", handlers.api_autonomy_demote)
    app.router.add_post("/api/autonomy/undo", handlers.api_autonomy_undo)
    app.router.add_get("/api/dashboard/config", handlers.api_dashboard_config)
    app.router.add_put("/api/dashboard/config", handlers.api_dashboard_config)
    from gideon.interfaces.dashboard.handlers.views import (
        api_dashboard_view_detail,
        api_dashboard_view_tile_action,
        api_dashboard_view_tile_binding,
        api_dashboard_view_tile_refresh,
        api_dashboard_view_tile_resolve,
        api_dashboard_view_tiles,
        api_dashboard_views,
        api_genui_library,
    )

    app.router.add_get("/api/genui/library", api_genui_library)
    from gideon.interfaces.dashboard.handlers.surfaces import api_surface_overlays

    app.router.add_get("/api/surfaces/overlays", api_surface_overlays)
    app.router.add_get("/api/dashboard/views", api_dashboard_views)
    app.router.add_post("/api/dashboard/views", api_dashboard_views)
    app.router.add_post(
        "/api/dashboard/views/{view_id}/tiles/resolve", api_dashboard_view_tile_resolve
    )
    app.router.add_put(
        "/api/dashboard/views/{view_id}/tiles/binding", api_dashboard_view_tile_binding
    )
    app.router.add_post(
        "/api/dashboard/views/{view_id}/tiles/refresh", api_dashboard_view_tile_refresh
    )
    app.router.add_get(
        "/api/dashboard/views/{view_id}/tiles/refresh", api_dashboard_view_tile_refresh
    )
    app.router.add_post(
        "/api/dashboard/views/{view_id}/tiles/action", api_dashboard_view_tile_action
    )
    app.router.add_post(
        "/api/dashboard/views/{view_id}/tiles", api_dashboard_view_tiles
    )
    app.router.add_get("/api/dashboard/views/{view_id}", api_dashboard_view_detail)
    app.router.add_put("/api/dashboard/views/{view_id}", api_dashboard_view_detail)
    app.router.add_delete("/api/dashboard/views/{view_id}", api_dashboard_view_detail)

    app.router.add_get("/api/mcp", handlers.api_mcp_servers)
    app.router.add_get("/api/mcp/active", handlers.api_mcp_active)
    app.router.add_post("/api/mcp/probe", handlers.api_mcp_probe)
    app.router.add_get("/api/mcp/probe", handlers.api_mcp_probe_cached)
    app.router.add_post("/api/mcp/probe/{name}", handlers.api_mcp_probe_one)
    app.router.add_get("/api/mcp/pool-stats", handlers.api_mcp_pool_stats)
    app.router.add_get("/api/mcp/importable", handlers.api_mcp_importable)
    app.router.add_post("/api/mcp/sync", handlers.api_mcp_sync)
    app.router.add_post("/api/mcp/apply", handlers.api_mcp_apply)
    app.router.add_post("/api/mcp/toggle", handlers.api_mcp_toggle)
    app.router.add_post("/api/mcp/toggle-tool", handlers.api_mcp_toggle_tool)
    app.router.add_post("/api/mcp/toggle-all", handlers.api_mcp_toggle_all)
    app.router.add_post("/api/mcp/remove", handlers.api_mcp_remove)
    app.router.add_put("/api/mcp/servers/{name}", handlers.api_mcp_server_detail)
    app.router.add_delete("/api/mcp/servers/{name}", handlers.api_mcp_server_detail)

    app.router.add_post("/api/chat", chat.api_chat)
    app.router.add_get("/api/chat/sessions", chat.api_chat_sessions)
    app.router.add_post("/api/chat/sessions", chat.api_chat_session_create)
    app.router.add_post("/api/chat/sessions/cleanup", chat.api_chat_sessions_cleanup)
    app.router.add_get("/api/chat/screen-frame", chat.api_chat_screen_state)
    app.router.add_post("/api/chat/screen-frame", chat.api_chat_screen_frame)
    app.router.add_post("/api/chat/screen-frame/pin", chat.api_chat_screen_frame_pin)
    from gideon.interfaces.dashboard import session_bulk, session_starters

    session_bulk.register_routes(app)
    session_starters.register_routes(app)
    app.router.add_get(
        "/api/chat/sessions/bound-project", chat.api_chat_session_bound_project
    )
    app.router.add_get("/api/chat/sessions/{session}", chat.api_chat_session_detail)
    app.router.add_get(
        "/api/chat/sessions/{session}/tool-result/{rid}", chat.api_chat_tool_result
    )
    app.router.add_post("/api/chat/sessions/{session}/stop", chat.api_chat_session_stop)
    app.router.add_post(
        "/api/chat/sessions/{session}/interrupt", chat.api_chat_session_interrupt
    )
    app.router.add_delete(
        "/api/chat/sessions/{session}/queue/{queue_id}",
        chat.api_chat_session_queue_cancel,
    )
    app.router.add_delete("/api/chat/sessions/{session}", chat.api_chat_session_delete)
    app.router.add_post(
        "/api/chat/sessions/{session}/agent", chat.api_chat_session_agent
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/acp-agent", chat.api_chat_session_acp_agent
    )

    app.router.add_post("/api/optimizer/optimize", handlers.handle_optimize)
    app.router.add_post(
        "/api/chat/sessions/{session}/model", chat.api_chat_session_model
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/reasoning-effort",
        chat.api_chat_session_reasoning_effort,
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir",
        chat.api_chat_session_workspace_dir,
    )
    app.router.add_get("/api/recent-projects", chat.api_recent_projects)
    app.router.add_patch(
        "/api/chat/sessions/{session}/color", chat.api_chat_session_color
    )
    app.router.add_patch(
        "/api/chat/sessions/{session}/natural-voice",
        chat.api_chat_session_natural_voice,
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/context", chat.api_chat_session_context
    )
    app.router.add_post("/api/chat/sessions/{session}/fork", chat.api_chat_session_fork)
    app.router.add_post(
        "/api/chat/sessions/{session}/fork-rewound", chat.api_chat_session_fork_rewound
    )
    app.router.add_post("/api/chat/sessions/{session}/undo", chat.api_chat_session_undo)
    app.router.add_get(
        "/api/chat/sessions/{session}/rewind", chat.api_chat_session_rewind_preview
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/rewind", chat.api_chat_session_rewind
    )
    app.router.add_post("/api/chat/sessions/{session}/side/open", chat.api_side_open)
    app.router.add_post("/api/chat/sessions/{session}/side/turn", chat.api_side_turn)
    app.router.add_post("/api/chat/sessions/{session}/side/close", chat.api_side_close)
    app.router.add_get("/api/agents/installed", handlers.api_agents_installed)
    app.router.add_get("/api/slash-commands", handlers.api_slash_commands)
    app.router.add_get("/api/agents/detail/{name}", handlers.api_agent_detail)
    app.router.add_patch("/api/agents/detail/{name}", handlers.api_agent_detail)
    app.router.add_delete("/api/agents/detail/{name}", handlers.api_agent_detail)
    app.router.add_get("/api/agents", handlers.api_gideon_agents)
    app.router.add_post("/api/agents", handlers.api_gideon_agents_create)
    app.router.add_post("/api/agents/sync", handlers.api_gideon_agents_sync)
    from gideon.interfaces.dashboard.handlers.routing import (
        api_routing_dismiss,
        api_routing_status,
        api_routing_unmute,
    )

    app.router.add_get("/api/agents/routing/status", api_routing_status)
    app.router.add_post("/api/agents/routing/dismiss", api_routing_dismiss)
    app.router.add_post("/api/agents/routing/unmute", api_routing_unmute)
    app.router.add_put("/api/agents/{name}", handlers.api_gideon_agent_update)
    app.router.add_delete("/api/agents/{name}", handlers.api_gideon_agent_delete)
    from gideon.interfaces.dashboard.handlers.agent_marketplace import (
        api_agent_marketplace_activate,
        api_agent_marketplace_create,
        api_agent_marketplace_delete,
        api_agent_marketplace_get,
        api_agent_marketplace_list,
        api_agent_marketplace_list_marketplaces,
        api_agent_marketplace_test,
        api_agent_marketplace_update,
    )

    app.router.add_get(
        "/api/agent-marketplace/marketplaces", api_agent_marketplace_list_marketplaces
    )
    app.router.add_get("/api/agent-marketplace/agents", api_agent_marketplace_list)
    app.router.add_post("/api/agent-marketplace/agents", api_agent_marketplace_create)
    app.router.add_get(
        "/api/agent-marketplace/agents/{name}", api_agent_marketplace_get
    )
    app.router.add_put(
        "/api/agent-marketplace/agents/{name}", api_agent_marketplace_update
    )
    app.router.add_delete(
        "/api/agent-marketplace/agents/{name}", api_agent_marketplace_delete
    )
    app.router.add_post(
        "/api/agent-marketplace/agents/{name}/activate", api_agent_marketplace_activate
    )
    app.router.add_post(
        "/api/agent-marketplace/agents/{name}/test", api_agent_marketplace_test
    )
    app.router.add_get("/api/agent-metadata/{name}", handlers.api_agent_metadata_get)
    app.router.add_put("/api/agent-metadata/{name}", handlers.api_agent_metadata_put)
    app.router.add_delete(
        "/api/agent-metadata/{name}", handlers.api_agent_metadata_delete
    )
    app.router.add_get("/api/sessions/{id}/agents", handlers.api_session_agents_list)
    app.router.add_get(
        "/api/sessions/{id}/agents/{agent_id}", handlers.api_session_agent_result
    )
    app.router.add_get(
        "/api/sessions/{id}/agents/{agent_id}/stream", handlers.api_session_agent_stream
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/resume", chat.api_chat_session_resume
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/approve", chat.api_chat_session_approve
    )
    app.router.add_post("/api/chat/mode", chat.api_chat_mode)
    app.router.add_post("/api/chat/task-mode", chat.api_chat_task_mode)
    app.router.add_get(
        "/api/chat/sessions/{session}/plan-session", chat.api_chat_plan_session
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/activate", chat.api_chat_plan_activate
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/edit", chat.api_chat_plan_edit
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/comment", chat.api_chat_plan_comment
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/approve", chat.api_chat_plan_approve
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/cancel", chat.api_chat_plan_cancel
    )
    app.router.add_post("/api/chat/nav/resolve-links", chat.api_nav_resolve_links)
    app.router.add_post(
        "/api/chat/sessions/{session}/generate-title",
        chat.api_chat_session_generate_title,
    )
    app.router.add_patch(
        "/api/chat/sessions/{session}/title", chat.api_chat_session_rename
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/regenerate", chat.api_chat_session_regenerate
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/switch-variant",
        chat.api_chat_session_switch_variant,
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/edit-resend", chat.api_chat_session_edit_resend
    )
    app.router.add_get("/api/chat/folders", chat.api_chat_folders)
    app.router.add_post("/api/chat/folders", chat.api_chat_folder_create)
    app.router.add_patch("/api/chat/folders/{id}", chat.api_chat_folder_update)
    app.router.add_delete("/api/chat/folders/{id}", chat.api_chat_folder_delete)
    app.router.add_patch(
        "/api/chat/sessions/{session}/folder", chat.api_chat_session_folder
    )
    app.router.add_patch("/api/chat/sessions/{session}/pin", chat.api_chat_session_pin)
    app.router.add_get("/api/chat/tags", chat.api_chat_tags)
    app.router.add_post("/api/chat/tags", chat.api_chat_tag_create)
    app.router.add_patch("/api/chat/tags/{id}", chat.api_chat_tag_update)
    app.router.add_delete("/api/chat/tags/{id}", chat.api_chat_tag_delete)
    app.router.add_put("/api/chat/sessions/{session}/tags", chat.api_chat_session_tags)
    app.router.add_post("/api/chat/sessions/{session}/drop", chat.api_chat_session_drop)
    from gideon.interfaces.dashboard.handlers import session_organize as _sess_org

    app.router.add_get(
        "/api/chat/sessions/{session}/organize", _sess_org.api_session_organize_suggest
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/organize/accept",
        _sess_org.api_session_organize_accept,
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/organize/decline",
        _sess_org.api_session_organize_decline,
    )
    from gideon.interfaces.dashboard import chat_retag

    app.router.add_post("/api/sessions/retag-all", chat_retag.api_retag_all)
    app.router.add_get("/api/sessions/retag-all", chat_retag.api_retag_status)
    app.router.add_post("/api/sessions/retag-all/cancel", chat_retag.api_retag_cancel)
    app.router.add_get("/api/chat/tag-columns", chat.api_chat_tag_columns)
    app.router.add_post("/api/chat/tag-columns", chat.api_chat_tag_column_create)
    app.router.add_put("/api/chat/tag-columns/order", chat.api_chat_tag_columns_reorder)
    app.router.add_patch("/api/chat/tag-columns/{id}", chat.api_chat_tag_column_update)
    app.router.add_delete("/api/chat/tag-columns/{id}", chat.api_chat_tag_column_delete)
    app.router.add_post("/api/voice/synthesize", chat.api_voice_synthesize)

    from gideon.interfaces.dashboard.handlers import voice_profiles as _vprof

    app.router.add_get("/api/voice/profiles", _vprof.api_voice_profiles_list)
    app.router.add_post("/api/voice/profiles", _vprof.api_voice_profile_create)
    app.router.add_get("/api/voice/bindings", _vprof.api_voice_bindings_get)
    app.router.add_put("/api/voice/bindings", _vprof.api_voice_bindings_put)
    app.router.add_delete("/api/voice/bindings", _vprof.api_voice_bindings_delete)
    app.router.add_post("/api/voice/migrate", _vprof.api_voice_migrate)
    app.router.add_get("/api/voice/resolve", _vprof.api_voice_resolve)
    app.router.add_get("/api/voice/profiles/{id}", _vprof.api_voice_profile_get)
    app.router.add_put("/api/voice/profiles/{id}", _vprof.api_voice_profile_update)
    app.router.add_delete("/api/voice/profiles/{id}", _vprof.api_voice_profile_delete)
    app.router.add_get("/api/voice/profiles/{id}/audio", _vprof.api_voice_profile_audio)
    app.router.add_post("/api/voice/profiles/{id}/lock", _vprof.api_voice_profile_lock)
    app.router.add_post(
        "/api/voice/profiles/{id}/unlock", _vprof.api_voice_profile_unlock
    )
    app.router.add_post(
        "/api/voice/profiles/{id}/consent", _vprof.api_voice_profile_consent_record
    )
    app.router.add_post(
        "/api/voice/profiles/{id}/consent/verify",
        _vprof.api_voice_profile_consent_verify,
    )
    app.router.add_delete(
        "/api/voice/profiles/{id}/consent", _vprof.api_voice_profile_consent_revoke
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/handoff", chat.api_chat_session_handoff
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/channel-link", chat.api_chat_session_channel_link
    )
    app.router.add_get("/api/channels/reply-targets", chat.api_channel_reply_targets)

    app.router.add_post("/api/reveal", handlers.api_reveal_path)
    app.router.add_get("/api/file-read", handlers.api_file_read)
    app.router.add_get("/api/file-raw", handlers.api_file_raw)
    app.router.add_get("/api/file-watch", handlers.api_file_watch)
    app.router.add_get("/api/config-fs/stream", handlers.api_config_fs_watch)
    app.router.add_post("/api/file-write", handlers.api_file_write)
    app.router.add_get("/api/file-search", handlers.api_file_search)
    app.router.add_get("/api/file-list", handlers.api_file_list)
    app.router.add_get("/api/file-git-status", handlers.api_file_git_status)
    app.router.add_get("/api/file-git-log", handlers.api_file_git_log)
    app.router.add_get("/api/file-git-commit", handlers.api_file_git_commit)
    app.router.add_get("/api/file-git-original", handlers.api_file_git_original)
    app.router.add_get("/api/file-content-search", handlers.api_file_content_search)
    app.router.add_get("/api/file-complete", handlers.api_file_complete)
    app.router.add_post("/api/file-create", handlers.api_file_create)
    app.router.add_post("/api/file-move", handlers.api_file_move)
    app.router.add_post("/api/file-delete", handlers.api_file_delete)
    app.router.add_post("/api/file-upload", handlers.api_file_upload)
    app.router.add_get("/api/browse-dirs", handlers.api_browse_dirs)
    app.router.add_post("/api/create-dir", handlers.api_create_dir)
    app.router.add_post("/api/upload", handlers.api_upload)
    app.router.add_post("/api/upload/file", handlers.api_upload_file)
    _register_upload_routes(app)
    app.router.add_get("/api/attachment-extract", handlers.api_attachment_extract)
    app.router.add_post("/api/channel/upload-file", handlers.api_channel_upload_file)
    app.router.add_post("/api/outbox/notify", handlers.api_outbox_notify)
    app.router.add_get("/api/outbox", handlers.api_outbox_list)
    app.router.add_get("/api/outbox/{filename}", handlers.api_outbox_download)
    app.router.add_post("/api/screenshot", handlers.api_screenshot)

    app.router.add_get("/api/ws/terminal/{session_id}", handlers.api_terminal_ws)
    app.router.add_post("/api/terminal/sessions", handlers.api_terminal_create)
    app.router.add_get("/api/terminal/sessions", handlers.api_terminal_list)
    app.router.add_delete(
        "/api/terminal/sessions/{session_id}", handlers.api_terminal_delete
    )
    app.router.add_get("/api/sandbox/providers", handlers.api_sandbox_providers)

    from gideon.interfaces.dashboard.handlers.channel_trust import (
        api_channel_trust,
        api_channel_trust_revoke,
    )
    from gideon.interfaces.dashboard.handlers.channels import (
        api_channel_connect,
        api_channel_disconnect,
        api_channel_get,
        api_channel_test,
        api_channels_list,
    )

    app.router.add_get("/api/channels", api_channels_list)
    app.router.add_get("/api/channels/trust", api_channel_trust)
    app.router.add_delete(
        "/api/channels/trust/{provider}/senders/{sender_id}", api_channel_trust_revoke
    )
    app.router.add_get("/api/channels/{name}", api_channel_get)
    app.router.add_post("/api/channels/{name}/connect", api_channel_connect)
    app.router.add_post("/api/channels/{name}/disconnect", api_channel_disconnect)
    app.router.add_post("/api/channels/{name}/test", api_channel_test)

    from gideon.interfaces.dashboard.handlers.tools import (
        api_providers_toggle,
        api_tool_groups,
        api_tool_invoke,
        api_tools_list,
        api_tools_savings,
        api_tools_toggle,
    )

    app.router.add_get("/api/tools", api_tools_list)
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    app.router.add_post("/api/tools/toggle", api_tools_toggle)
    app.router.add_post("/api/tools/provider-toggle", api_providers_toggle)
    app.router.add_get("/api/tools/savings", api_tools_savings)
    app.router.add_get("/api/tools/groups", api_tool_groups)

    from gideon.interfaces.dashboard.handlers.computer_use import (
        api_computer_use_dispatch,
        api_computer_use_live_view,
    )

    app.router.add_post("/api/computer-use/dispatch", api_computer_use_dispatch)
    app.router.add_get("/api/computer-use/live-view", api_computer_use_live_view)

    from gideon.interfaces.dashboard.handlers.manifest import api_manifest

    app.router.add_get("/api/manifest", api_manifest)

    from gideon.interfaces.dashboard.handlers.legibility import (
        api_always_on,
        api_always_on_doc,
        api_always_on_doc_write,
        api_discover,
        api_discover_dismiss,
    )

    app.router.add_get("/api/legibility/discover", api_discover)
    app.router.add_post("/api/legibility/discover/dismiss", api_discover_dismiss)
    app.router.add_get("/api/legibility/always-on", api_always_on)
    app.router.add_get("/api/legibility/always-on/doc", api_always_on_doc)
    app.router.add_put("/api/legibility/always-on/doc", api_always_on_doc_write)

    # Legibility — Gideon as a routed-context provider for external agents (§7).
    from gideon.interfaces.dashboard.handlers.context import (
        api_context_get,
        api_project_context_regenerate,
    )

    app.router.add_get("/api/context", api_context_get)
    app.router.add_post(
        "/api/projects/{project_id}/context-adapters/regenerate",
        api_project_context_regenerate,
    )

    from gideon.engine.tasks.handlers import register_task_routes

    register_task_routes(app)

    from gideon.automation.workflows.handlers import register_workflow_routes

    register_workflow_routes(app)

    from gideon.interfaces.dashboard.handlers.loop_routes import (
        register_unified_loop_routes,
    )

    register_unified_loop_routes(app)

    from gideon.workspace.artifacts.handlers import register_artifact_routes

    register_artifact_routes(app)

    app.router.add_get("/api/inbox", handlers_inbox.api_inbox_list)
    app.router.add_get("/api/inbox/open", handlers_inbox.api_inbox_open_items)
    app.router.add_get("/api/inbox/kinds", handlers_inbox.api_inbox_kinds)
    app.router.add_post("/api/inbox/seen", handlers_inbox.api_inbox_seen)
    app.router.add_get("/api/inbox/status", handlers_inbox.api_inbox_status)
    app.router.add_post("/api/inbox/restart", handlers_inbox.api_inbox_restart)
    app.router.add_post("/api/inbox/dismiss-all", handlers_inbox.api_inbox_dismiss_all)
    app.router.add_post(
        "/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create
    )
    app.router.add_post("/api/inbox/notes", handlers_inbox.api_inbox_note_create)
    app.router.add_post(
        "/api/inbox/{id}/apply", handlers_inbox.api_inbox_proposal_apply
    )
    app.router.add_post("/api/inbox/{id}/restore", handlers_inbox.api_inbox_restore)
    app.router.add_post("/api/inbox/send", handlers_inbox.api_inbox_send)
    app.router.add_put("/api/inbox/{id}", handlers_inbox.api_inbox_update)
    app.router.add_post("/api/inbox/{id}/draft", handlers_inbox.api_inbox_draft)
    app.router.add_post("/api/inbox/{id}/open", handlers_inbox.api_inbox_open)
    app.router.add_post("/api/inbox/{id}/favorite", handlers_inbox.api_inbox_favorite)
    app.router.add_get("/api/inbox/digest", handlers_inbox.api_inbox_digest)
    app.router.add_get("/api/inbox/providers", handlers_inbox.api_inbox_providers)

    app.router.add_delete("/api/notifications", handlers.api_notification_delete)
    app.router.add_post("/api/notifications/ack", handlers.api_notification_ack)
    app.router.add_post("/api/notifications/unack", handlers.api_notification_unack)
    app.router.add_post(
        "/api/notifications/ack-all", handlers.api_notifications_ack_all
    )
    app.router.add_get("/api/update/check", handlers.api_update_check)
    app.router.add_get("/api/changelog", handlers.api_changelog)
    app.router.add_post("/api/update", handlers.api_update_apply)
    app.router.add_post("/api/update/auto", handlers.api_update_auto)
    app.router.add_post("/api/update/dev-mode", handlers.api_update_dev_mode)
    app.router.add_post("/api/update/cancel", handlers.api_update_cancel)
    app.router.add_post("/api/system/restart", handlers.api_restart)
    _truthy = {"1", "true", "yes", "on"}
    if (
        os.environ.get("GIDEON_HOME", "").endswith("-dev")
        or os.environ.get("GIDEON_DEV_MODE", "").lower() in _truthy
    ):
        app.router.add_post("/api/update/simulate", handlers.api_update_simulate)
    app.router.add_get("/api/sessions", handlers.api_sessions)
    app.router.add_delete("/api/sessions", handlers.api_sessions_clear)
    app.router.add_get("/api/sessions/context", handlers.api_sessions_context)
    app.router.add_get("/api/sessions/health", handlers.api_sessions_health)
    app.router.add_post("/api/sessions/restart", handlers.api_sessions_restart)
    app.router.add_get("/api/sessions/search", handlers.api_sessions_search)
    app.router.add_get("/api/sessions/{key}", handlers.api_session_detail)
    app.router.add_delete("/api/sessions/{key}", handlers.api_session_delete)
    app.router.add_get("/api/logs", handlers.api_logs)
    app.router.add_get("/api/logs/level", handlers.api_log_level_get)
    app.router.add_post("/api/logs/level", handlers.api_log_level)
    app.router.add_post("/api/sel/rotate", handlers.api_sel_rotate)
    app.router.add_get("/api/security/stats", handlers.api_security_stats)
    app.router.add_get(
        "/api/security/denied-commands", handlers.api_security_denied_commands
    )
    app.router.add_get("/api/security/egress", handlers.api_security_egress)
    from gideon.interfaces.dashboard.handlers.security_audit import (
        register_security_audit_routes,
    )

    register_security_audit_routes(app)
    from gideon.interfaces.dashboard.handlers.security_credentials import (
        register_security_credential_routes,
    )

    register_security_credential_routes(app)
    from gideon.interfaces.dashboard.handlers.secrets import register_secrets_routes

    register_secrets_routes(app)
    app.router.add_get("/api/approvals", handlers.api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", handlers.api_approval_resolve)

    app.router.add_get("/api/token/local", handlers.api_token_local)

    app.router.add_post("/api/logout", handlers.api_logout)

    app.router.add_post("/api/hooks/agent", handlers.api_hooks_agent)

    from gideon.extensions.providers.entity_routes import register_entity_routes
    from gideon.extensions.providers.instance_routes import register_instance_routes
    from gideon.extensions.providers.loader import load_all_extensions
    from gideon.extensions.providers.routes import (
        register_routes as register_extension_routes,
    )

    load_all_extensions()
    from gideon.extensions.providers.use_cases import migrate_legacy_bindings
    from gideon.integrations.llm.registry import sync_entries_from_config

    try:
        migrate_legacy_bindings()
    except Exception:
        pass
    sync_entries_from_config()
    register_extension_routes(app)
    register_instance_routes(app)
    register_entity_routes(app)

    setup_knowledge_routes(app)
    setup_research_report_routes(app)

    async def _transports_startup(app_: web.Application) -> None:
        """Register the always-present in-app Web UI transport at boot.

        Extension-backed transports (Slack, and future Telegram/Discord) are
        registered by the provider registry's ChannelTypeHandler when their
        extension is enabled — one source of truth, no parallel startup path.
        """
        from gideon.integrations.channel_transports import register_default_transports

        try:
            register_default_transports()
        except Exception:
            logger.exception("Failed to register the Web UI channel transport")

    app.on_startup.append(_transports_startup)

    async def _control_bridge_startup(app_: web.Application) -> None:
        """Bind the loopback control bridge on its own random port (EXTERNAL-ACCESS §4).

        Its OWN runner, not a route here: the dashboard's port is knowable and a control
        surface on a knowable port is a port-scan away from being probed. A mount refusal
        is normal (the surface is off by default) and must never block gateway startup —
        so this swallows, logs, and leaves no discovery file behind.
        """
        from gideon.integrations.inbound import bridge as _bridge

        try:
            await _bridge.start(app_["state"])
        except Exception:
            logger.warning("control bridge failed to start", exc_info=True)
            try:
                _bridge.remove_discovery()
            except Exception:
                pass

    app.on_startup.append(_control_bridge_startup)

    async def _control_bridge_shutdown(app_: web.Application) -> None:
        """Tear the bridge down and DELETE its discovery file: a file naming a dead
        port is worse than no file, because a client trusts it and hangs."""
        from gideon.integrations.inbound import bridge as _bridge

        try:
            await _bridge.stop()
        except Exception:
            logger.debug("control bridge shutdown failed", exc_info=True)

    app.on_cleanup.append(_control_bridge_shutdown)

    async def _mcp_migrate_startup(app_: web.Application) -> None:
        """UT3: fold any legacy ``settings/mcp.json`` content into the canonical
        ``~/.gideon/mcp.json`` once, so the dual store can't re-diverge."""
        from gideon.interfaces.dashboard.handlers.mcp import _migrate_legacy_mcp_json

        try:
            _migrate_legacy_mcp_json()
        except Exception:
            logger.exception("Failed to migrate legacy mcp.json")

    app.on_startup.append(_mcp_migrate_startup)

    async def _action_providers_startup(app_: web.Application) -> None:
        """Register the bundled action providers (bash, webhook, run-script, …)."""
        from gideon.integrations.action_providers.registry import (
            _ensure_default_providers_registered,
        )

        try:
            _ensure_default_providers_registered()
        except Exception:
            logger.exception("Failed to register action providers")

    app.on_startup.append(_action_providers_startup)

    async def _prompt_providers_startup(app_: web.Application) -> None:
        """Register the bundled native filesystem prompt provider."""
        from gideon.integrations.prompt_providers.registry import (
            _ensure_default_providers_registered,
        )

        try:
            _ensure_default_providers_registered()
        except Exception:
            logger.exception("Failed to register prompt providers")

    app.on_startup.append(_prompt_providers_startup)

    async def _projection_rules_startup(app_: web.Application) -> None:
        """Install the user's tool-output projection rules (TokenJuice OP6) into the
        projection engine so a large output of a user-taught type keeps its salient
        slice instead of a blunt cut. Fail-soft — a bad rule is skipped, never fatal."""
        try:
            from gideon.core.config.loader import AppConfig
            from gideon.integrations.tool_providers import projection

            projection.set_user_rules(
                [
                    projection.ProjectionRule(
                        name=r.name,
                        match_regex=r.match_regex,
                        strategy=r.strategy,
                        head=r.head,
                        tail=r.tail,
                        keep=r.keep,
                        skip=r.skip,
                        count=r.count,
                    )
                    for r in AppConfig.load().tools.projection_rules
                ]
            )
        except Exception:
            logger.exception("Failed to install tool-output projection rules")

    app.on_startup.append(_projection_rules_startup)

    async def _skill_catalogs_startup(app_: web.Application) -> None:
        """Register the operator's configured skill catalogs (``packs.skill_catalogs``,
        AP-6) on the shared skills registry so the Skills store can browse them.

        Each catalog registers at COMMUNITY tier and installs through the same
        ``install_guarded`` chokepoint as every other marketplace. Fail-soft per
        catalog inside ``register_skill_catalogs``; a total failure is logged, never
        fatal — an unreachable catalog must not cost the bundled marketplaces."""
        try:
            from gideon.extensions.packs.catalog_marketplace import (
                register_skill_catalogs,
            )

            names = register_skill_catalogs()
            if names:
                logger.info(
                    "Registered %d skill catalog(s): %s", len(names), ", ".join(names)
                )
        except Exception:
            logger.exception("Failed to register configured skill catalogs")

    app.on_startup.append(_skill_catalogs_startup)

    async def _app_sources_seed_startup(app_: web.Application) -> None:
        """Seed the shipped app-registry git source into ``app-sources.json`` — once, ever
        (ECOSYSTEM-TOOLING T2.2).

        This is the "first run" site: the seed writes one removable row and a marker, so
        removing the source in the Store persists across every later start. Gated by
        ``apps.registry_source_enabled``. Store LISTING only — it adds no install path, and
        installing from it still goes through the scanner gate. Fail-soft: a sources-file
        problem must never cost the gateway its boot."""
        try:
            from gideon.extensions.apps.catalog import seed_default_git_sources

            seeded = await asyncio.to_thread(seed_default_git_sources)
            if seeded:
                logger.info("Seeded default app source(s): %s", ", ".join(seeded))
        except Exception:
            logger.exception("Failed to seed default app sources")

    app.on_startup.append(_app_sources_seed_startup)

    async def _model_providers_startup(app_: web.Application) -> None:
        """Register config model-managers as local providers; retry the legacy migration.

        config.json ``providers[]`` are NOT replayed here. That happens exactly once, in
        the synchronous body above, because ``setup_knowledge_routes`` builds the
        knowledge embedder during app construction — before any on_startup hook — and
        would otherwise see an empty registry. A second replay used to sit here and was
        measured returning 0 entries on every boot: the body call is unguarded, so it has
        either registered everything already or taken the boot down with it, leaving this
        one nothing to do. ``migrate_legacy_bindings`` DOES belong here as a retry: it
        unlinks the legacy file only on success, so a partial failure of the (silently
        swallowed) body call leaves real work, and this copy logs it.
        """
        from gideon.extensions.providers.use_cases import migrate_legacy_bindings

        try:
            migrate_legacy_bindings()
        except Exception:
            logger.exception("Failed to migrate legacy use-case bindings")
        try:
            from gideon.integrations.local_models.registry import (
                register_config_model_managers,
            )

            register_config_model_managers()
        except Exception:
            logger.exception(
                "Failed to register config model-managers as local providers"
            )

    app.on_startup.append(_model_providers_startup)

    async def _resume_interrupted_reindex_startup(app_: web.Application) -> None:
        """Auto-resume an INTERRUPTED or model-swap-orphaned embedding re-index.

        Switching the embedding model nulls the old (incompatible) vectors, then
        re-embeds every item. If the gateway died mid-re-index (crash/kill/OOM), items
        are left with text but no embedding OR — if it died after a model SWAP but
        before re-embed — with an old WRONG-DIMENSION vector. Either way the store sits
        silently unsearchable against the active model (retrieval skips dim mismatches)
        with no recovery. On boot, once the active embedding model is resolvable, detect
        BOTH states (missing OR stale-dim vectors) and finish the re-index automatically.
        Runs AFTER _model_providers_startup so the embedder is wired; fully best-effort —
        never blocks or crashes startup."""
        try:
            state = app_["state"]
            ks = getattr(state, "knowledge_store", None)
            if ks is None:
                return
            from gideon.interfaces.dashboard.handlers.embedding_reindex import (
                _resolve_embed,
            )

            embedder, embed_fn, model = _resolve_embed(app_)
            _dim = getattr(embedder, "dim", None) if embedder is not None else None
            active_dim = _dim() if callable(_dim) else None
            needing = ks.count_items_needing_reembed(active_dim)
            if needing <= 0:
                return
            if embed_fn is None:
                logger.warning(
                    "Embedding re-index needed: %d knowledge item(s) missing/stale "
                    "vectors, but the active embedding model (%s) isn't ready — the "
                    "store stays keyword-searchable; re-run once the model is available.",
                    needing,
                    model or "none",
                )
                return
            from gideon.interfaces.dashboard.handlers.memory import _get_provider

            vector_store = _get_provider(state)
            job, error = state.embedding_reindex().start(
                model=model,
                knowledge_store=ks,
                vector_store=vector_store,
                embedder=embedder,
                embed_fn=embed_fn,
            )
            if error:
                logger.warning("Auto-resume re-index refused: %s", error)
            else:
                logger.info(
                    "Auto-resuming embedding re-index (%d item(s) missing/stale "
                    "vectors) with model %s [job %s]",
                    needing,
                    model,
                    getattr(job, "id", "?"),
                )
        except Exception:
            logger.exception("Failed to check/resume interrupted embedding re-index")

    app.on_startup.append(_resume_interrupted_reindex_startup)

    from gideon.interfaces.dashboard.embedding_reindex import (
        register_chunk_backfill_pass,
    )

    register_chunk_backfill_pass()

    async def _warm_acp_pool_startup(app_: web.Application) -> None:
        """Start the ACP live-connection pool: one warmed connection per ready
        runtime, serving BOTH the discovery snapshot (instant lists) AND the first
        chat turn (instant first turn — claimed in get_or_create). Warming runs in
        the BACKGROUND (each is a ~15-20s live session); the pool also starts a
        health loop that respawns dead connections. Runs after the boot-time
        config replay in the body above, so the acp_agent entries are registered.
        Best-effort — failures never affect the gateway."""
        try:
            import asyncio as _asyncio

            from gideon.integrations.acp.connection_pool import init_acp_pool
            from gideon.interfaces.dashboard.handlers.providers import (
                warm_readiness_cache,
            )

            st = app_.get("state")
            start_sem = getattr(getattr(st, "sessions", None), "_start_sem", None)
            if start_sem is None:
                start_sem = _asyncio.Semaphore(4)
            await init_acp_pool(start_sem)

            async def _warm_readiness() -> None:
                try:
                    await warm_readiness_cache()
                except Exception:
                    logger.debug("ACP readiness warm failed", exc_info=True)

            _asyncio.ensure_future(_warm_readiness())
        except Exception:
            logger.debug("ACP pool startup failed", exc_info=True)

    app.on_startup.append(_warm_acp_pool_startup)

    async def _acp_pool_shutdown(app_: web.Application) -> None:
        """Drain + shut down all pooled ACP connections on gateway stop."""
        try:
            from gideon.integrations.acp.connection_pool import (
                get_acp_pool,
                set_acp_pool,
            )

            pool = get_acp_pool()
            if pool is not None:
                await pool.shutdown()
                set_acp_pool(None)
        except Exception:
            logger.debug("ACP pool shutdown failed", exc_info=True)

    app.on_cleanup.append(_acp_pool_shutdown)

    async def _mcp_client_shutdown(app_: web.Application) -> None:
        """Stop the idle sweeper + drain all live MCP connections on gateway stop
        (rel-mcp-server-pooling #46)."""
        try:
            from gideon.integrations.mcp_client import get_mcp_client_registry

            reg = get_mcp_client_registry()
            if reg is not None:
                await reg.shutdown_all()
        except Exception:
            logger.debug("MCP client shutdown failed", exc_info=True)

    app.on_cleanup.append(_mcp_client_shutdown)

    async def _app_backends_shutdown(app_: web.Application) -> None:
        """Terminate every app-backend subprocess on gateway stop. Without this the
        backends (snippet-lab/standup-notes/… server.py) were spawned on enable but
        never reaped on shutdown — so each gateway restart ORPHANED another set
        (reparented to init), leaking dozens of processes over a dev session."""
        try:
            from gideon.extensions.apps.backend_runtime import get_backend_supervisor

            get_backend_supervisor().stop_all()
        except Exception:
            logger.debug("app-backend shutdown failed", exc_info=True)

    app.on_cleanup.append(_app_backends_shutdown)

    async def _discovery_shutdown(app_: web.Application) -> None:
        """Send the mDNS goodbye and release the socket on gateway stop (COMPANION-APPS C3).

        Without it, a restart leaves other devices caching this gateway's address for two
        minutes pointing at a port nothing is listening on. Registered HERE rather than beside
        the advertiser's start, because ``runner.setup()`` freezes ``on_cleanup`` before the
        bind host — and therefore the start decision — is known. A no-op when nothing is
        advertising, which is the default."""
        try:
            from gideon.integrations.companion import discovery

            discovery.shutdown()
        except Exception:
            logger.debug("LAN discovery shutdown failed", exc_info=True)

    app.on_cleanup.append(_discovery_shutdown)

    if _DIST_DIR.is_dir():
        app.router.add_static(
            "/assets",
            _DIST_DIR / "assets" if (_DIST_DIR / "assets").is_dir() else _DIST_DIR,
            show_index=False,
            append_version=True,
        )
        if (_DIST_DIR / "sprites").is_dir():
            app.router.add_static("/sprites", _DIST_DIR / "sprites", show_index=False)
        if (_DIST_DIR / "fonts").is_dir():
            app.router.add_static("/fonts", _DIST_DIR / "fonts", show_index=False)
        if (_DIST_DIR / "icons").is_dir():
            app.router.add_static(
                "/icons",
                _DIST_DIR / "icons",
                show_index=False,
                append_version=False,
            )
        if (_DIST_DIR / "vendor").is_dir():
            app.router.add_static(
                "/vendor",
                _DIST_DIR / "vendor",
                show_index=False,
                append_version=False,
            )
        logger.info("Serving React build from %s", _DIST_DIR)

    @web.middleware  # type: ignore[misc]
    async def no_cache_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        resp = await handler(request)  # type: ignore[operator]
        if hasattr(resp, "headers"):
            resp.headers.setdefault(
                "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"
            )
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
            _apply_response_policies(request, resp)
        return resp  # type: ignore[return-value]

    _safe_methods = {"GET", "HEAD", "OPTIONS"}

    _sel_log_methods = {"POST", "PUT", "DELETE", "PATCH"}

    @web.middleware  # type: ignore[misc]
    async def sel_audit_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method in _sel_log_methods and request.path.startswith("/api/"):
            from gideon.security.sel import sel

            try:
                resp = await handler(request)  # type: ignore[operator]
                sel().log_api_access(
                    caller="dashboard_user",
                    operation=f"{request.method} {request.path}",
                    outcome="ok" if resp.status < 400 else "error",
                    resources=request.path,
                )
                return resp  # type: ignore[return-value]
            except Exception as exc:
                sel().log_api_access(
                    caller="dashboard_user",
                    operation=f"{request.method} {request.path}",
                    outcome="error",
                    resources=request.path,
                    error=str(exc)[:200],
                )
                raise
        return await handler(request)  # type: ignore[operator]

    app["allowed_origins"] = build_allowed_origins(port, local_only, configured_host)

    @web.middleware  # type: ignore[misc]
    async def csrf_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method not in _safe_methods:
            if not check_origin(request, require=True, fallback_header="Referer"):
                from gideon.http_errors import json_error

                return json_error("auth_origin_not_allowed", status=403)
        return await handler(request)  # type: ignore[operator]

    @web.middleware  # type: ignore[misc]
    async def app_permission_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        """Enforce an app's declared ``permissions.api`` allowlist (A5).

        Only acts on requests carrying an app identity (``request["app"]`` set
        from an app-scoped token). A path the app didn't declare is rejected
        403 before the handler runs — the server-side, bypass-proof half of the
        permission boundary for an app-scoped client or backend. It does not
        sandbox an app's frontend bundle, which executes in the host origin.
        Owner/dashboard requests (no app identity) pass.

        The decision itself is ``permissions.app_request_denial``, not inline here:
        this closure cannot be imported, so every test of the boundary had to
        re-implement it and was free to drift from it. This half owns logging the
        refusal and shaping the response; the module owns what is refused."""
        from gideon.extensions.apps.permissions import (
            APP_SCOPED_PREFIXES,
            app_request_denial,
        )

        app_name = request.get("app", "")
        if app_name and request.path.startswith(APP_SCOPED_PREFIXES):

            def _deny(reason: str) -> web.StreamResponse:
                from gideon.security.sel import sel

                try:
                    sel().log_api_access(
                        caller=f"app:{app_name}",
                        operation=f"{request.method} {request.path}",
                        outcome="denied",
                        source="app_permissions",
                        resources=request.path,
                        error=reason,
                    )
                except Exception:
                    pass
                raise web.HTTPForbidden(
                    text=f"app {app_name!r} not permitted to access {request.path}",
                    content_type="text/plain",
                )

            reason = app_request_denial(app_name, request.path)
            if reason:
                return _deny(reason)
        return await handler(request)  # type: ignore[operator]

    _secret_path = config_dir() / ".local_secret"
    _secret_path.parent.mkdir(parents=True, exist_ok=True)
    _internal_secret = os.urandom(16).hex()
    app["local_secret"] = _internal_secret

    from gideon.security.auth.modes import AuthMode as _AuthMode

    _no_auth = app["auth_cfg"].mode == _AuthMode.NONE
    if _no_auth:
        logger.warning("GIDEON_AUTH_MODE=none — token auth DISABLED (loopback only)")

    @web.middleware
    async def _dev_user_middleware(
        request: web.Request, handler: object
    ) -> web.StreamResponse:
        from gideon.interfaces.dashboard.token_auth import (
            attach_validated_paired_session,
        )

        if not attach_validated_paired_session(request, port=port):
            request["user"] = request.get("user") or "dev-local"
        if not request.get("app"):
            from gideon.interfaces.dashboard.token_auth import validate_token_with_app

            app_token = ""
            _auth = request.headers.get("Authorization", "")
            if _auth.startswith("Bearer "):
                app_token = _auth[7:].strip()
            if not app_token:
                app_token = request.query.get("app_token", "")
            if app_token:
                a_valid, _a_user, _reason, a_app = validate_token_with_app(app_token)
                if a_valid and a_app:
                    request["app"] = a_app
        return await handler(request)  # type: ignore[operator]

    from gideon.interfaces.dashboard.api_version_gate import api_version_middleware
    from gideon.interfaces.dashboard.invalid_id_gate import invalid_id_middleware
    from gideon.interfaces.dashboard.request_boundary import request_boundary_middleware

    app.middlewares[:] = [
        no_cache_middleware,
        api_version_middleware(),
        *(
            [_dev_user_middleware]
            if _no_auth
            else [
                csrf_middleware,
                token_auth_middleware(
                    internal_paths=frozenset(
                        {
                            "/api/send-message",
                            "/api/session-keepalive",
                            "/api/session-tool-policy",
                            "/api/hooks/agent",
                            "/api/outbox/notify",
                            "/api/channel/upload-file",
                            "/api/mcp/servers",
                            "/api/tools/invoke",
                            "/api/computer-use/dispatch",
                        }
                    ),
                    mixed_internal_paths=frozenset(
                        {
                            "/api/spawn",
                            "/api/lessons",
                            "/api/triggers",
                        }
                    ),
                    internal_secret=_internal_secret,
                    port=port,
                    local_only=local_only,
                ),
            ]
        ),
        app_permission_middleware,
        sel_audit_middleware,
        request_boundary_middleware(),
        invalid_id_middleware(),
        spa_fallback,
    ]

    if dashboard_url:
        _has_token_auth = any(
            getattr(mw, "_is_token_auth", False) for mw in app.middlewares
        )
        if _has_token_auth:
            app["allowed_origins"] = build_allowed_origins(
                port, local_only, configured_host, dashboard_url
            )
            logger.info(
                "dashboard_url=%s: added to CSRF allowed origins (token auth verified)",
                dashboard_url,
            )
        else:
            logger.error(
                "dashboard_url=%s requires token auth — refusing to start without it. "
                "Connect a channel or remove dashboard.url from config.",
                dashboard_url,
            )
            raise RuntimeError("dashboard_url requires token auth middleware")

    runner = web.AppRunner(app)
    await runner.setup()
    _bind_host = resolve_bind_host()
    if _bind_host == "127.0.0.1" and not local_only:
        _bind_host = "0.0.0.0"
    if _no_auth:
        _bind_host = "127.0.0.1"
    site = web.TCPSite(runner, _bind_host, port)
    await _start_site(site, port)

    try:
        _write_secret_file(_secret_path, _internal_secret)
    except OSError:
        await runner.cleanup()
        raise

    try:
        from gideon.integrations.companion import discovery as _discovery

        _discovery.set_gateway_bind(_bind_host, port)
        _discovery.reconcile()
    except Exception:
        logger.warning("LAN discovery failed to start", exc_info=True)

    asyncio.create_task(handlers._bg_mcp_probe())

    try:
        from gideon.integrations.mcp_client import get_mcp_client_registry

        _mcp_reg = get_mcp_client_registry()
        if _mcp_reg is not None:
            _mcp_reg.start_sweeper()
    except Exception:
        logger.debug("MCP idle sweeper start skipped", exc_info=True)

    _reaper = asyncio.create_task(handlers.reap_orphaned_terminals(app))
    _reaper.add_done_callback(lambda t: t.result() if not t.cancelled() else None)
    state._terminal_reaper = _reaper

    state._sel_prune_task = asyncio.create_task(_sel_prune_loop())

    state._upload_sweep_task = asyncio.create_task(_upload_sweep_loop())

    try:
        from gideon.operations.durability.service import DurabilityService

        state._durability_svc = DurabilityService(notifier=state.notify)
        await state._durability_svc.start()
    except Exception:
        logger.warning("Durability service failed to start", exc_info=True)

    try:
        from gideon.cognition.knowledge.source_engine import SourceEngine
        from gideon.integrations.inbox import emit_shared_knowledge_item
        from gideon.integrations.knowledge_providers.dir_source import DirSourceProvider
        from gideon.integrations.knowledge_providers.feed_source import (
            FeedSourceProvider,
        )
        from gideon.integrations.knowledge_providers.registry import (
            configure_shared_item_push,
            register_provider,
        )
        from gideon.integrations.knowledge_providers.web_source import WebSourceProvider

        register_provider(DirSourceProvider(state.knowledge_store))
        register_provider(FeedSourceProvider(state.knowledge_store))
        register_provider(WebSourceProvider(state.knowledge_store))
        configure_shared_item_push(
            lambda provider, item: emit_shared_knowledge_item(state, provider, item)
        )
        state._source_engine = SourceEngine(
            state.knowledge_store,
            state.knowledge_ingest_queue(),
        )
        state._source_engine.start()
    except Exception:
        logger.warning("Source engine failed to start", exc_info=True)

    try:
        from gideon.cognition.knowledge import artifact_ingest

        state._artifact_indexer = artifact_ingest.start(
            state.knowledge_store,
            enqueue=state.knowledge_ingest_queue().enqueue,
        )
    except Exception:
        logger.warning("Artifact knowledge mirror failed to start", exc_info=True)

    state.start_flush_loop()

    from gideon.core.config.loader import AppConfig

    cfg = AppConfig.load()
    _apply_startup_yolo(state, cfg)
    restored = chat.restore_recent_sessions(
        state,
        cfg.dashboard.restore_window_minutes if cfg.dashboard.restore_sessions else 0,
        folders_only=not cfg.dashboard.restore_sessions,
    )
    if restored:
        logger.info("Restored %d session(s)", restored)

    return runner, state


async def start_api_server(
    sessions: "ConversationDirectory",
    port: int = _DEFAULT_PORT,
    subagents: "DelegationSupervisor | None" = None,
    owner_id: str = "",
) -> tuple[web.AppRunner, ConsoleState]:
    """Start a minimal API-only server for MCP tool transport (no UI)."""
    state = ConsoleState(
        sessions=sessions,
        start_time=time.time(),
        subagents=subagents,
        owner_id=owner_id,
    )
    state._hook_store = ScriptHookStore()
    set_global_hook_store(state._hook_store)

    from gideon.integrations.inbox_providers.native_source import (
        set_dashboard_state as _set_inbox_state,
    )

    _set_inbox_state(state)

    if state.subagents is not None:
        state.subagents.hook_store = state._hook_store

    state.wire_session_compact_callback()

    app = web.Application(client_max_size=_single_post_ceiling())
    app["state"] = state
    app["gateway_id"] = _new_gateway_id()
    state.load_folders()
    state.load_tags()
    app["port"] = port
    from gideon.security.auth.modes import AuthConfig as _AuthConfig

    app["auth_cfg"] = _AuthConfig.from_env()
    _log_auth_mode(app["auth_cfg"])

    _precompute_telemetry(state)

    _sel_methods = {"GET", "POST", "PUT", "DELETE"}

    @web.middleware  # type: ignore[misc]
    async def sel_audit_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method in _sel_methods and request.path.startswith("/api/"):
            from gideon.security.sel import sel

            try:
                resp = await handler(request)  # type: ignore[operator]
                sel().log_api_access(
                    caller="mcp_tool",
                    operation=f"{request.method} {request.path}",
                    outcome="ok" if resp.status < 400 else "error",
                    resources=request.path,
                )
                return resp  # type: ignore[return-value]
            except Exception as exc:
                sel().log_api_access(
                    caller="mcp_tool",
                    operation=f"{request.method} {request.path}",
                    outcome="error",
                    resources=request.path,
                    error=str(exc)[:200],
                )
                raise
        return await handler(request)  # type: ignore[operator]

    app.middlewares.append(sel_audit_middleware)

    _register_mcp_routes(app)
    app.router.add_get("/api/healthz", handlers.api_healthz)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await _start_site(site, port)
    logger.info("API-only server listening on 127.0.0.1:%d", port)

    return runner, state
