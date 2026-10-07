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
        cls,
        config: dict[str, Any],
        ctx: ActionContext,
        task: str,
        *,
        trigger_start_approval: Any = None,
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
                parent_run=(
                    ("workflow:" + str(ctx.payload["run_id"]))
                    if ctx.event == "workflow_node" and ctx.payload.get("run_id")
                    else ""
                ),
                parent_session_key=str(
                    (ctx.payload or {}).get("session_key", "") or ""
                ),
                agent=agent,
                max_turns=turns,
                model=model,
                approval_mode=(
                    None
                    if trigger_start_approval is not None
                    else _approval_for(config)
                ),
                trigger_start_approval=trigger_start_approval,
                capability_class=str(config.get("capability") or "").strip().lower()
                or None,
                silent=False,
                **({"cwd": config["cwd"]} if config.get("cwd") else {}),
            )
        )


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
        payload = ctx.payload if isinstance(ctx.payload, dict) else {}
        trigger_id = str(ctx.trigger_id or payload.get("trigger_id") or "").strip()
        trigger_start_approval = None
        if trigger_id:
            from gideon.automation.triggers.grants import agent_start_approval

            trigger_start_approval = agent_start_approval(trigger_id, action_config)
            if trigger_start_approval is None:
                return ActionResult(
                    False,
                    error="invoke-agent: the current trigger action has no matching owner Allow",
                )
        if trigger_start_approval is not None:
            from gideon.security.durable_work import accepted_trigger_origin

            accepted_origin = getattr(ctx, "accepted_origin", None)
            if accepted_origin is None:
                # Direct native invocation still derives provenance from the actual sealed row.
                accepted_origin = accepted_trigger_origin(trigger_id)
            if accepted_origin is None:
                return ActionResult(
                    False, error="invoke-agent: no current authenticated trigger origin"
                )
        else:
            accepted_origin = None
        invocation = _Invocation.prepare(
            action_config,
            ctx,
            task,
            trigger_start_approval=trigger_start_approval,
        )
        if accepted_origin is not None:
            invocation.arguments["accepted_origin"] = accepted_origin
        from gideon.extensions.apps.agent_tiers import capability_class
        from gideon.extensions.apps.app_work import for_job, held, of_session

        inherited = held() or of_session(
            invocation.arguments.get("parent_session_key", "")
        )
        job_work = for_job(trigger_id) if trigger_start_approval is not None else None
        work = inherited or job_work
        if inherited is not None and job_work is not None:
            work = (
                inherited.child(job_work.tier)
                if inherited.app == job_work.app
                else inherited.child("")
            )
        if work is not None:
            work = work.child()
            if not work.tier:
                return ActionResult(
                    False, error="invoke-agent: app may run no agent work now"
                )
            invocation.arguments["app_work"] = work
            invocation.arguments["capability_class"] = capability_class(work.tier)
            invocation.arguments["approval_mode"] = ""
            if job_work is not None:
                invocation.arguments["parent_session_key"] = f"app:{work.app}"
                invocation.arguments["silent"] = True
                if work.tier == "text":
                    invocation.arguments["agent"] = ""
        await semaphore.acquire()
        from gideon.integrations.action_providers.completion import agent_launch

        try:
            info = services.subagents.spawn(**invocation.arguments)
            return agent_launch(
                services.subagents, info, f"spawned agent for: {task[:80]}"
            )
        except Exception as error:
            return ActionResult(False, error=f"invoke-agent: spawn failed: {error}")
        finally:
            semaphore.release()


def create_provider(config: dict[str, Any] | None = None) -> InvokeAgentActionProvider:
    return InvokeAgentActionProvider()
