from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from gideon.integrations.acp.errors import AcpError, AcpProcessDied
from gideon.integrations.llm_helpers import PromptBusyExhaustedError
from gideon.workspace import notification_kinds


class DelegationHost:
    def __init__(self, runtime: Any, api: Any) -> None:
        self.runtime, self.api, self.logger = runtime, api, api.logger
        self.approval: Callable[[Any, str], Awaitable[bool]] | None = None

    def redact(self, text: str) -> str:
        filtered, _ = self.api.redact_exfiltration_urls(text)
        filtered, _ = self.api.redact_credentials(filtered)
        return filtered

    def start(self) -> None:
        runtime = self.runtime
        assert runtime.sessions is not None
        assert runtime.ctx_builder is not None
        self.approval = runtime._interactive_approval(
            "subagent", session_resolver=self.session_for
        )
        options = runtime._cfg.agent
        runtime.subagent_mgr = self.api.DelegationSupervisor(
            sessions=runtime.sessions,
            ctx_builder=runtime.ctx_builder,
            on_done=self.completed,
            max_concurrent=self.api.resolve_max_subagents(
                options.max_subagents, per_agent_gb=options.spawn_min_memory_gb
            ),
            default_turn_limit=options.subagent_max_turns,
            default_timeout=options.subagent_timeout_secs,
            on_tool_approval=self.approval,
            on_spawn_approval=self.approve_spawn,
            is_yolo=self.yolo,
            on_event=self.event,
        )
        runtime.subagent_mgr.start_reaper()

    def session_for(self, request: str) -> str:
        identity = request.removeprefix("spawn:")
        manager = self.runtime.subagent_mgr
        agent = manager.get(identity) if manager is not None else None
        parent = agent.parent_session_key if agent and agent.parent_session_key else ""
        session = parent.removeprefix("dashboard:")
        self.logger.info(
            "_spawn_session_resolver: rid=%s agent_id=%s info=%s session=%s",
            request,
            identity,
            agent is not None,
            session,
        )
        return session

    def yolo(self) -> bool:
        from gideon.security.trust_mode import is_yolo_active

        state = self.runtime.dashboard_state
        return bool(state is not None and state.is_yolo_active()) or is_yolo_active()

    async def approve_spawn(
        self, request_id: str, description: str, parent_session_key: str = ""
    ) -> bool:
        event = self.api.LLMEvent(
            kind="permission_request", request_id=request_id, title=description
        )
        return (
            await self.approval(event, parent_session_key)
            if self.approval is not None
            else False
        )

    async def status(self, agent: Any, event: str) -> None:
        runtime, state = self.runtime, self.runtime.dashboard_state
        if not state:
            return
        try:
            peers = (
                runtime.subagent_mgr.running_agents_for(agent.parent_session_key)
                if runtime.subagent_mgr
                else []
            )
            session = agent.parent_session_key.removeprefix("dashboard:")
            state.broadcast_ws(
                "subagent_status",
                dict(
                    running=len(peers),
                    id=agent.id,
                    event=event,
                    session=session,
                    agents=peers,
                ),
            )
        except Exception:
            self.logger.info(
                "Failed to broadcast subagent %s status", agent.id, exc_info=True
            )

    async def event(self, kind: str, agent: Any, extra: dict) -> None:
        state = self.runtime.dashboard_state
        if not state:
            return
        name = agent.parent_session_key.removeprefix("dashboard:")
        payload = dict(id=agent.id, session=name)
        payload.update(extra)
        if kind == "subagent_chunk":
            state.broadcast_ws_subagent_subscribers(kind, payload)
            return
        if kind == "subagent_injection_failed":
            session = state.get_session(name)
            if session:
                task = self.redact((agent.task or "")[:100])
                error = self.redact(extra.get("error", "timed out"))
                session.append(
                    "assistant",
                    (
                        f"[Subagent completion event]\nAgent `{agent.id}` failed\nTask: {task}\n\n"
                        f"Error: {error}\nResult delivery timed out — the subagent finished but "
                        "its result could not be injected into this session."
                    ),
                    "msg msg-a",
                )
                failure = extra.get("failure_msg", "")
                if failure:
                    session._pending_subagent_failures.append(self.redact(failure))
                state.push_sessions_update()
                self.logger.warning(
                    "Injected timeout error for subagent %s into session %s",
                    agent.id,
                    name,
                )
        state.broadcast_ws(kind, payload)

    async def completed(self, agents: list[Any]) -> None:
        if agents:
            await CompletionDelivery(self, agents).send()

    def recover(self, session: Any, parent: str) -> None:
        runtime = self.runtime
        if (
            session._recovery_chat_triggered
            or not session._pending_subagent_failures
            or not runtime.dashboard_state
        ):
            return
        if session._recovery_retrigger_count >= 3:
            self.logger.warning(
                "Recovery retrigger cap (3) reached for %s, dropping %d queued failures",
                parent,
                len(session._pending_subagent_failures),
            )
            session._pending_subagent_failures.clear()
            return
        from gideon.interfaces.dashboard.chat import run_chat

        session._recovery_retrigger_count += 1
        session._recovery_chat_triggered = True
        message = self.redact("\n\n".join(session._pending_subagent_failures))
        session._pending_subagent_failures.clear()
        session.append("user", message, "msg msg-u auto-go")

        def settled(task: asyncio.Task) -> None:
            session._recovery_chat_triggered = False
            if task.cancelled():
                self.logger.warning("Re-triggered recovery cancelled for %s", parent)
                return
            if task.exception():
                self.logger.error(
                    "Re-triggered recovery failed for %s",
                    parent,
                    exc_info=task.exception(),
                )
            if session._pending_subagent_failures:
                self.recover(session, parent)

        task = asyncio.create_task(
            asyncio.wait_for(
                run_chat(runtime.dashboard_state, session, message),
                timeout=self.api.CHAT_TURN_TIMEOUT,
            )
        )
        session.task = task
        runtime._background_tasks.add(task)
        task.add_done_callback(runtime._background_tasks.discard)
        task.add_done_callback(settled)


