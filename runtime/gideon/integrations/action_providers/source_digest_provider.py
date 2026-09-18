"""Deliver the watched-source digest through its shared engine and system clock."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
SOURCE_DIGEST_JOB_NAME = "system:source-digest"
SOURCE_DIGEST_SCHEDULE = "0 7 * * *"
_CLOCK = ReportClock(
    SOURCE_DIGEST_JOB_NAME,
    SOURCE_DIGEST_JOB_NAME,
    "source-digest",
    singletons.SOURCE_DIGEST,
)


def _source_result(result: Any) -> ActionResult:
    message = result.skipped_reason or (
        f"created {result.item_id} from {result.item_count} items (notified={result.notified})"
    )
    return ActionResult(True, stdout=f"source digest: {message}")


@dataclass(frozen=True)
class _SourceDigestRun:
    store: Any
    state: Any

    async def execute(self) -> ActionResult:
        from gideon.cognition.knowledge.source_digest import run_morning_digest

        try:
            result = await run_morning_digest(
                knowledge_store=self.store, state=self.state
            )
        except Exception as exc:
            return ActionResult(False, error=f"source digest failed: {exc}")
        finally:
            try:
                self.store.close()
            except Exception:
                logger.debug("source digest: store close failed", exc_info=True)
        return _source_result(result)


class SourceDigestActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "source-digest"

    @property
    def display_name(self) -> str:
        return "Morning Source Digest"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        state = getattr(get_action_services(), "state", None)
        if state is None:
            return ActionResult(
                False, error="source digest: no dashboard state to notify"
            )
        try:
            store = _open_store()
        except Exception as exc:
            return ActionResult(
                False, error=f"source digest: no knowledge store: {exc}"
            )
        return await _SourceDigestRun(store, state).execute()


def _open_store():
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def create_provider(config: dict[str, Any] | None = None) -> SourceDigestActionProvider:
    return SourceDigestActionProvider()


def reconcile_source_digest_cron(store: Any) -> None:
    from gideon.automation.triggers.screen import capabilities_for_action

    _CLOCK.converge(store)
    try:
        row = store.get(SOURCE_DIGEST_JOB_NAME)
    except Exception:
        logger.debug(
            "source-digest cron: could not read the trigger store", exc_info=True
        )
        return
    try:
        revision = _CLOCK.plan(row, SOURCE_DIGEST_SCHEDULE, policy="create")
        if revision is None:
            return
        revision.persist(store, capabilities_for_action)
        logger.info(
            "registered the morning source-digest trigger (%s)", SOURCE_DIGEST_SCHEDULE
        )
    except Exception:
        logger.warning("source-digest cron: registration failed", exc_info=True)
