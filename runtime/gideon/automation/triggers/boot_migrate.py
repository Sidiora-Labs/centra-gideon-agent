from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)


def config_dir() -> Path:
    return config_loader.config_dir()


@dataclass(frozen=True)
class StartupMigration:
    root: Path | str
    now: float

    def run(self) -> dict[str, Any]:
        from gideon.automation.triggers.store import TriggerStore

        try:
            store = TriggerStore(base_dir=self.root)
        except Exception:
            logger.warning(
                "trigger store unavailable; skipping cron migration", exc_info=True
            )
            return self.failure("store unavailable")
        try:
            report = store.migrate_from_crons()
        except Exception:
            logger.warning(
                "cron migration failed; leaving the trigger store as-is", exc_info=True
            )
            return self.failure("migration raised")
        armed = arm_unarmed(store, now=self.now)
        frozen = backfill_capabilities(store)
        result: dict = {
            key: int(report.get(key, 0) or 0)
            for key in ("converted", "written", "refused")
        }
        result.update(
            ok=bool(report.get("lossless", False)) and not report.get("reason"),
            lossless=bool(report.get("lossless", False)),
            reason=str(report.get("reason", "") or ""),
            armed=armed,
            frozen=frozen,
        )
        _log_report(result)
        return result

    @staticmethod
    def failure(reason: str) -> dict[str, Any]:
        return dict(ok=False, reason=reason, converted=0, armed=[])


def migrate_and_arm(
    base_dir: Path | str | None = None, *, now: float = 0.0
) -> dict[str, Any]:
    return StartupMigration(config_dir() if base_dir is None else base_dir, now).run()


@dataclass(frozen=True)
class ExistingTriggerUpdates:
    store: Any

    def apply(
        self,
        field: str,
        eligible: Callable[[Any], bool],
        value_for: Callable[[Any], Any],
    ) -> list[str]:
        changed = []
        for row in self.store.load():
            trigger = row.trigger
            if not getattr(row, "ok", True) or not eligible(trigger):
                continue
            value = value_for(trigger)
            if value:
                setattr(trigger, field, value)
                self.store.upsert(trigger)
                changed.append(trigger.id)
        return changed


def arm_unarmed(store: Any, *, now: float = 0.0) -> list[str]:
    from gideon.automation.triggers.arm import arm, needs_arming

    return ExistingTriggerUpdates(store).apply(
        "next_fire_at", needs_arming, lambda trigger: arm(trigger, now=now)
    )


def backfill_capabilities(store: Any) -> list[str]:
    from gideon.automation.triggers import screen

    return ExistingTriggerUpdates(store).apply(
        "capabilities",
        lambda trigger: not trigger.capabilities,
        screen.capabilities_for_action,
    )


def verify_report(base_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        from gideon.automation.triggers.verify import verify_home

        result = verify_home(base_dir)
        return result.to_dict()
    except Exception:
        logger.debug("verify-migration could not run at boot", exc_info=True)
        return {}


def _log_report(report: dict[str, Any]) -> None:
    frozen = report.get("frozen") or []
    if frozen:
        preview = ", ".join(frozen[:5]) + ("…" if len(frozen) > 5 else "")
        logger.info(
            "trigger capability backfill: froze %d automation(s) to their current action (%s)",
            len(frozen),
            preview,
        )
    if report.get("reason") == "no crons.json":
        logger.debug("no crons.json to migrate")
        return
    converted, written = (report.get(key, 0) for key in ("converted", "written"))
    armed = report.get("armed") or []
    if converted or armed:
        suffix = (
            ""
            if report.get("lossless", True)
            else " (NOT lossless — run `gideon automation verify-migration`)"
        )
        logger.info(
            "cron migration: %d converted, %d written, %d armed%s",
            converted,
            written,
            len(armed),
            suffix,
        )
    else:
        logger.debug("cron migration: nothing to do")
