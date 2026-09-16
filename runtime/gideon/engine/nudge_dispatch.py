from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from gideon.automation.loop import files as loop_files


@dataclass(frozen=True)
class NudgeLimits:
    loop_timeout: float
    chat_timeout: float
    retries: int
    reminder: str


class NudgeTurn:
    def __init__(
        self, driver: NudgeDispatch, state: Any, session: Any, message: str
    ) -> None:
        from gideon.interfaces.dashboard.chat import run_chat

        self.driver, self.state, self.session, self.message = (
            driver,
            state,
            session,
            message,
        )
        self.run_chat = run_chat
        self.loop_worker = getattr(session, "_app", "") == "loop"

    def finding_count(self) -> int:
        try:
            identity = self.session.key.split("loop-", 1)[-1]
            if loop_files.loop_dir(identity) is None and "-" in identity:
                identity = identity.rsplit("-", 1)[0]
            return len(loop_files.get_findings(identity))
        except Exception:
            return 0

    def provider(self):
        from gideon.interfaces.dashboard.chat_utils import _history_key_for

        key = _history_key_for(self.session.key)
        return key, self.state.sessions.get_provider(key)

    async def run_once(self, message: str) -> None:
        limits = self.driver.limits
        timeout = limits.loop_timeout if self.loop_worker else limits.chat_timeout
        try:
            await asyncio.wait_for(
                self.run_chat(self.state, self.session, message), timeout=timeout
            )
        except asyncio.TimeoutError:
            self.driver.logger.warning(
                "AutoNudge: turn for %s exceeded %ss — cancelling wedged turn",
                self.session.key,
                timeout,
            )
            self.session._last_turn_errored = True
            try:
                _, provider = self.provider()
                if provider is not None and hasattr(provider, "cancel"):
                    await provider.cancel()
            except Exception:
                self.driver.logger.debug(
                    "cancel after turn timeout failed for %s",
                    self.session.key,
                    exc_info=True,
                )

    async def fresh_context(self) -> bool:
        started = False
        try:
            key, provider = self.provider()
            fresh = getattr(provider, "start_fresh_turn_session", None)
            if fresh is not None:
                await fresh()
                started = True
                self.state.sessions.mark_new(key)
            else:
                self.driver.logger.debug(
                    "no start_fresh_turn_session on provider for %s", self.session.key
                )
        except Exception:
            self.driver.logger.debug(
                "fresh turn session failed for %s", self.session.key, exc_info=True
            )
        return started

    def rearm(self) -> None:
        self.session._suppress_autonudge_rearm = False
        try:
            from gideon.automation.triggers.nudge import get_instance

            service = get_instance()
            if service is not None:
                service.notify_turn_complete(
                    self.session.key,
                    errored=getattr(self.session, "_last_turn_errored", False),
                )
        except Exception:
            self.driver.logger.debug(
                "re-arm after cycle failed for %s", self.session.key, exc_info=True
            )

    async def run(self) -> None:
        if not self.loop_worker:
            await self.run_once(self.message)
            return
        before = self.finding_count()
        self.session._suppress_autonudge_rearm = True
        try:
            await self.run_once(self.message)
            for attempt in range(self.driver.limits.retries):
                if self.finding_count() > before or getattr(
                    self.session, "_last_turn_errored", False
                ):
                    break
                self.driver.logger.info(
                    "AutoNudge: %s produced no finding (re-prompt %d/%d) — fresh ACP session + re-prompt",
                    self.session.key,
                    attempt + 1,
                    self.driver.limits.retries,
                )
                reminder = self.driver.limits.reminder
                retry = (
                    f"{self.message}\n\n{reminder}"
                    if await self.fresh_context()
                    else reminder
                )
                self.session.append("nudge", retry, "msg msg-nudge")
                await self.run_once(retry)
        finally:
            self.rearm()


