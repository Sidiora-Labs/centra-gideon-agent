"""Monthly usage notification delivery with durable per-month bookkeeping."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.automation.triggers import singletons
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.report_clock import ReportClock
from gideon.integrations.action_providers.services import get_action_services

logger = logging.getLogger(__name__)
USAGE_RECAP_JOB_NAME = "system:usage-recap"
USAGE_RECAP_SCHEDULE = "0 9 1 * *"
_MARK_FILE = "usage_recap_sent.json"
_CLOCK = ReportClock(
    USAGE_RECAP_JOB_NAME, USAGE_RECAP_JOB_NAME, "usage-recap", singletons.USAGE_RECAP
)


def previous_month(now: datetime | None = None) -> str:
    reference = now or datetime.now(tz=timezone.utc)
    year, offset = divmod(reference.year * 12 + reference.month - 2, 12)
    return f"{year:04d}-{offset + 1:02d}"


def _mark_path(home: Path) -> Path:
    return Path(home).joinpath(_MARK_FILE)


@dataclass(frozen=True)
class _RecapLedger:
    path: Path

    def read(self) -> dict[str, Any]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return document if isinstance(document, dict) else {}

    def record(self, month: str, delivered: bool) -> None:
        from gideon.core.atomic_write import atomic_write

        document = self.read() | {
            "last_month": month,
            "last_at": datetime.now(tz=timezone.utc).isoformat(),
            "delivered": bool(delivered),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.path, json.dumps(document, indent=2) + "\n")
        except OSError:
            logger.warning(
                "usage recap: could not persist the once-per-month mark", exc_info=True
            )


def read_mark(home: Path) -> dict[str, Any]:
    return _RecapLedger(_mark_path(home)).read()


def _write_mark(home: Path, month: str, *, delivered: bool) -> None:
    _RecapLedger(_mark_path(home)).record(month, delivered)


@dataclass(frozen=True)
class _RecapDispatch:
    home: Path
    month: str

    def deliver(self, state: Any) -> ActionResult:
        from gideon.engine.routing import usage
        from gideon.workspace.notification_kinds import USAGE_RECAP

        try:
            body = usage.usage_recap(self.month, home=self.home)
        except Exception as exc:
            return ActionResult(False, error=f"usage recap: render failed: {exc}")
        try:
            state.notify(
                USAGE_RECAP,
                f"Usage recap — {self.month}",
                body,
                meta={"month": self.month},
            )
        except Exception as exc:
            return ActionResult(False, error=f"usage recap: delivery failed: {exc}")
        _write_mark(self.home, self.month, delivered=True)
        return ActionResult(True, stdout=f"usage recap: emitted for {self.month}")


class UsageRecapActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "usage-recap"

    @property
    def display_name(self) -> str:
        return "Monthly Usage Recap"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.core.config.loader import config_dir

        request = _RecapDispatch(
            Path(config_dir()),
            str((action_config or {}).get("month") or "").strip() or previous_month(),
        )
        if read_mark(request.home).get("last_month") == request.month:
            return ActionResult(
                True, stdout=f"usage recap: {request.month} already sent"
            )
        state = getattr(get_action_services(), "state", None)
        if state is None:
            return ActionResult(
                False, error="usage recap: no dashboard state to notify"
            )
        return request.deliver(state)


def create_provider(config: dict[str, Any] | None = None) -> UsageRecapActionProvider:
    return UsageRecapActionProvider()


def reconcile_usage_recap_cron(store: Any) -> None:
    from gideon.automation.triggers.screen import capabilities_for_action

    _CLOCK.converge(store)
    try:
        row = store.get(USAGE_RECAP_JOB_NAME)
    except Exception:
        logger.debug(
            "usage-recap cron: could not read the trigger store", exc_info=True
        )
        return
    try:
        revision = _CLOCK.plan(row, USAGE_RECAP_SCHEDULE, policy="create")
        if revision is None:
            return
        revision.persist(store, capabilities_for_action)
        logger.info(
            "registered the monthly usage-recap trigger (%s)", USAGE_RECAP_SCHEDULE
        )
    except Exception:
        logger.warning("usage-recap cron: registration failed", exc_info=True)
