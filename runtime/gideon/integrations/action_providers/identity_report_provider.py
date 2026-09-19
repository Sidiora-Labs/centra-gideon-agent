"""Cadence admission, shared identity report delivery, and clock convergence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.report_clock import ReportClock
from gideon.integrations.action_providers.services import get_action_services

logger = logging.getLogger(__name__)
IDENTITY_REPORT_TRIGGER_ID = "system:learning-identity-report"
PROVIDER_NAME = "identity-report"
_CADENCE_CRON: dict[str, str] = {"monthly": "0 9 1 * *", "weekly": "0 9 * * 1"}
_CLOCK = ReportClock(IDENTITY_REPORT_TRIGGER_ID, "Identity report", PROVIDER_NAME)


@dataclass(frozen=True)
class _IdentityCadence:
    name: str

    def refusal(self) -> ActionResult | None:
        from gideon.cognition.learning_report import CADENCE_OFF

        if not self.name:
            return ActionResult(
                False, error="identity report: cadence config unreadable"
            )
        if self.name == CADENCE_OFF:
            return ActionResult(True, stdout="identity report: cadence is off")
        return None

    def result(self, delivery: Any) -> ActionResult:
        summary = (
            f"identity report ({self.name}): artifact "
            f"{delivery.artifact_slug or '—'} v{delivery.artifact_version} · "
            f"inbox {delivery.inbox_item_id or '—'}"
        )
        missing = []
        if not delivery.artifact_slug:
            missing.append("artifact")
        if not delivery.inbox_item_id:
            missing.append("inbox item")
        return ActionResult(
            not missing,
            exit_code=int(bool(missing)),
            stdout=summary,
            error=(
                f"identity report: no {' and no '.join(missing)} was written"
                if missing
                else ""
            ),
        )


class IdentityReportActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Identity Report"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.cognition import learning_report

        policy = _IdentityCadence(learning_report.configured_cadence())
        refused = policy.refusal()
        if refused is not None:
            return refused
        state = getattr(get_action_services(), "state", None)
        if state is None:
            return ActionResult(
                False, error="identity report: no dashboard state to deliver through"
            )
        try:
            delivery = await learning_report.deliver_identity_report(
                state,
                window_days=learning_report.cadence_window_days(policy.name),
                vs=_vector_store(state),
                narrate=True,
            )
        except Exception as exc:
            logger.warning("identity report: delivery failed", exc_info=True)
            return ActionResult(False, error=f"identity report: delivery failed: {exc}")
        return policy.result(delivery)


def create_provider(
    config: dict[str, Any] | None = None,
) -> IdentityReportActionProvider:
    return IdentityReportActionProvider()


def _vector_store(state: Any) -> Any:
    builder = getattr(state, "context_builder", None)
    owner, member = (
        (state, "_standalone_memory") if builder is None else (builder, "memory")
    )
    return getattr(getattr(owner, member, None), "vector_store", None)


def reconcile_identity_report_trigger(store: Any) -> None:
    from gideon.automation.triggers.screen import capabilities_for_action
    from gideon.cognition.learning_report import (
        CADENCE_OFF,
        DEFAULT_CADENCE,
        configured_cadence,
    )

    cadence = configured_cadence()
    if not cadence:
        logger.debug("identity-report trigger: cadence unreadable")
        return
    try:
        existing = store.get(IDENTITY_REPORT_TRIGGER_ID)
    except Exception:
        logger.debug(
            "identity-report trigger: could not read the trigger store", exc_info=True
        )
        return
    try:
        expression = _CADENCE_CRON.get(cadence, _CADENCE_CRON[DEFAULT_CADENCE])
        revision = _CLOCK.plan(
            existing, expression, policy="cadence", enabled=cadence != CADENCE_OFF
        )
        if revision is None:
            return
        revision.persist(store, capabilities_for_action)
        if revision.created:
            logger.info("registered the identity-report trigger (%s)", cadence)
    except Exception:
        logger.warning("identity-report trigger: registration failed", exc_info=True)
