"""Run remediation and converge its adaptive system clock from measured health."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)
REMEDIATION_TRIGGER_ID = "system:self-remediation"
PROVIDER_NAME = "self-remediation"


@dataclass(frozen=True)
class _AdaptiveCadence:
    healthy: int
    degraded: int

    @classmethod
    def configured(cls, cfg: Any) -> _AdaptiveCadence:
        values = [
            max(1, int(getattr(cfg, name, fallback) or fallback)) * 60
            for name, fallback in (
                ("idle_minutes_healthy", 60),
                ("tick_minutes_degraded", 5),
            )
        ]
        return cls(*values)

    def update(self, trigger: Any, healthy: bool | None = None) -> None:
        merged = dict(trigger.spec or {})
        merged.update(
            kind="adaptive",
            interval_secs_healthy=self.healthy,
            interval_secs_degraded=self.degraded,
        )
        if healthy is None:
            merged.setdefault("health_state", "healthy")
        else:
            merged["health_state"] = "healthy" if healthy else "degraded"
        trigger.spec = merged

    def label(self, healthy: bool) -> str:
        interval = self.healthy if healthy else self.degraded
        return f"{max(1, round(interval / 60))}m"


@dataclass(frozen=True)
class _RemediationPass:
    config: Any
    started: float

    def run(self) -> Any:
        from gideon.operations.resilience.remediation import run_remediation

        return run_remediation(
            target_score=float(self.config.target_score),
            max_cost_usd=self.config.max_cost_usd,
            now=self.started,
        )


def _pass_result(result: Any, rearmed: str) -> ActionResult:
    failed = tuple(
        str(job.get("id")) for job in result.jobs if job.get("status") == "error"
    )
    summary = (
        f"score {result.score_before:.0f}→{result.score_after:.0f} "
        f"({result.stopped_reason or 'no plan'}); "
        f"{len(result.jobs)} job(s); next in {rearmed or 'unchanged'}"
    )
    return ActionResult(
        not failed,
        exit_code=int(bool(failed)),
        stdout=summary,
        error="remediation job(s) failed: " + ", ".join(failed) if failed else "",
    )


class SelfRemediationActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Self-Remediation"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.core.config.loader import AppConfig
        from gideon.operations.resilience import remediation

        try:
            config = AppConfig.load().resilience.remediation
        except Exception as exc:
            return ActionResult(False, error=f"remediation config unreadable: {exc}")
        if not config.enabled:
            return ActionResult(True, stdout="remediation: disabled by config")
        job = _RemediationPass(config, time.time())
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, job.run)
        except Exception as exc:
            logger.warning("remediation run failed", exc_info=True)
            return ActionResult(False, error=f"remediation failed: {exc}")
        clock = _rearm(
            healthy=result.score_after >= remediation.HEALTHY_SCORE,
            cfg=config,
            now=job.started,
        )
        return _pass_result(result, clock)


def create_provider(
    config: dict[str, Any] | None = None,
) -> SelfRemediationActionProvider:
    return SelfRemediationActionProvider()


def _cadence_secs(cfg: Any) -> tuple[int, int]:
    cadence = _AdaptiveCadence.configured(cfg)
    return cadence.healthy, cadence.degraded


def _persist_clock(store: Any, trigger: Any, now: float | None = None) -> None:
    from gideon.automation.triggers.arm import arm

    scheduled = arm(trigger) if now is None else arm(trigger, now=now)
    if scheduled:
        trigger.next_fire_at = scheduled
    store.upsert(trigger)


def _rearm(*, healthy: bool, cfg: Any, now: float) -> str:
    try:
        from gideon.automation.triggers.store import TriggerStore
        from gideon.core.config.loader import config_dir

        store = TriggerStore(base_dir=config_dir())
        row = store.get(REMEDIATION_TRIGGER_ID)
        if row is None:
            return ""
        cadence = _AdaptiveCadence.configured(cfg)
        cadence.update(row.trigger, healthy)
        _persist_clock(store, row.trigger, now)
        return cadence.label(healthy)
    except Exception:
        logger.debug("could not re-arm the remediation trigger", exc_info=True)
        return ""


def reconcile_remediation_trigger(store: Any) -> None:
    from gideon.automation.triggers import singletons
    from gideon.automation.triggers.models import Trigger
    from gideon.automation.triggers.screen import capabilities_for_action
    from gideon.core.config.loader import AppConfig

    singletons.converge(store, singletons.SELF_REMEDIATION)
    try:
        config = AppConfig.load().resilience.remediation
    except Exception:
        logger.debug("remediation trigger: config unreadable", exc_info=True)
        return
    try:
        row = store.get(REMEDIATION_TRIGGER_ID)
    except Exception:
        logger.debug(
            "remediation trigger: could not read the trigger store", exc_info=True
        )
        return
    try:
        if row is None:
            trigger = Trigger(
                id=REMEDIATION_TRIGGER_ID,
                name="Self-remediation",
                kind="clock",
                created_by="system",
                delivery="inbox",
            )
        else:
            trigger = row.trigger
        trigger.purpose = singletons.SELF_REMEDIATION
        cadence = _AdaptiveCadence.configured(config)
        cadence.update(trigger)
        trigger.enabled = bool(config.enabled)
        trigger.workflow = {"inline": {"provider": PROVIDER_NAME, "config": {}}}
        trigger.capabilities = capabilities_for_action(trigger)
        _persist_clock(store, trigger)
        if row is None:
            logger.info(
                "registered the self-remediation trigger (healthy %ds / degraded %ds)",
                cadence.healthy,
                cadence.degraded,
            )
    except Exception:
        logger.warning("remediation trigger: registration failed", exc_info=True)
