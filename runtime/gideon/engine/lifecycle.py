"""Ordered runtime startup, process supervision and resource retirement."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from gideon import shutdown_event
from gideon.core.config.loader import AppConfig, config_dir
from gideon.core.constants import DATA_WARNING
from gideon.core.env import browser_available
from gideon.engine import gateway_base
from gideon.interfaces.dashboard.origin import (
    build_dashboard_url,
    format_dashboard_urls,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from gideon.interfaces.dashboard.token_auth import (
    DEFAULT_BROWSER_SESSION_TTL_SECS,
    generate_token,
)

if TYPE_CHECKING:
    from gideon.engine.gateway import RuntimeCoordinator

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartupStage:
    name: str
    action: Callable[[], Any]
    optional: bool = False


async def advance(stages: Iterable[StartupStage]) -> tuple[str, ...]:
    completed = []
    for stage in stages:
        try:
            result = stage.action()
            if inspect.isawaitable(result):
                await result
        except Exception:
            if not stage.optional:
                raise
            log.exception("Optional startup stage %s failed", stage.name)
        else:
            completed.append(stage.name)
    return tuple(completed)


async def cancel_tasks(tasks: Iterable[asyncio.Task | None]) -> None:
    current = asyncio.current_task()
    selected = tuple(
        {task for task in tasks if task is not None and task is not current}
    )
    for task in selected:
        task.cancel()
    if selected:
        await asyncio.gather(*selected, return_exceptions=True)


async def settle(operations: Iterable[Awaitable[Any]]) -> None:
    results = await asyncio.gather(*operations, return_exceptions=True)
    for outcome in results:
        if isinstance(outcome, Exception):
            log.warning("Resource cleanup failed: %s", outcome)


async def bind_surface(runtime: RuntimeCoordinator, *, api_only: bool) -> None:
    from gideon.engine import gateway
    from gideon.interfaces.dashboard import start_api_server

    if runtime.sessions is None:
        raise RuntimeError(
            "Conversation services must be initialized before HTTP binding"
        )
    host, port = parse_dashboard_url(runtime.config.dashboard.url)
    override = runtime._port_override
    if override is not None:
        port = 0 if override == "auto" else int(override)
    runtime._configured_host = host
    runtime._local_only = is_local_bind(resolve_bind_host())
    options = dict(
        sessions=runtime.sessions,
        port=port,
        subagents=runtime.subagent_mgr,
        owner_id=runtime.owner_id,
    )
    if api_only:
        serving = start_api_server
    else:
        serving = gateway.start_dashboard
        options.update(
            context_builder=runtime.ctx_builder,
            conversation_log=runtime.conv_log,
            consolidator=runtime.consolidator,
            local_only=runtime._local_only,
            configured_host=host,
            dashboard_url=runtime.config.dashboard.url,
        )
    runner, surface = await serving(**options)
    runtime._dashboard_runner = runner
    runtime.dashboard_state = surface
    addresses = runner.addresses if runner is not None else ()
    runtime._dashboard_port = addresses[0][1] if port == 0 and addresses else port
    runtime._publish_runtime_base()
    if surface is not None:
        surface.no_crons = runtime._no_crons
        if not api_only:
            surface._inbox_svc = runtime.inbox_svc
            surface._inbox_restart = runtime._restart_inbox


def reap_backends() -> None:
    try:
        from gideon.extensions.apps.backend_runtime import get_backend_supervisor

        get_backend_supervisor().stop_all()
    except Exception:
        log.debug("Application processes could not all be reaped", exc_info=True)


async def retire(runtime: RuntimeCoordinator) -> None:
    from gideon.integrations.channel_transports import get_transport, list_transports

    gateway_base.unpublish()
    reap_backends()
    surface = runtime.dashboard_state
    if surface is not None:
        from gideon.interfaces.dashboard.chat import save_all_sessions_to_history

        save_all_sessions_to_history(surface)
        surface.file_indexes.stop_all()

    await cancel_tasks(runtime._handler_tasks)
    for watcher in (runtime.loop_watchdog, runtime.workflow_watchdog):
        if watcher is not None:
            await watcher.stop()
    await cancel_tasks(
        (
            runtime._file_watch_task,
            runtime._web_watch_task,
            runtime._clock_task,
            runtime._reaper_task,
        )
    )
    for service in (runtime.heartbeat_svc, runtime.inbox_svc):
        if service is not None:
            service.stop()

    closing = []
    for owner, method in (
        (runtime.subagent_mgr, "cancel_all"),
        (runtime.sessions, "close_all"),
    ):
        if owner is not None:
            closing.append(getattr(owner, method)())
    if runtime._dashboard_runner is not None:
        if surface is not None:
            await surface.close_all_ws()
        closing.append(runtime._dashboard_runner.cleanup())
    for name in list_transports():
        transport = get_transport(name)
        if transport is not None:
            closing.append(transport.stop_inbound())
    await settle(closing)


class ProcessHooks:
    def __init__(self, runtime: RuntimeCoordinator):
        self.runtime = runtime
        self.loop = asyncio.get_running_loop()
        self.previous = self.loop.get_exception_handler()
        self.stopping = False
        self.installed: list[signal.Signals] = []

    def install(self) -> None:
        self.loop.set_exception_handler(self.capture)
        for item in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(item, self.stop)
            self.installed.append(item)

    def restore(self) -> None:
        self.loop.set_exception_handler(self.previous)
        for item in self.installed:
            self.loop.remove_signal_handler(item)

    def stop(self) -> None:
        if not self.stopping:
            self.stopping = True
            shutdown_event.set()
            return
        from gideon.engine.session import cleanup_orphaned_sessions

        print("\nForce exit!")
        cleanup_orphaned_sessions()
        reap_backends()
        os._exit(0)

    def capture(self, loop: asyncio.AbstractEventLoop, context: dict) -> None:
        try:
            failure = context.get("exception")
            if isinstance(failure, Exception):
                from gideon.operations.resilience.crashes import record_crash

                task = context.get("task")
                name = str(task.get_name() or "") if task is not None else ""
                started = float(
                    getattr(self.runtime.dashboard_state, "start_time", 0.0)
                )
                record_crash(
                    "loop_worker" if "loop" in name.lower() else "turn",
                    failure,
                    session_key=name,
                    uptime_secs=time.time() - started,
                    now=time.time(),
                )
        except Exception:
            log.debug("Could not persist asynchronous failure", exc_info=True)
        if self.previous is None:
            loop.default_exception_handler(context)
        else:
            self.previous(loop, context)


class RuntimeProcess:
    def __init__(self, runtime: RuntimeCoordinator):
        self.runtime = runtime
        self.hooks: ProcessHooks | None = None

    def establish_policy(self) -> None:
        from gideon.core.resource_limits import raise_fd_limit
        from gideon.engine.session import cleanup_orphaned_sessions
        from gideon.integrations.computer_use.enable_state import (
            ensure_computer_use_boot,
        )
        from gideon.security.guardrails.ceiling import ensure_governance_boot

        ensure_governance_boot()
        ensure_computer_use_boot()
        raise_fd_limit()
        cleanup_orphaned_sessions()

    async def start(self) -> None:
        from gideon.integrations.embedding_providers.registry import get_active_embed_fn

        runtime = self.runtime
        runtime._init_services()
        embedding = get_active_embed_fn()
        if embedding and getattr(runtime, "vector_memory", None) is not None:
            runtime.vector_memory.embed_fn = embedding
        await advance(
            (
                StartupStage("schedules", runtime._init_cron),
                StartupStage("heartbeat", runtime._init_heartbeat),
                StartupStage("graph probe", runtime._install_graph_maintenance_probe),
                StartupStage(
                    "graph maintenance", runtime._register_graph_maintenance_passes
                ),
                StartupStage("inbox", runtime._init_inbox, optional=True),
                StartupStage("tool discovery", runtime._init_mcp_discovery),
                StartupStage("delegation", runtime._init_subagents),
                StartupStage(
                    "http",
                    (
                        runtime._init_api_server
                        if runtime._no_dashboard
                        else runtime._init_dashboard
                    ),
                ),
            )
        )
        if runtime._json_ready:
            payload = {
                "port": runtime._dashboard_port,
                "token": generate_token(
                    "local-startup", ttl_seconds=DEFAULT_BROWSER_SESSION_TTL_SECS
                ),
                "pid": os.getpid(),
                "home": str(config_dir()),
            }
            print("GIDEON_READY:" + json.dumps(payload), flush=True)
        await advance(
            (
                StartupStage("autonomous loops", runtime._init_autonudge),
                StartupStage("channels", runtime._start_channel_inbound),
            )
        )
        print("Checking for updates…")
        await runtime._check_for_updates()

    async def probe_tools(self) -> None:
        from gideon.interfaces.dashboard.handlers import _bg_mcp_probe

        try:
            deadline = AppConfig.load().dashboard.mcp_probe_timeout_secs + 15
        except Exception:
            deadline = 30
        print("Probing MCP servers…")
        try:
            await asyncio.wait_for(_bg_mcp_probe(), timeout=deadline)
        except TimeoutError:
            print("MCP probe timed out — continuing without full probe")

    async def announce(self) -> None:
        from gideon.engine.gateway import _open_dashboard
        from gideon.integrations.channel_transports import (
            get_transport,
            list_transports,
        )

        runtime = self.runtime
        try:
            if runtime.sessions is not None:
                await runtime.sessions.start_pool(blocking=False)
                log.info("Background session starting")
        except Exception:
            log.warning("Background session start failed", exc_info=True)
        if not runtime._no_dashboard:
            host = resolve_dashboard_host(runtime._local_only, runtime._configured_host)
            token = generate_token(
                "local-startup", ttl_seconds=DEFAULT_BROWSER_SESSION_TTL_SECS
            )
            url = build_dashboard_url(
                f"http://{host}:{runtime._dashboard_port}",
                token,
                local_only=runtime._local_only,
            )
            for line in format_dashboard_urls(
                url,
                port=runtime._dashboard_port,
                local_only=runtime._local_only,
                has_custom_host=bool(runtime._configured_host),
            ):
                print(line)
            if not runtime._no_open and runtime.config.dashboard.auto_open_browser:
                if browser_available():
                    _open_dashboard(url)
                else:
                    print("Headless remote session — skipping browser auto-open")
        for name in list_transports():
            transport = get_transport(name)
            if transport and transport.name != "webui" and transport.connected:
                print(f"Gideon gateway connected to {transport.display_name}")

    async def serve(self) -> None:
        from gideon.engine.session import cleanup_orphaned_sessions

        self.establish_policy()
        announcement: asyncio.Task | None = None
        try:
            await self.start()
            self.hooks = ProcessHooks(self.runtime)
            self.hooks.install()
            await self.probe_tools()
            announcement = asyncio.create_task(self.announce(), name="gideon-startup")
            print("Gideon gateway starting…")
            print(f"\n{DATA_WARNING}\n")
            await shutdown_event.wait()
            print("Shutting down…")
        finally:
            await cancel_tasks((announcement,))
            try:
                await asyncio.wait_for(self.runtime._shutdown(), timeout=10)
            except Exception:
                log.warning("Graceful shutdown could not finish", exc_info=True)
            if self.hooks is not None:
                self.hooks.restore()
            cleanup_orphaned_sessions()
        print("Goodbye!")
        os._exit(0)
