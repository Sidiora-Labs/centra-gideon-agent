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
        from gideon.integrations.action_providers.command_lifecycle import action_timeout

        return action_timeout(self.config, {"bash": 300}.get(self.name, 30))


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

    def _stamp_run_owner(self) -> bool:
        """Persist the process that owns this fire before provider code can run."""
        owner_pid = os.getpid()
        try:
            from uuid import uuid4
            from gideon.automation.triggers import claims
            from gideon.automation.triggers.scheduling import Claim
            from gideon.automation.triggers.store import TriggerStore
            from gideon.automation.triggers.routing import routed
            from gideon.core.config.loader import config_dir

            store = routed(TriggerStore(base_dir=config_dir()))
            identity = str(getattr(self.trigger, "id", "") or "")
            stored = store.get(identity)
            if stored is not None and stored.trigger.run_owner_pid and not claims.read_claims(identity, base_dir=config_dir()):
                return False
            expected_holder = str(self.payload.get("claim_holder") or "")
            self._claim_holder = claims.bind_owner(identity, owner_pid=owner_pid, base_dir=config_dir(), expected_holder=expected_holder)
            if expected_holder and not self._claim_holder:
                return False
            if not self._claim_holder:
                claim = Claim(identity, f"dispatch:{uuid4().hex}", time.time())
                if not claims.acquire_claim(claim, owner_pid=owner_pid, overlap=self.trigger.overlap, base_dir=config_dir()):
                    return False
                self._claim_holder = claim.holder
            stored = store.get(str(getattr(self.trigger, "id", "") or ""))
            current = stored.trigger if stored is not None else self.trigger
            current.run_owner_pid = owner_pid
            self.trigger.run_owner_pid = owner_pid
            store.upsert(current)
            return True
        except Exception:
            if getattr(self, "_claim_holder", ""):
                claims.release_claim(self.trigger.id, base_dir=config_dir(), holder=self._claim_holder, owner_pid=owner_pid)
            self.logger.warning(
                "trigger %s: could not persist run owner PID",
                getattr(self.trigger, "id", ""),
                exc_info=True,
            )
            return False

    def _clear_run_owner(self) -> None:
        """Clear only this process's stamp after all dispatch work has settled."""
        owner_pid = os.getpid()
        try:
            from gideon.automation.triggers import claims
            from gideon.automation.triggers.store import TriggerStore
            from gideon.automation.triggers.routing import routed
            from gideon.core.config.loader import config_dir

            store = routed(TriggerStore(base_dir=config_dir()))
            if not claims.release_claim(self.trigger.id, base_dir=config_dir(), holder=getattr(self, "_claim_holder", ""), owner_pid=owner_pid):
                return
            stored = store.get(str(getattr(self.trigger, "id", "") or ""))
            if stored is None or stored.trigger.run_owner_pid != owner_pid:
                return
            stored.trigger.run_owner_pid = next((claim.owner_pid for claim in claims.read_claims(self.trigger.id, base_dir=config_dir())), 0)
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
        from gideon.security.guardrails.rungs import record_execution
        from gideon.automation.triggers.missed import late_outcome
        from gideon.automation.triggers.models import Outcome
        from gideon.security.guardrails.policy import unattended_dispatch_key
        from gideon.security.net.policy import egress_held_to

        started = time.time()
        _, late = late_outcome(
            Outcome.RAN.value,
            scheduled_for=float(self.payload.get("scheduled_for") or 0),
            started_at=started,
        ) if self.payload.get("scheduled_for") else ("", "")
        settled = False
        observing = False
        effect_unresolved = False

        if not self._stamp_run_owner():
            await self.refuse("another run owns this trigger, or its ownership could not be verified")
            return
        try:
            identity = unattended_dispatch_key(f"trigger:{self.trigger.id}")
            with fire_budget(self.trigger, self.logger), egress_held_to(identity):
                result = await action.provider.execute(
                    config, context, timeout=action.timeout
                )
            if bool(getattr(result, "success", False)):
                record_execution(
                    route, result, label=action.name, refs=self.references(action)
                )
            status = await self.runtime._record_fire_outcome(
                self.trigger,
                result=result,
                started=started,
                finished=time.time(),
                late=late,
            )
            settled = status is not None and status not in {"launched", "queued", "waiting", "interrupted"}
            from gideon.automation.triggers import parks

            effect_unresolved = status in {"launched", "queued", "waiting"} and not parks.parked(result)
            completion = getattr(result, "completion", None)
            if status in {"launched", "queued", "waiting"} and completion is not None:
                import asyncio

                task = asyncio.create_task(self._settle_completion(completion, started, late, route=route, label=action.name, refs=self.references(action)))
                self.runtime._handler_tasks.add(task)
                task.add_done_callback(self.runtime._handler_tasks.discard)
                observing = True
            if settled and status != "skipped_noop":
                self.runtime._deliver_fire_outcome(
                    self.trigger,
                    ok=bool(getattr(result, "success", True)),
                    error=str(getattr(result, "error", "") or ""),
                )
            if settled:
                self._retire_recorded(status)
        except Exception as error:
            self.logger.warning(
                "trigger %s: action failed", self.trigger.id, exc_info=True
            )
            status = await self.runtime._record_fire_outcome(
                self.trigger, exc=error, started=started, finished=time.time()
            )
            settled = status is not None
            self.runtime._deliver_fire_outcome(
                self.trigger, ok=False, error=f"{type(error).__name__}: {error}"
            )
        finally:
            try:
                self.runtime._push_trigger_refresh()
                if settled:
                    await self.runtime._fire_chained_triggers(self.trigger, self.payload)
            finally:
                if not observing and not effect_unresolved:
                    self._clear_run_owner()

    async def _settle_completion(self, completion: Any, started: float, late: str, *, route: Any = None, label: str = "", refs: Any = None) -> None:
        import asyncio
        from gideon.integrations.action_providers.base import ActionResult
        from gideon.security.guardrails.policy import unattended_dispatch_key
        from gideon.security.net.policy import egress_held_to

        try:
            try:
                with egress_held_to(unattended_dispatch_key(f"trigger:{self.trigger.id}")):
                    result = await completion()
            except asyncio.CancelledError:
                result = ActionResult(False, outcome="interrupted", error="completion observer stopped during shutdown")
            except Exception as error:
                result = ActionResult(False, error=f"{type(error).__name__}: {error}")
            if route is not None:
                from gideon.security.guardrails.rungs import record_execution
                record_execution(route, result, label=label, refs=refs)
            status = await self.runtime._record_fire_outcome(
                self.trigger, result=result, started=started, finished=time.time(), late=late
            )
            if status is not None and status not in {"launched", "queued", "waiting", "interrupted"}:
                if status != "skipped_noop":
                    self.runtime._deliver_fire_outcome(self.trigger, ok=result.success, error=result.error)
                self._retire_recorded(status)
                await self.runtime._fire_chained_triggers(self.trigger, self.payload)
        finally:
            self.runtime._push_trigger_refresh()
            self._clear_run_owner()

    def _retire_recorded(self, status: str) -> None:
        from gideon.automation.triggers.routing import routed
        from gideon.automation.triggers.service import retire_after_run
        from gideon.automation.triggers.store import TriggerStore
        from gideon.core.config.loader import config_dir

        try:
            retire_after_run(routed(TriggerStore(base_dir=config_dir())), self.trigger, status=status, settled_holder=getattr(self, "_claim_holder", ""))
        except Exception:
            self.logger.warning("trigger %s: retirement failed; retained for review", self.trigger.id, exc_info=True)

    async def run(self) -> None:
        from gideon.automation.triggers import secrets
        from gideon.integrations.action_providers import ActionContext

        action = TriggerAction.resolve(self.trigger.workflow or {})
        if not action.name:
            self.logger.debug("trigger %s has no action provider", self.trigger.id)
            await self.refuse("no action provider configured")
            return
        if action.provider is None:
            self.logger.warning(
                "trigger %s: unknown action provider %r", self.trigger.id, action.name
            )
            await self.refuse(f"unknown action provider: {action.name}")
            return
        if not await self.screen_payload():
            return
        try:
            config = secrets.resolve(action.config)
        except secrets.UnresolvedSecret as error:
            self.logger.warning("trigger %s: %s", self.trigger.id, error)
            await self.refuse(str(error))
            return
        from gideon.security.durable_work import accepted_trigger_origin
        context = ActionContext(
            event=self.event,
            accepted_origin=accepted_trigger_origin(str(self.trigger.id)),
            trigger_id=str(self.trigger.id),
            context=str(self.trigger.id) if action.name == "run-workflow" else "",
            payload=self.payload,
        )
        route = await self.authorize(action, config, context)
        if route is not None:
            await self.execute(action, config, context, route)