class NudgeDispatch:
    def __init__(
        self, runtime: Any, *, render: Any, logger: Any, limits: NudgeLimits
    ) -> None:
        self.runtime, self.render, self.logger, self.limits = (
            runtime,
            render,
            logger,
            limits,
        )

    def observe(self, event: str, loop: Any) -> None:
        state = self.runtime.dashboard_state
        if state and loop is not None:
            fields = (
                "id",
                "session_name",
                "message",
                "idle_secs",
                "max_cycles",
                "cycle_count",
                "active",
                "last_fire_ts",
            )
            state.broadcast_ws(
                "autonudge_state",
                {
                    "event": event,
                    "session": loop.session_name,
                    "loop": {name: getattr(loop, name) for name in fields},
                },
            )

    def completion(self, key: str):
        def report(task: asyncio.Task) -> None:
            runtime = self.runtime
            state = runtime.dashboard_state
            session = state._sessions.get(key) if state else None
            errored = bool(session and getattr(session, "_last_turn_errored", False))
            if runtime.loop_watchdog is not None:
                runtime.loop_watchdog.record_turn_outcome(
                    key.split("loop-", 1)[-1], ok=not errored
                )

        return report

    async def fire(self, loop: Any) -> bool:
        runtime, state = self.runtime, self.runtime.dashboard_state
        if state is None:
            self.logger.warning(
                "AutoNudge: dashboard not ready — skipping fire for loop %s", loop.id
            )
            return False
        session = state._sessions.get(loop.session_name)
        if session is None:
            self.logger.warning(
                "AutoNudge: session %s missing — removing loop %s",
                loop.session_name,
                loop.id,
            )
            await runtime.autonudge_svc.remove(loop.id)
            return False
        message = self.render(loop.message, loop.stop_sentinel_path)
        if session.running or getattr(session, "_suppress_autonudge_rearm", False):
            self.logger.info(
                "AutoNudge skip: session %s is running (loop %s cycle %d)",
                session.key,
                loop.id,
                loop.cycle_count,
            )
            return False
        text = f"[auto-nudge cycle {loop.cycle_count + 1}]\n{message}"
        session.append("nudge", text, "msg msg-nudge")
        turn = NudgeTurn(self, state, session, text)
        task = asyncio.create_task(turn.run())
        session.task = task
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        if turn.loop_worker and runtime.loop_watchdog is not None:
            task.add_done_callback(self.completion(session.key))
        runtime._session_tasks[session.key] = task
        state.push_sessions_update()
        return True


class SupervisorAssembly:
    def __init__(self, runtime: Any, logger: Any) -> None:
        self.runtime, self.logger = runtime, logger

    def start(self) -> None:
        runtime = self.runtime
        if runtime.dashboard_state is not None:
            from gideon.automation.loop.watchdog import LoopWatchdog

            runtime.loop_watchdog = LoopWatchdog(
                runtime.dashboard_state, runtime.autonudge_svc
            )
            runtime.loop_watchdog.start()
        if runtime._cfg.workflows.enabled:
            self.start_workflows()

    def start_workflows(self) -> None:
        from gideon.automation.workflows.bundled_defs import register_bundled_provider
        from gideon.automation.workflows.controller import EngineServices
        from gideon.automation.workflows.native_defs import register_native_provider
        from gideon.automation.workflows.tick import Limits
        from gideon.automation.workflows.verify import run_verify_block
        from gideon.automation.workflows.watchdog import WorkflowWatchdog
        from gideon.cognition.memory_service import MemoryService

        register_native_provider()
        register_bundled_provider()
        runtime = self.runtime
        config = runtime._cfg.workflows
        services = EngineServices(
            subagents=runtime.subagent_mgr,
            verify=run_verify_block,
            model_tiers=config.model_tiers(),
            lane_limits=Limits(lanes=config.lane_caps()),
            node_timeout_total=config.default_node_timeout_total_secs,
            node_timeout_stall=config.default_node_timeout_stall_secs,
            memory=MemoryService.over_vector_store(
                getattr(runtime, "vector_memory", None)
            ),
        )
        runtime.workflow_watchdog = WorkflowWatchdog(runtime.dashboard_state, services)
        runtime.workflow_watchdog.start()
        if runtime.dashboard_state is not None:
            runtime.dashboard_state.workflows = runtime.workflow_watchdog
        try:
            from gideon.integrations.action_providers.services import (
                get_action_services,
            )

            service = get_action_services()
            if service is not None:
                service.workflows = runtime.workflow_watchdog
        except Exception:
            self.logger.debug(
                "could not attach the workflow supervisor to action services"
            )
