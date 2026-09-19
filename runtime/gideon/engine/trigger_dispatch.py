from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class TriggerAction:
    name: str
    config: dict[str, Any]
    provider: Any

    @classmethod
    def resolve(cls, workflow: dict[str, Any]) -> TriggerAction:
        from gideon.integrations.action_providers import get_action_provider
        from gideon.integrations.action_providers.registry import (
            _ensure_default_providers_registered,
        )

        inline = workflow.get("inline")
        action = (inline or workflow) if isinstance(inline, dict) else workflow
        name = str(action.get("provider") or "")
        implementation = None
        if name:
            _ensure_default_providers_registered()
            implementation = get_action_provider(name)
        return cls(name, action.get("config") or {}, implementation)

    @property
    def timeout(self) -> int:
        return {"bash": 300}.get(self.name, 30)


@contextmanager
def fire_budget(trigger: Any, logger: logging.Logger) -> Iterator[None]:
    from gideon.automation.triggers.calendar import run_budget_for
    from gideon.security.guardrails import budgets

    key = f"trigger:{trigger.id}:{int(time.time() * 1000)}"
    identity = budgets.set_current_run_key(key)
    ceiling = budgets.set_current_run_budget(
        run_budget_for(getattr(trigger, "gates", None))
    )
    try:
        yield
    finally:
        budgets.reset_current_run_budget(ceiling)
        budgets.reset_current_run_key(identity)
        try:
            budgets.get_meter().end_run(key)
        except Exception:
            logger.debug("end_run failed for %s", key, exc_info=True)


