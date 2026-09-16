"""Gateway process orchestrator for Gideon.

Manages the lifecycle of all runtime services: session manager, cron
scheduler, context builder, heartbeat, autonudge, inbox, MCP discovery,
subagents, task runner, dashboard / API server, update checks, and signal
handling. This is the core process boot — it runs with or without any external
channel configured.

Channel connectivity is optional and pluggable via the channel-transport seam:
each registered transport's ``start_inbound`` runs at boot (Slack Socket-Mode
lives entirely in the ``slack-channel`` app bundle), and the transport registers
its outbound :class:`~gideon.integrations.channel_delivery.ChannelDelivery` on the
orchestrator. Core imports NO vendor channel code. With no channel configured the
gateway runs dashboard-only.
"""

import asyncio
import functools
import json
import logging
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from gideon import shutdown_event
from gideon.automation.loop import files as loop_files
from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers.nudge import AutoNudgeService, NudgeLoop
from gideon.automation.triggers.nudge import enabled as autonudge_enabled
from gideon.cognition.context import PromptAssembler
from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.core.config import AppConfig
from gideon.core.config import loader as config_loader
from gideon.core.config.loader import (
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
)
from gideon.core.constants import CHAT_TURN_TIMEOUT, DATA_WARNING
from gideon.core.env import _is_wsl, browser_available
from gideon.engine import gateway_base
from gideon.engine.heartbeat import (
    HeartbeatService,
    is_keep_response,
    strip_keep_sentinel,
)
from gideon.engine.hooks import HookManager, HooksConfig
from gideon.engine.session import BACKGROUND_KEY, ConversationDirectory
from gideon.engine.subagent import (
    INJECTION_TIMEOUT,
    DelegationSupervisor,
    SubagentInfo,
    ToolApprovalCallback,
    resolve_max_subagents,
)
from gideon.extensions.skills import ProcedureLibrary
from gideon.integrations.acp.errors import AcpError, AcpProcessDied
from gideon.integrations.channel_history import ChannelHistory
from gideon.integrations.llm.base import LLMEvent
from gideon.integrations.llm_helpers import PromptBusyExhaustedError, stream_and_collect
from gideon.interfaces.dashboard import start_dashboard
from gideon.interfaces.dashboard.chat_runner import run_chat
from gideon.interfaces.dashboard.handlers import MAX_PROMPT_BYTES
from gideon.interfaces.dashboard.handlers.autonudge import render_nudge_message
from gideon.interfaces.dashboard.origin import (
    build_dashboard_url,
    format_dashboard_urls,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import (
    DEFAULT_BROWSER_SESSION_TTL_SECS,
    generate_token,
)
from gideon.operations.frontend import build_frontend_async
from gideon.security.approval_brief import attach_approval_brief
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.sel import sel
from gideon.workspace import notification_kinds


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


if TYPE_CHECKING:
    from gideon.automation.loop.watchdog import LoopWatchdog
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.integrations.channel_delivery import ChannelDelivery
    from gideon.integrations.channel_transports.base import ChannelMessage
    from gideon.integrations.channel_trust import TrustVerdict
    from gideon.integrations.inbox_service import InboxService
    from gideon.integrations.llm_helpers import ToolApprovalPolicy
    from gideon.interfaces.dashboard.state import _ChatSession

logger = logging.getLogger(__name__)


_MAX_INJECT_ATTEMPTS = 2

_AUTONOMY_PROPOSAL_INTERVAL_SECS = 6 * 60 * 60

_NUDGE_TURN_TIMEOUT = 1800.0

_MAX_CYCLE_REPROMPTS = 3
_CYCLE_REPROMPT_MSG = (
    "You ended the turn without writing this cycle's deliverable. Do it NOW, in "
    "THIS turn, before you stop: use your file-write/editor tools to actually "
    "write findings/cycle_NNN.json (next sequential N) with the structured "
    "finding, and (if the goal has a document deliverable) create or update it in "
    "the loop dir. Do not just describe them — write the files, then end the turn."
)


from gideon.engine.trigger_outcomes import _REFUSAL_STATUSES

_READ_ONLY_TOOL_PREFIXES = (
    "read",
    "list",
    "get",
    "search",
    "find",
    "describe",
    "show",
    "view",
    "fetch",
    "query",
    "grep",
    "ls",
    "cat",
    "head",
    "tail",
)

_WRITE_INDICATORS = (
    "write",
    "delete",
    "create",
    "destroy",
    "remove",
    "update",
    "modify",
    "replace",
    "set",
    "put",
    "post",
    "exec",
    "execute",
    "run",
    "rm",
    "rmdir",
    "drop",
    "patch",
    "send",
    "publish",
    "save",
    "edit",
    "kill",
    "terminate",
)


def _is_read_only_tool(event_title: str) -> bool:
    if not event_title:
        return False
    words = [
        word
        for word in re.split(r"[\s_\-:()/.,]+", event_title.strip().lower())
        if word
    ]
    return bool(
        words
        and words[0] in _READ_ONLY_TOOL_PREFIXES
        and not set(words).intersection(_WRITE_INDICATORS)
    )


def injection_approval_policy(parent_key: str) -> "ToolApprovalPolicy":
    from gideon.integrations.llm_helpers import ToolApprovalPolicy
    from gideon.security.guardrails.policy import (
        approval_policy_for_session,
        is_unattended_session,
    )

    return (
        approval_policy_for_session(parent_key)
        if is_unattended_session(parent_key)
        else ToolApprovalPolicy.AUTO_APPROVE
    )


def _background_write_surface(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def background_call(*args: Any, **kwargs: Any) -> Any:
        from contextlib import ExitStack

        from gideon.operations.durability.state_history import (
            SURFACE_BACKGROUND,
            writing_surface,
        )

        with ExitStack() as contexts:
            contexts.enter_context(writing_surface(SURFACE_BACKGROUND))
            result = await fn(*args, **kwargs)
        return result

    return background_call


class ApprovalFlow:
    """Admission policy and prompt exchange for one gateway approval source."""

    def __init__(self, coordinator: Any, source: str, resolver: Any) -> None:
        self.coordinator = coordinator
        self.source = source
        self.resolver = resolver

    def session_hint(self) -> str:
        state = self.coordinator.dashboard_state
        if state and state._sessions:
            return next(
                (
                    key.removeprefix("dashboard:")
                    for key, session in state._sessions.items()
                    if session.running
                ),
                "",
            )
        return ""

    def cli_approval(self, event: LLMEvent) -> bool:
        mode = self.coordinator._approval_mode
        if mode != "yolo" and not (
            mode == "reads" and _is_read_only_tool(event.title or "")
        ):
            return False
        try:
            title = redact_exfiltration_urls(redact_credentials(event.title or "")[0])[
                0
            ]
            sel().log_api_access(
                caller=f"cli:approval={mode}",
                operation=f"{self.source}.cli_approval_auto_approve",
                outcome="ok",
                resources=title,
            )
        except Exception:
            logger.warning(
                "SEL audit failed for cli --approval auto-approve", exc_info=True
            )
        return True

    def audit_trust(
        self, caller: str, operation: str, title: str, *, approved: bool = False
    ) -> None:
        try:
            from gideon.security.sel import sel as audit_log

            audit_log().log_api_access(
                caller=caller,
                operation=f"{self.source}.{operation}",
                outcome="ok" if approved else "not_auto_approved",
                resources=title,
            )
        except Exception:
            logger.warning("SEL audit failed for trust check", exc_info=True)

    def trusted(self, event: LLMEvent, hint: str) -> bool:
        state = self.coordinator.dashboard_state
        title = redact_exfiltration_urls(redact_credentials(event.title)[0])[0]
        parent = hint or None
        if self.resolver:
            try:
                parent = self.resolver(str(event.request_id))
            except Exception:
                logger.warning(
                    "session_resolver failed for %s", event.request_id, exc_info=True
                )
                parent = None
        if parent:
            session = (state._sessions or {}).get(parent)
            trusted = bool(session and session._trust)
            operation = (
                "scoped_trust_auto_approve"
                if trusted
                else (
                    "scoped_trust_not_trusted"
                    if session
                    else "scoped_trust_session_not_found"
                )
            )
            self.audit_trust(f"session:{parent}", operation, title, approved=trusted)
            return trusted
        if not self.resolver and not hint:
            sessions = state._sessions
            trusted = bool(
                sessions and all(session._trust for session in sessions.values())
            )
            self.audit_trust(
                f"source:{self.source}",
                (
                    "all_sessions_trust_auto_approve"
                    if trusted
                    else "all_sessions_trust_not_trusted"
                ),
                title,
                approved=trusted,
            )
            return trusted
        self.audit_trust(f"source:{self.source}", "scoped_trust_fallthrough", title)
        return False

    def automatically_allowed(self, event: LLMEvent, hint: str) -> bool:
        from gideon.security.trust_mode import is_yolo_active

        coordinator = self.coordinator
        if self.source in coordinator._cfg.hooks.get("auto_approve_sources", []):
            logger.info(
                "Auto-approving tool %s from source %s", event.title, self.source
            )
            return True
        if self.cli_approval(event) or is_yolo_active():
            return True
        state = coordinator.dashboard_state
        return bool(state and (state.is_yolo_active() or self.trusted(event, hint)))

    async def approve(self, event: LLMEvent, parent_session_key: str = "") -> bool:
        hint = self.session_hint()
        if self.automatically_allowed(event, hint):
            return True
        exchange = ApprovalExchange(self, event, hint)
        if self.coordinator._channel_delivery is not None:
            try:
                decision = await exchange.channel(parent_session_key)
                if decision is not None:
                    return decision
            except Exception:
                logger.debug(
                    "Channel approval failed, falling back to dashboard", exc_info=True
                )
        if self.coordinator.dashboard_state:
            return await exchange.dashboard()
        return True


class ApprovalExchange:
    def __init__(self, flow: ApprovalFlow, event: LLMEvent, hint: str) -> None:
        self.flow = flow
        self.event = event
        self.hint = hint
        self.request_id = str(event.request_id)
        self.dashboard_future: asyncio.Future | None = None

    def dashboard(self):
        flow, event = self.flow, self.event
        return flow.coordinator.dashboard_state.request_approval(
            self.request_id,
            flow.source,
            event.title,
            tool_input=event.tool_input,
            tool_purpose=event.tool_purpose,
            session=flow.resolver(self.request_id) if flow.resolver else self.hint,
        )

    def on_prompted(self, pending: Any) -> None:
        if not self.flow.coordinator.dashboard_state:
            return
        self.dashboard_future = asyncio.ensure_future(self.dashboard())

        def relay(completed: asyncio.Future) -> None:
            if completed.cancelled() or completed.exception():
                return
            decision = "approved" if completed.result() else "rejected"
            if not pending.future.done():
                pending.future.set_result(decision)

        self.dashboard_future.add_done_callback(relay)

    async def channel(self, parent_session_key: str):
        flow = self.flow
        approved = None
        attach_approval_brief(self.event)
        try:
            approved = await flow.coordinator._channel_delivery.request_approval(
                self.event,
                source=flow.source,
                parent_session_key=parent_session_key,
                sessions=flow.coordinator.sessions,
                on_prompted=self.on_prompted,
            )
        finally:
            state = flow.coordinator.dashboard_state
            if state:
                state.resolve_approval(self.request_id, bool(approved))
            if self.dashboard_future and not self.dashboard_future.done():
                self.dashboard_future.cancel()
        return approved


class RuntimeCoordinator:
    """Manages the lifecycle of all gateway services.

    Responsibilities are intentionally narrow — event routing and
    interactive handling are delegated to :mod:`events` and
    :mod:`interactions` respectively.
    """

    def __init__(
        self,
        cfg: AppConfig,
        *,
        no_dashboard: bool = False,
        no_crons: bool = False,
        no_open: bool = False,
        port_override: str | None = None,
        json_ready: bool = False,
        approval_mode: str | None = None,
    ) -> None:
        from gideon.engine.gateway_state import LaunchSettings

        LaunchSettings(
            no_dashboard, no_crons, no_open, port_override, json_ready, approval_mode
        ).initialize(self, cfg)

    @property
    def config(self) -> AppConfig:
        return self._cfg

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def _channel_delivery(self) -> "ChannelDelivery | None":
        from gideon.integrations import channel_delivery

        return channel_delivery.owner_reachable()

    @_channel_delivery.setter
    def _channel_delivery(self, delivery: "ChannelDelivery | None") -> None:
        self.register_channel_delivery(delivery)

    def register_channel_delivery(
        self, delivery: "ChannelDelivery | None", provider: str = ""
    ) -> None:
        from gideon.integrations import channel_delivery

        channel_delivery.register(delivery, provider)

    async def deliver_channel_inbound(
        self, provider: str, msg: "ChannelMessage", *, is_dm: bool = True
    ) -> "TrustVerdict":
        from gideon.integrations import channel_inbound

        return await channel_inbound.deliver_inbound(
            self, provider, msg, is_dm=is_dm, turn_runner=run_chat
        )

    def _interactive_approval(
        self, source: str, session_resolver: Callable[[str], str] | None = None
    ) -> ToolApprovalCallback:
        return ApprovalFlow(self, source, session_resolver).approve

    _REQUIRED_DEPS = [
        ("snowballstemmer", "snowballstemmer>=1.0"),
    ]

    def _check_missing_deps(self) -> None:
        from gideon.engine.gateway_maintenance import DependencyRepair

        DependencyRepair(self._REQUIRED_DEPS, logger).run()

    def _init_services(self) -> None:
        from gideon.automation.workflows.legacy import archive_legacy_sops
        from gideon.engine.agent import rebuild_agent_config
        from gideon.engine.services import assemble_conversations

        preparations = (
            ("dependency repair", self._check_missing_deps),
            ("agent configuration", rebuild_agent_config),
            (
                "legacy workflow preservation",
                lambda: archive_legacy_sops(config_dir() / "workflows"),
            ),
        )
        for name, prepare in preparations:
            try:
                prepare()
            except Exception:
                logger.warning("Startup preparation failed: %s", name, exc_info=True)
        resources = assemble_conversations(self.config)
        self.ctx_builder = resources.context
        self.conv_log = resources.history
        self.consolidator = resources.consolidation
        self.sessions = resources.conversations
        self.vector_memory = resources.semantic
        self.channel_history = resources.channels
        logger.info(
            "Conversation services ready; indexed %s files", resources.indexed_files
        )

    def _day_budget_exceeded(self, *, context: str) -> bool:
        from gideon.engine.automation_routes import DailySpendGate

        return DailySpendGate(self, logger).exceeded(context)

    async def _clock_loop(self) -> None:
        from gideon.engine.automation_routes import AutomationRoutes

        await AutomationRoutes(self, logger).clock()

    def _push_trigger_refresh(self) -> None:
        state = getattr(self, "dashboard_state", None)
        try:
            if state is not None:
                topics = ("crons", "cron_history")
                state.push_refresh(*topics)
        except Exception:
            logger.debug("could not push a trigger refresh", exc_info=True)

    async def _trigger_reaper_loop(self) -> None:
        from gideon.engine.automation_routes import AutomationRoutes

        await AutomationRoutes(self, logger).reap()

    @_background_write_surface
    async def _fire_store_trigger(
        self, trigger: Any, payload: dict[str, Any], *, event: str = "trigger.fired"
    ) -> None:
        from gideon.engine.trigger_dispatch import TriggerDispatch

        await TriggerDispatch(self, trigger, payload, event, logger).run()

    def _surface_missed_review(self, report: dict[str, Any]) -> None:
        from gideon.engine.trigger_outcomes import TriggerPublication

        TriggerPublication(self, logger).missed(report)

    def _surface_attention_card(self, trigger: Any, decision: Any) -> None:
        from gideon.engine.trigger_outcomes import TriggerPublication

        TriggerPublication(self, logger).attention(trigger, decision)

    def _next_delivery_attempt(self) -> str:
        from gideon.engine.trigger_outcomes import TriggerPublication

        return TriggerPublication(self, logger).next_attempt()

    def _dedupe_repeat_failure(self, trigger: Any, *, error: str) -> bool:
        from gideon.engine.trigger_outcomes import TriggerPublication

        return TriggerPublication(self, logger).repeated_failure(trigger, error)

    def _deliver_fire_outcome(self, trigger: Any, *, ok: bool, error: str = "") -> None:
        from gideon.engine.trigger_outcomes import TriggerPublication

        TriggerPublication(self, logger).outcome(trigger, ok, error)

    async def _record_fire_outcome(
        self, trigger: Any, *, result: Any = None, exc: BaseException | None = None
    ) -> None:
        from gideon.engine.trigger_outcomes import FireLedger

        await FireLedger(self, ExecutionJournal, logger).record(trigger, result, exc)

    async def _record_blocked_fire(self, trigger: Any, groups: str) -> None:
        message = "payload blocked by the injection screen ({}); never retried".format(
            groups
        )
        await self._record_refused_fire(
            trigger, status="blocked_injection", error=message
        )

    async def _record_refused_fire(
        self, trigger: Any, *, status: str, error: str
    ) -> None:
        from gideon.engine.trigger_outcomes import FireLedger

        await FireLedger(self, ExecutionJournal, logger).refused(trigger, status, error)

    async def _fire_chained_triggers(
        self, trigger: Any, payload: dict[str, Any]
    ) -> None:
        from gideon.engine.automation_routes import AutomationRoutes

        await AutomationRoutes(self, logger).cascade(trigger, payload)

    async def _file_watch_poll_loop(self) -> None:
        from gideon.engine.background_passes import WatchPoll

        await WatchPoll(self, web=False, logger=logger).run()

    def _scan_autonomy_promotions(self) -> None:
        from gideon.engine.background_passes import AutonomySweep

        AutonomySweep(
            self, interval=_AUTONOMY_PROPOSAL_INTERVAL_SECS, logger=logger
        ).run()

    def _scan_scratchpad(self) -> None:
        from gideon.engine.background_passes import scan_scratchpad

        scan_scratchpad(self, logger)

    async def _web_watch_poll_loop(self) -> None:
        from gideon.engine.background_passes import WatchPoll

        await WatchPoll(self, web=True, logger=logger).run()

    async def _fire_file_trigger(self, payload: dict[str, Any]) -> None:
        from gideon.engine.automation_routes import AutomationRoutes

        await AutomationRoutes(self, logger).file(payload)

    async def _init_cron(self) -> None:
        from gideon.engine.automation_boot import AutomationBoot

        await AutomationBoot(self, home=config_dir, logger=logger).start()

    async def _init_heartbeat(self) -> None:
        from gideon.engine.heartbeat_jobs import HeartbeatJobs

        async def collect(*args: Any, **kwargs: Any):
            return await stream_and_collect(*args, **kwargs)

        jobs = HeartbeatJobs(self, collect=collect, audit=lambda: sel(), logger=logger)
        self.heartbeat_svc = HeartbeatService(
            on_task=jobs.task,
            consolidator=self.consolidator,
            on_due_commitments=jobs.commitments,
            on_auto_archive=jobs.archive,
        )
        await self.heartbeat_svc.start()

    def _register_graph_maintenance_passes(self) -> None:
        from gideon.engine.background_passes import KnowledgeMaintenance

        KnowledgeMaintenance(self, logger).register()

    def _install_graph_maintenance_probe(self) -> None:
        from gideon.engine.background_passes import KnowledgeMaintenance

        KnowledgeMaintenance(self, logger).install_probe()

    async def _init_autonudge(self) -> None:
        from gideon.engine.nudge_dispatch import (
            NudgeDispatch,
            NudgeLimits,
            SupervisorAssembly,
        )

        if not autonudge_enabled():
            logger.info("AutoNudge disabled via feature flag")
            return
        dispatch = NudgeDispatch(
            self,
            render=render_nudge_message,
            logger=logger,
            limits=NudgeLimits(
                _NUDGE_TURN_TIMEOUT,
                CHAT_TURN_TIMEOUT,
                _MAX_CYCLE_REPROMPTS,
                _CYCLE_REPROMPT_MSG,
            ),
        )
        self.autonudge_svc = AutoNudgeService(
            base_dir=config_dir(), on_fire=dispatch.fire
        )
        self.autonudge_svc.subscribe(dispatch.observe)
        await self.autonudge_svc.start()
        SupervisorAssembly(self, logger).start()

    async def _init_inbox(self) -> None:
        from gideon.engine.result_delivery import InboxAssembly

        InboxAssembly(self, logger).start()

    async def _restart_inbox(self) -> str:
        try:
            self._cfg = AppConfig.load()
            await self._init_inbox()
            if self.dashboard_state is not None:
                self.dashboard_state._inbox_svc = self.inbox_svc
        except Exception as error:
            logger.exception("Inbox restart failed")
            return str(error) or "restart failed"
        return "ok"

    def _notif_meta(self, parent_key: str | None) -> dict[str, str] | None:
        from gideon.engine.result_delivery import notification_origin

        return notification_origin(self, parent_key, logger)

    async def _deliver_result(
        self,
        title: str,
        task_summary: str,
        result_text: str,
        deliver: str,
    ) -> None:
        from gideon.engine.result_delivery import ResultDelivery, ResultNotice

        delivery = ResultDelivery(
            self,
            ResultNotice.scrub(title, task_summary, result_text),
            audit=sel,
            logger=logger,
            maximum=MAX_PROMPT_BYTES,
        )
        if deliver == "dashboard":
            if self.dashboard_state:
                delivery.show(self.dashboard_state.get_or_create_session())
            return
        await delivery.send(deliver)

    def _init_mcp_discovery(self) -> None:
        try:
            from gideon.integrations.mcp_discovery import list_servers

            names = tuple(server.name for server in list_servers())
            message = (
                f"Configured MCP servers: {', '.join(names)}"
                if names
                else "No MCP servers configured"
            )
            logger.info(message)
        except Exception:
            logger.debug("MCP server listing failed", exc_info=True)

    def _init_subagents(self) -> None:
        from gideon.engine.delegation_host import DelegationHost

        DelegationHost(self, sys.modules[__name__]).start()

    def _publish_runtime_base(self) -> None:
        """Publish the socket we ACTUALLY bound, for every child to resolve from.

        This process is the only one that knows the answer, so it is the only one allowed
        to state it: ``gateway_base.publish()`` records the bound port in this process's
        environment (inherited by every child we spawn) and in a per-home runtime record
        (readable by a child whose environment was rebuilt from an allowlist).

        Before the owner existed, each child worked the base out for itself from
        ``dashboard.url`` or from the import-time ``DASHBOARD_PORT``, and BOTH fall back to
        the fixed 10000. Neither ``--port`` nor ``--port auto`` writes that config, so a
        gateway on another port addressed its children at 10000 — which on a multi-instance
        host is a DIFFERENT instance, not a dead socket (#2539). Two earlier symptoms of the
        same root: ``subagent_run`` answering ``<urlopen error [Errno 61] Connection
        refused>`` on a kiro ACP session while the in-process tools beside it worked
        (`AAP-3`, `K58`), and a ``run-script`` action's ``ctx.notify()`` persisting into a
        second instance's notification store.

        No ``if self._dashboard_port:`` guard: an unset/zero port here means we bound but
        cannot say to what, and ``publish()`` raises. Failing at startup is the honest
        answer — the guard's silence just deferred the same failure to the first tool call,
        by which time the request had already gone somewhere.
        """
        gateway_base.publish(self._dashboard_port)

    async def _init_dashboard(self) -> None:
        from gideon.engine.lifecycle import bind_surface

        await bind_surface(self, api_only=False)

    async def _init_api_server(self) -> None:
        from gideon.engine.lifecycle import bind_surface

        await bind_surface(self, api_only=True)

    async def _shutdown(self) -> None:
        from gideon.engine.lifecycle import retire

        await retire(self)

    async def _check_for_updates(self) -> None:
        from gideon.engine.gateway_maintenance import RuntimeUpdates

        await RuntimeUpdates(self, build_frontend_async, logger).check()

    async def _auto_apply_update(self) -> None:
        from gideon.engine.gateway_maintenance import RuntimeUpdates

        await RuntimeUpdates(self, build_frontend_async, logger).apply()

    async def _start_channel_inbound(self) -> None:
        from gideon.engine.lifecycle import StartupStage, advance
        from gideon.integrations.channel_transports import (
            get_transport,
            list_transports,
        )

        receivers = [get_transport(name) for name in list_transports()]
        stages = (
            StartupStage(
                f"channel:{receiver.name}",
                lambda receiver=receiver: receiver.start_inbound(self),
                optional=True,
            )
            for receiver in receivers
            if receiver is not None
        )
        await advance(stages)

    async def run(self) -> None:
        from gideon.engine.lifecycle import RuntimeProcess

        await RuntimeProcess(self).serve()


def _open_dashboard(url: str) -> None:
    import webbrowser

    print(f"Open Gideon: {url}", flush=True)
    if not _is_wsl():
        try:
            if webbrowser.open(url):
                return
        except Exception:
            pass
    _wslview_open(url)


def _wslview_open(url: str) -> bool:
    import subprocess

    command = ["wslview", url]
    try:
        subprocess.run(command, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    else:
        return True


async def run_gateway(
    cfg: AppConfig,
    *,
    no_dashboard: bool = False,
    no_crons: bool = False,
    no_open: bool = False,
    port_override: str | None = None,
    json_ready: bool = False,
    approval_mode: str | None = None,
) -> None:
    await RuntimeCoordinator(
        cfg,
        no_dashboard=no_dashboard,
        no_crons=no_crons,
        no_open=no_open,
        port_override=port_override,
        json_ready=json_ready,
        approval_mode=approval_mode,
    ).run()
