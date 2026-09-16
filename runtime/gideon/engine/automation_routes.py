from __future__ import annotations

import logging
from typing import Any


class AutomationRoutes:
    def __init__(self, runtime: Any, logger: logging.Logger) -> None:
        self.runtime, self.logger = runtime, logger
        self._store = None

    @property
    def store(self):
        from gideon.automation.triggers.store import TriggerStore
        from gideon.core.config.loader import config_dir

        if self._store is None:
            self._store = TriggerStore(base_dir=config_dir())
        return self._store

    async def _runner(self, payload: dict[str, Any]) -> dict[str, str]:
        selected = self.store.get(str(payload.get("trigger_id") or ""))
        if selected is not None:
            await self.runtime._fire_store_trigger(selected.trigger, payload)
            return {"status": "launched"}
        return {"status": "error"}

    async def clock(self) -> None:
        from gideon.automation.triggers.loop import run_forever

        await run_forever(
            self.store,
            runner=self._runner,
            sessions=self.runtime.sessions,
            base_dir=self.store.base_dir,
        )

    async def reap(self) -> None:
        from gideon.automation.triggers.reaper import run_forever

        await run_forever(store=self.store, base_dir=self.store.base_dir)

    async def file(self, payload: dict[str, Any]) -> None:
        identity = str(payload.get("trigger_id") or "")
        selected = self.store.get(identity)
        if selected is not None:
            await self.runtime._fire_store_trigger(
                selected.trigger, payload, event="file.changed"
            )

    async def cascade(self, source: Any, payload: dict[str, Any]) -> None:
        try:
            from gideon.automation.triggers.chain import next_fires

            declaration = source.workflow if isinstance(source.workflow, dict) else {}
            pending, refused = next_fires(
                self.store,
                source_id=source.id,
                source_payload=payload,
                source_def=str(declaration.get("ref", "") or ""),
            )
            for refusal in refused:
                self.logger.info(
                    "chain %s did not fire: %s",
                    refusal["trigger_id"],
                    refusal["reason"],
                )
            for trigger, message in pending:
                await self.runtime._fire_store_trigger(
                    trigger, message, event="trigger.chained"
                )
        except Exception:
            self.logger.warning(
                "chain dispatch failed after %s", source.id, exc_info=True
            )


class DailySpendGate:
    def __init__(self, runtime: Any, logger: logging.Logger) -> None:
        self.runtime, self.logger = runtime, logger

    def announce(self, context: str, reason: str) -> None:
        from gideon.workspace import notification_kinds

        if getattr(self.runtime, "_budget_notified", False):
            return
        self.runtime._budget_notified = True
        if self.runtime.dashboard_state is not None:
            try:
                self.runtime.dashboard_state.notify(
                    notification_kinds.WARNING,
                    "Daily automation budget reached",
                    f"{context} was skipped — {reason}. Unattended runs resume "
                    "tomorrow, or raise the budget in Settings → Guardrails.",
                )
            except Exception:
                self.logger.debug("budget notify failed", exc_info=True)

    def exceeded(self, context: str) -> bool:
        try:
            from gideon.security.guardrails.budgets import (
                BudgetVerdict,
                budget_from_config,
                get_meter,
            )

            limit = budget_from_config()
            if limit.is_unlimited:
                return False
            verdict, reason = get_meter().check_day(limit)
            if verdict is BudgetVerdict.EXCEEDED:
                self.announce(context, reason)
                self.logger.info("%s skipped: %s", context, reason)
                return True
            self.runtime._budget_notified = False
            return False
        except Exception:
            self.logger.debug("day-budget check failed (fail-open)", exc_info=True)
            return False
