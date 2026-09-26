from __future__ import annotations

import asyncio
import os
import time
from importlib import import_module
from typing import Any

from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord

RECONCILERS = (
    (
        "gideon.extensions.apps.app_hooks",
        "reconcile_app_hooks",
        "app-hook reconcile failed",
    ),
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


def _owner_provably_dead(owner_pid: Any) -> bool:
    try:
        pid = int(owner_pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError, OverflowError):
        return False
    return False


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

    def reconcile(self, store: Any) -> None:
        for module, entry, failure in RECONCILERS:
            try:
                getattr(import_module(module), entry)(store)
            except Exception:
                self.logger.warning(failure, exc_info=True)

    async def recover_interrupted(self, store: Any) -> list[str]:
        """Resolve claims only when their persisted process owner is provably gone."""
        from gideon.automation.triggers import claims
        from gideon.automation.triggers.models import TriggerHealth
        from gideon.automation.triggers.service import to_iso

        interrupted: list[str] = []
        observed_at = time.time()
        base_dir = getattr(store, "base_dir", self.home())
        journal = ExecutionJournal(self.home())
        for trigger_id in claims.running_ids(now=observed_at, base_dir=base_dir):
            try:
                stored = store.get(trigger_id)
                if stored is None:
                    continue
                trigger = stored.trigger
                owner_pid = int(getattr(trigger, "run_owner_pid", 0) or 0)
                if not _owner_provably_dead(owner_pid):
                    continue
                claim = claims.read_claim(
                    trigger_id, now=observed_at, base_dir=base_dir
                )
                if claim is None or not claims.release_claim(
                    trigger_id, base_dir=base_dir
                ):
                    continue
                reason = (
                    f"interrupted at boot: owner PID {owner_pid} is no longer running"
                )
                run_id = f"interrupted-{owner_pid}-{int(observed_at * 1000)}"
                trigger.last_run_id = run_id
                trigger.run_owner_pid = 0
                trigger.health_status = TriggerHealth.DEGRADED.value
                trigger.last_failure_at = to_iso(observed_at)
                trigger.last_error_summary = reason
                store.upsert(trigger)
                await journal.append(
                    ExecutionRecord(
                        run_id=run_id,
                        job_id=trigger_id,
                        trigger="interrupted",
                        started_at=claim.claimed_at,
                        finished_at=observed_at,
                        duration_ms=int(
                            max(0.0, observed_at - claim.claimed_at) * 1000
                        ),
                        status="failure",
                        error=reason,
                    )
                )
                interrupted.append(trigger_id)
            except Exception:
                self.logger.warning(
                    "trigger %s: interrupted-run recovery failed",
                    trigger_id,
                    exc_info=True,
                )
        return interrupted

    async def recover(self, store: Any) -> None:
        try:
            from gideon.automation.triggers.service import boot

            interrupted = await self.recover_interrupted(store)
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
            if interrupted:
                self.logger.warning(
                    "trigger boot sweep: marked %d interrupted run(s): %s",
                    len(interrupted),
                    ", ".join(interrupted),
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
        await self.recover(store)
        self.runtime._clock_task = asyncio.create_task(self.runtime._clock_loop())
        self.runtime._reaper_task = asyncio.create_task(
            self.runtime._trigger_reaper_loop()
        )
