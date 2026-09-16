"""Bounded background admission for agents invoked by native actions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.services import get_action_services
from gideon.integrations.action_providers.template import render_template

logger = logging.getLogger(__name__)
_HOOK_INVOKE_MAX_DEPTH = 3
_HOOK_INVOKE_MAX_CONCURRENT = 6
_invoke_agent_sem = asyncio.Semaphore(_HOOK_INVOKE_MAX_CONCURRENT)


def _approval_for(config: dict[str, Any]) -> str | None:
    requested = (config.get("approval_mode") or "").strip() or None
    if requested is None:
        try:
            from gideon.core.config.loader import AppConfig
            from gideon.engine.hooks import HooksConfig

            policy = HooksConfig.from_dict(AppConfig.load().hooks)
            requested = "auto" if policy.auto_approve_subagent_spawn else None
        except Exception:
            logger.debug(
                "invoke-agent: auto-approve config lookup failed", exc_info=True
            )
    return requested


@dataclass(frozen=True)
class _Invocation:
    arguments: dict[str, Any]

    @classmethod
    def prepare(
        cls, config: dict[str, Any], ctx: ActionContext, task: str
    ) -> _Invocation:
        agent = (config.get("agent") or "").strip()
        model = (config.get("model") or "").strip() or None
        try:
            turns = int(config.get("max_turns", 0) or 0)
        except (ValueError, TypeError):
            turns = 0
        return cls(
            dict(
                task=task,
                parent_session_key=str(
                    (ctx.payload or {}).get("session_key", "") or ""
                ),
                agent=agent,
                max_turns=turns,
                model=model,
                approval_mode=_approval_for(config),
                capability_class=str(config.get("capability") or "").strip().lower()
                or None,
                silent=False,
            )
        )


@dataclass
class _SpawnReservation:
    semaphore: asyncio.Semaphore
    released: bool = False

    def release(self, completed: Any = None) -> None:
        if not self.released:
            self.released = True
            self.semaphore.release()

    async def run(self, services: Any, invocation: _Invocation) -> None:
        try:
            services.subagents.spawn(**invocation.arguments)
        except Exception:
            logger.warning("invoke-agent: spawn failed", exc_info=True)
        finally:
            self.release()

    def schedule(self, services: Any, invocation: _Invocation) -> None:
        pending = self.run(services, invocation)
        try:
            scheduled = services.spawn_background(pending)
        except BaseException:
            pending.close()
            self.release()
            raise
        if isinstance(scheduled, asyncio.Future):
            scheduled.add_done_callback(self.release)


class InvokeAgentActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "invoke-agent"

    @property
    def display_name(self) -> str:
        return "Invoke Agent"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        depth = int((ctx.payload or {}).get("__hook_depth", 0) or 0)
        if depth >= _HOOK_INVOKE_MAX_DEPTH:
            return ActionResult(
                False,
                error=f"invoke-agent depth cap ({_HOOK_INVOKE_MAX_DEPTH}) reached — not spawning",
            )
        task = render_template(action_config.get("task_template", ""), ctx).strip()
        if not task:
            return ActionResult(
                False, error="invoke-agent hook is missing 'task_template'"
            )
        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(
                False, error="invoke-agent: subagent manager unavailable"
            )
        semaphore = _invoke_agent_sem
        if semaphore.locked():
            return ActionResult(
                False,
                error=f"invoke-agent capacity reached ({_HOOK_INVOKE_MAX_CONCURRENT} in flight)",
            )
        invocation = _Invocation.prepare(action_config, ctx, task)
        await semaphore.acquire()
        _SpawnReservation(semaphore).schedule(services, invocation)
        return ActionResult(
            True, stdout=f"spawned agent for: {task[:80]}", outcome="launched"
        )


def create_provider(config: dict[str, Any] | None = None) -> InvokeAgentActionProvider:
    return InvokeAgentActionProvider()