class TriggerDispatch:
    def __init__(
        self,
        runtime: Any,
        trigger: Any,
        payload: dict[str, Any],
        event: str,
        logger: logging.Logger,
    ) -> None:
        self.runtime = runtime
        self.trigger = trigger
        self.payload = payload
        self.event = event
        self.logger = logger

    def _stamp_run_owner(self) -> None:
        """Persist the process that owns this fire before provider code can run."""
        owner_pid = os.getpid()
        self.trigger.run_owner_pid = owner_pid
        try:
            from gideon.automation.triggers.store import TriggerStore
            from gideon.core.config.loader import config_dir

            store = TriggerStore(base_dir=config_dir())
            stored = store.get(str(getattr(self.trigger, "id", "") or ""))
            current = stored.trigger if stored is not None else self.trigger
            current.run_owner_pid = owner_pid
            store.upsert(current)
        except Exception:
            self.logger.warning(
                "trigger %s: could not persist run owner PID",
                getattr(self.trigger, "id", ""),
                exc_info=True,
            )

    def _clear_run_owner(self) -> None:
        """Clear only this process's stamp after all dispatch work has settled."""
        owner_pid = os.getpid()
        try:
            from gideon.automation.triggers.store import TriggerStore
            from gideon.core.config.loader import config_dir

            store = TriggerStore(base_dir=config_dir())
            stored = store.get(str(getattr(self.trigger, "id", "") or ""))
            if stored is None or stored.trigger.run_owner_pid != owner_pid:
                return
            stored.trigger.run_owner_pid = 0
            store.upsert(stored.trigger)
            if getattr(self.trigger, "run_owner_pid", 0) == owner_pid:
                self.trigger.run_owner_pid = 0
        except Exception:
            self.logger.warning(
                "trigger %s: could not clear run owner PID",
                getattr(self.trigger, "id", ""),
                exc_info=True,
            )

    async def screen_payload(self) -> bool:
        from gideon.automation.triggers import screen as screen_mod
        from gideon.automation.triggers.screen import payload_text_for, screen

        kind = str(getattr(self.trigger, "kind", "") or "")
        text = payload_text_for(self.payload, kind=kind)
        if not text:
            return True
        verdict = screen(text)
        if getattr(verdict, "verdict", "") != "blocked":
            self.payload = screen_mod.fence_payload(
                self.payload, kind=kind, trigger_id=self.trigger.id
            )
            return True
        groups = ", ".join(getattr(verdict, "groups", ()) or ()) or "injection"
        self.logger.warning(
            "trigger %s: payload blocked by the injection screen (%s); not retried",
            self.trigger.id,
            groups,
        )
        await self.runtime._record_blocked_fire(self.trigger, groups)
        self.runtime._push_trigger_refresh()
        return False

    async def refuse(self, reason: str) -> None:
        from gideon.automation.triggers.models import Outcome

        await self.runtime._record_refused_fire(
            self.trigger, status=Outcome.SKIPPED_GATE.value, error=reason
        )
        self.runtime._push_trigger_refresh()

    def references(self, action: TriggerAction) -> dict[str, str]:
        return {
            "trigger": str(getattr(self.trigger, "id", "") or ""),
            "provider": action.name,
        }

    async def authorize(
        self, action: TriggerAction, config: dict[str, Any], context: Any
    ) -> Any:
        from gideon.security.guardrails.denylist import enforce_action
        from gideon.security.guardrails.policy import unattended_dispatch_key
        from gideon.security.guardrails.rungs import (
            announce_withheld,
            route_provider_action,
        )

        identity = unattended_dispatch_key(
            f"trigger:{getattr(self.trigger, 'id', '') or ''}"
        )
        decision = enforce_action(action.name, config, context, session_key=identity)
        if decision.blocked:
            matched = decision.matched or ""
            reason = decision.reason or "blocked by a guardrail rule"
            self.logger.warning(
                "trigger %s: action blocked by the guardrails denylist (%s)",
                self.trigger.id,
                matched,
            )
            detail = f"{matched} — {reason}" if matched else reason
            await self.refuse(f"blocked by the guardrails denylist: {detail}")
            return None
        route = route_provider_action(action.name, session_key=identity)
        if route.executes:
            return route
        announce_withheld(
            route,
            title=f"{action.name} is waiting for you",
            body=f"The {action.name!r} action on trigger {self.trigger.id} did not run: {route.reason}.",
            refs=self.references(action),
            dedup_key=f"autonomy_hold:{route.key}:trigger:{getattr(self.trigger, 'id', '')}",
        )
        await self.refuse(f"held for your approval: {route.reason}")
        return None

    async def execute(
        self, action: TriggerAction, config: dict[str, Any], context: Any, route: Any
    ) -> None:
        from gideon.security.guardrails.rungs import record_reversal

        self._stamp_run_owner()
        try:
            with fire_budget(self.trigger, self.logger):
                result = await action.provider.execute(
                    config, context, timeout=action.timeout
                )
            if route.records_reversal and bool(getattr(result, "success", False)):
                record_reversal(
                    route, result, label=action.name, refs=self.references(action)
                )
            await self.runtime._record_fire_outcome(self.trigger, result=result)
            self.runtime._deliver_fire_outcome(
                self.trigger, ok=bool(getattr(result, "success", True))
            )
        except Exception as error:
            self.logger.warning(
                "trigger %s: action failed", self.trigger.id, exc_info=True
            )
            await self.runtime._record_fire_outcome(self.trigger, exc=error)
            self.runtime._deliver_fire_outcome(
                self.trigger, ok=False, error=f"{type(error).__name__}: {error}"
            )
        finally:
            try:
                self.runtime._push_trigger_refresh()
                await self.runtime._fire_chained_triggers(self.trigger, self.payload)
            finally:
                self._clear_run_owner()

    async def run(self) -> None:
        from gideon.automation.triggers import secrets
        from gideon.integrations.action_providers import ActionContext

        action = TriggerAction.resolve(self.trigger.workflow or {})
        if not action.name:
            self.logger.debug("trigger %s has no action provider", self.trigger.id)
            return
        if action.provider is None:
            self.logger.warning(
                "trigger %s: unknown action provider %r", self.trigger.id, action.name
            )
            return
        if not await self.screen_payload():
            return
        try:
            config = secrets.resolve(action.config)
        except secrets.UnresolvedSecret as error:
            self.logger.warning("trigger %s: %s", self.trigger.id, error)
            self.runtime._push_trigger_refresh()
            return
        context = ActionContext(event=self.event, context="", payload=self.payload)
        route = await self.authorize(action, config, context)
        if route is not None:
            await self.execute(action, config, context, route)
