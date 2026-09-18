from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any

from gideon.automation.schedule_history import ExecutionJournal

RECONCILERS = (
    (
        "gideon.extensions.apps.app_crons",
        "reconcile_app_crons",
        "app-cron reconcile failed",
    ),
    (
        "gideon.integrations.action_providers.digest_provider",
        "reconcile_digest_cron",
        "digest-cron reconcile failed",
    ),
    ("gideon.assurance.selfqa.install", "reconcile", "selfqa-watch reconcile failed"),
    (
        "gideon.integrations.action_providers.usage_recap_provider",
        "reconcile_usage_recap_cron",
        "usage-recap-cron reconcile failed",
    ),
    (
        "gideon.integrations.action_providers.source_digest_provider",
        "reconcile_source_digest_cron",
        "source-digest-cron reconcile failed",
    ),
    (
        "gideon.integrations.action_providers.identity_report_provider",
        "reconcile_identity_report_trigger",
        "identity-report trigger reconcile failed",
    ),
    (
        "gideon.integrations.action_providers.remediation_provider",
        "reconcile_remediation_trigger",
        "self-remediation trigger reconcile failed",
    ),
)


class AutomationBoot:
    def __init__(self, runtime: Any, *, home: Any, logger: Any) -> None:
        self.runtime = runtime
        self.home = home
        self.logger = logger

    async def rotate(self) -> None:
        try:
            await ExecutionJournal(self.home()).rotate_all()
        except Exception:
            self.logger.debug("Run-history rotation at boot failed", exc_info=True)

    def migrate(self) -> None:
        try:
            from gideon.automation.triggers.boot_migrate import migrate_and_arm

            migrate_and_arm()
        except Exception:
            self.logger.warning("trigger-store migration failed at boot", exc_info=True)

    def converge(self, store: Any) -> None:
        """Collapse duplicate copies of every built-in singleton before the seeders run.

        The seeding sites converge their own purpose, but not all of them run at boot — the
        triage digest is installed from the dashboard — so a home holding twins of one of
        those would keep firing both until someone opened the page. One pass here covers the
        whole registry; it is idempotent, so the per-site calls stay the authority.
        """
        try:
            from gideon.automation.triggers.singletons import converge_all

            converge_all(store)
        except Exception:
            self.logger.warning(
                "built-in singleton convergence failed at boot", exc_info=True
            )

    def reconcile(self, store: Any) -> None:
        self.converge(store)
        for module, entry, failure in RECONCILERS:
            try:
                getattr(import_module(module), entry)(store)
            except Exception:
                self.logger.warning(failure, exc_info=True)

    def recover(self, store: Any) -> None:
        try:
            from gideon.automation.triggers.service import boot

            report = boot(store)
            rearmed = len(report.get("rearmed") or [])
            total = int(report.get("total", 0) or 0)
            missed = len((report.get("review") or {}).get("rows") or [])
            self.logger.info(
                "trigger boot sweep: re-armed %d of %d, %d missed slots to review",
                rearmed,
                total,
                missed,
            )
            self.runtime._surface_missed_review(report)
        except Exception:
            self.logger.warning("trigger boot sweep failed", exc_info=True)

    async def start(self) -> None:
        if self.runtime._no_crons:
            self.logger.info("Automations disabled (--no-crons)")
            return
        await self.rotate()
        self.runtime._file_watch_task = asyncio.create_task(
            self.runtime._file_watch_poll_loop()
        )
        self.runtime._web_watch_task = asyncio.create_task(
            self.runtime._web_watch_poll_loop()
        )
        self.migrate()
        from gideon.automation.triggers.store import TriggerStore

        store = TriggerStore(base_dir=self.home())
        self.reconcile(store)
        self.recover(store)
        self.runtime._clock_task = asyncio.create_task(self.runtime._clock_loop())
        self.runtime._reaper_task = asyncio.create_task(
            self.runtime._trigger_reaper_loop()
        )