class CompletionDelivery:
    def __init__(self, host: DelegationHost, agents: list[Any]) -> None:
        self.host, self.runtime, self.api, self.logger = (
            host,
            host.runtime,
            host.api,
            host.logger,
        )
        self.agents, self.lead = agents, agents[0]
        self.parent = self.lead.parent_session_key
        self.title = self.announce = self.body = ""

    def prepare(self) -> None:
        agents, host = self.agents, self.host
        blocks = [self.block(agent) for agent in agents]
        failures = sum(bool(agent.error) for agent in agents)
        if len(agents) == 1:
            status = "failed" if self.lead.error else "completed"
            title = f"Subagent `{self.lead.id}` {status}"
            self.announce = "[Subagent completion event]\n" + blocks[0]
        else:
            status = "with failures" if failures else "completed"
            title = f"{len(agents)} subagents {status}"
            self.announce = (
                f"[Subagent completion batch — {len(agents)} agents, {failures} failed]\n\n"
                + "\n\n---\n\n".join(blocks)
            )
        self.title, self.body = host.redact(title), self.announce

    def block(self, agent: Any) -> str:
        status = "failed" if agent.error else "completed"
        if agent.error:
            detail = f"Error: {agent.error}"
        else:
            detail = agent.result or "_No response._"
            if len(detail) > 3000:
                from gideon.integrations.tool_providers.projection import (
                    project_and_retain,
                )

                detail, _ = project_and_retain(
                    detail, session_key=self.parent, cap=3000
                )
        task = self.host.redact(agent.task)[:100]
        suffix = f" ({agent.agent})" if agent.agent else ""
        return f"Agent `{agent.id}`{suffix} {status}\nTask: {task}\n\n{self.host.redact(detail)}"

    def failed(self, reason: str) -> None:
        if self.runtime.subagent_mgr:
            for agent in self.agents:
                self.runtime.subagent_mgr.notify_injection_failed(agent, reason=reason)

    def notify(self) -> None:
        state = self.runtime.dashboard_state
        if state:
            state.notify(
                notification_kinds.SUBAGENT,
                self.title,
                self.body,
                meta=self.runtime._notif_meta(self.parent),
            )

    async def reset(self, context: str) -> None:
        try:
            assert self.runtime.sessions is not None
            await self.runtime.sessions.reset(self.parent)
        except Exception:
            self.logger.debug(
                "Failed to reset %s after %s", self.parent, context, exc_info=True
            )

    async def release(self) -> None:
        try:
            await self.runtime.sessions.cancel_current(self.parent)
        except Exception:
            self.logger.debug(
                "Failed to cancel parent prompt for %s", self.lead.id, exc_info=True
            )
        try:
            self.runtime.sessions.release(self.parent)
        except Exception:
            self.logger.exception("Failed to release session %s", self.parent)

    def message(self, is_new: bool) -> str:
        if self.runtime.ctx_builder:
            content, _ = self.runtime.ctx_builder.build_message(
                self.announce, is_new, self.parent
            )
            return content
        return self.announce

    async def inject(self, client: Any, message: str, source: str) -> str | None:
        def usage(event: object) -> None:
            from gideon.operations.usage_ledger import record_from_event

            model = getattr(getattr(client, "client", None), "_model", "") or ""
            record_from_event(
                event,
                source=source,
                session_key=self.parent,
                provider="acp",
                model=model if isinstance(model, str) and model != "auto" else "",
            )

        policy = self.api.injection_approval_policy(self.parent)
        hooks = self.runtime.ctx_builder.hooks if self.runtime.ctx_builder else None
        for attempt in range(3):
            try:
                return await self.api.stream_and_collect(
                    client,
                    message,
                    on_complete=usage,
                    approval_policy=policy,
                    hooks=hooks,
                )
            except (PromptBusyExhaustedError, AcpProcessDied) as error:
                reason = (
                    "provider dead after prompt-busy retries"
                    if isinstance(error, PromptBusyExhaustedError)
                    else "ACP process died"
                )
                self.logger.warning(
                    "Subagent %s: %s during %s injection", self.lead.id, reason, source
                )
                await self.reset(reason)
                self.failed(reason)
                return None
            except AcpError:
                if attempt >= 2:
                    raise
                self.logger.warning(
                    "Subagent %s %s injection attempt %d failed, retrying",
                    self.lead.id,
                    source,
                    attempt + 1,
                )
                try:
                    assert self.runtime.sessions is not None
                    await self.runtime.sessions.cancel_current(self.parent)
                except Exception:
                    self.logger.debug(
                        "Failed to cancel parent prompt for %s",
                        self.lead.id,
                        exc_info=True,
                    )
                await asyncio.sleep(2**attempt)
        return None

    async def dashboard(self) -> None:
        state = self.runtime.dashboard_state
        name = self.parent.removeprefix("dashboard:")
        session = state.get_session(name)
        self.announce, self.body = self.host.redact(self.announce), self.host.redact(
            self.body
        )
        if session:
            if session.running and session.task is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(session.task), timeout=self.api.INJECTION_TIMEOUT
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            if session.running:
                session.queue_append(self.announce)
                state.push_sessions_update()
                self.notify()
                return
            task = asyncio.create_task(
                asyncio.wait_for(
                    self.api.run_chat(state, session, self.announce),
                    timeout=self.api.CHAT_TURN_TIMEOUT,
                )
            )
            session.task = task
            state._background_tasks.add(task)
            task.add_done_callback(state._background_tasks.discard)

            def settled(completed: asyncio.Task) -> None:
                if session.task is completed:
                    session.task = None
                if not completed.cancelled() and completed.exception():
                    self.logger.error(
                        "Subagent injection run_chat failed: %s", completed.exception()
                    )
                    self.failed(self.host.redact(str(completed.exception())))

            task.add_done_callback(settled)
            state.push_sessions_update()
        self.notify()

    async def post_channel(self, response: str | None) -> None:
        try:
            delivery = self.runtime._channel_delivery
            if response and delivery is not None and self.runtime._owner_id:
                sessions = self.runtime.sessions
                channel = (
                    sessions.get_channel(self.parent) if sessions else None
                ) or await delivery.open_dm(self.runtime._owner_id)
                if channel:
                    elapsed = (
                        self.lead.elapsed
                        if self.lead.elapsed > 0
                        else time.monotonic() - self.lead.started
                    )
                    await delivery.deliver_subagent_reply(
                        channel, response, self.parent, elapsed
                    )
        except Exception:
            self.logger.exception(
                "Subagent %s: channel posting failed (injection succeeded)",
                self.lead.id,
            )

    async def channel(self) -> None:
        from gideon.automation.workflows.failure_taxonomy import classify_exception

        assert self.runtime.sessions is not None
        delivered = False
        reasons = []
        attempts_made = 0
        for attempt in range(1, self.api._MAX_INJECT_ATTEMPTS + 1):
            attempts_made = attempt
            if attempt > 1:
                await asyncio.sleep(2)
            acquired = False
            try:
                client, new, _ = await self.runtime.sessions.get_or_create(self.parent)
                acquired = True
                response = await asyncio.wait_for(
                    self.inject(client, self.message(new), "channel"),
                    timeout=self.api.INJECTION_TIMEOUT,
                )
                delivered = True
                await self.post_channel(response)
                break
            except asyncio.TimeoutError:
                reasons.append(
                    f"attempt {attempt} timed out after {int(self.api.INJECTION_TIMEOUT)}s"
                )
                self.logger.warning(
                    "Subagent %s: channel injection attempt %d/%d timed out after %.0fs",
                    self.lead.id,
                    attempt,
                    self.api._MAX_INJECT_ATTEMPTS,
                    self.api.INJECTION_TIMEOUT,
                )
                if acquired:
                    await self.reset("channel injection timeout")
            except Exception as error:
                reasons.append(f"attempt {attempt} failed: {error}")
                self.logger.exception(
                    "Subagent %s channel injection failed", self.lead.id
                )
                if not classify_exception(error).retryable:
                    break
                if acquired:
                    await self.reset("retriable channel injection failure")
            finally:
                if acquired:
                    await self.release()
        if not delivered:
            detail = self.host.redact("; ".join(reasons))
            self.logger.error(
                "Subagent %s: all %d channel injection attempts failed: %s",
                self.lead.id,
                attempts_made,
                detail,
            )
            self.failed(detail)
        self.notify()

    async def cron(self) -> None:
        runtime = self.runtime
        runtime._cron_injecting[self.parent] = (
            runtime._cron_injecting.get(self.parent, 0) + 1
        )
        assert runtime.sessions is not None
        acquired, response = False, None
        try:
            client, new, _ = await runtime.sessions.get_or_create(self.parent)
            acquired = True
            response = await asyncio.wait_for(
                self.inject(client, self.message(new), "cron"),
                timeout=self.api.INJECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self.logger.error(
                "Subagent %s: cron injection timed out after %.0fs",
                self.lead.id,
                self.api.INJECTION_TIMEOUT,
            )
            await self.reset("cron injection timeout")
            self.failed(f"injection timed out after {int(self.api.INJECTION_TIMEOUT)}s")
        except Exception:
            self.logger.exception("Subagent %s cron injection failed", self.lead.id)
        finally:
            if acquired:
                await self.release()
            remaining = runtime._cron_injecting.get(self.parent, 1) - 1
            if remaining <= 0:
                runtime._cron_injecting.pop(self.parent, None)
            else:
                runtime._cron_injecting[self.parent] = remaining
        if response:
            self.body += "\n\n" + self.host.redact(response)
        completed = {agent.id for agent in self.agents}
        busy = runtime.subagent_mgr and any(
            agent.parent_session_key == self.parent and agent.id not in completed
            for agent in runtime.subagent_mgr.running
        )
        if not busy and runtime._cron_injecting.get(self.parent, 0) <= 0:
            await self.reset("last subagent completion")

    async def send(self) -> None:
        for agent in self.agents:
            await self.host.status(agent, "done")
        self.prepare()
        if self.parent.startswith("dashboard:") and self.runtime.dashboard_state:
            await self.dashboard()
        elif self.parent and not self.parent.startswith(("cron:", "subagent:")):
            await self.channel()
        else:
            if self.parent.startswith("cron:"):
                await self.cron()
            if not all(agent.silent for agent in self.agents):
                self.notify()
