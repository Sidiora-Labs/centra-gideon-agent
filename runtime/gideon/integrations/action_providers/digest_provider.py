"""Deliver queued notifications and reconcile their configured digest schedule."""

from __future__ import annotations

import logging
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.report_clock import ReportClock
from gideon.integrations.action_providers.services import get_action_services

logger = logging.getLogger(__name__)
DIGEST_JOB_NAME = "system:notification-digest"
_CLOCK = ReportClock(DIGEST_JOB_NAME, DIGEST_JOB_NAME, "notification-digest")


class NotificationDigestActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "notification-digest"

    @property
    def display_name(self) -> str:
        return "Notification Digest"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.workspace import notification_rules

        state = getattr(get_action_services(), "state", None)
        try:
            identifier = notification_rules.run_digest(state)
        except Exception as exc:
            return ActionResult(False, error=f"digest failed: {exc}")
        message = f"created {identifier}" if identifier else "nothing queued"
        return ActionResult(True, stdout=f"digest: {message}")


def create_provider(
    config: dict[str, Any] | None = None,
) -> NotificationDigestActionProvider:
    return NotificationDigestActionProvider()


def reconcile_digest_cron(store: Any) -> None:
    from gideon.automation.triggers.screen import capabilities_for_action
    from gideon.workspace.notification_rules import digest_settings

    try:
        expression = digest_settings()["schedule"]
    except Exception:
        logger.debug("digest cron: could not read the schedule", exc_info=True)
        return
    try:
        row = store.get(DIGEST_JOB_NAME)
    except Exception:
        logger.debug("digest cron: could not read the trigger store", exc_info=True)
        return
    try:
        revision = _CLOCK.plan(row, expression, policy="schedule")
        if revision is None:
            return
        revision.persist(store, capabilities_for_action if revision.created else None)
        logger.info(
            "digest cron: %s (%s)",
            "registered" if revision.created else "schedule converged",
            expression,
        )
    except Exception:
        logger.log(
            logging.WARNING if row is None else logging.DEBUG,
            "digest cron: reconciliation failed",
            exc_info=True,
        )
