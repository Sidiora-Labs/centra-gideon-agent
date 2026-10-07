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
            from gideon.automation.triggers.legacy_import import import_legacy

            report = import_legacy(self.root, store)
        except Exception:
            logger.warning(
                "legacy trigger import failed; leaving the trigger store as-is",
                exc_info=True,
            )
            return self.failure("migration raised")
        armed = arm_unarmed(store, now=self.now)
        result: dict = {
            key: report.get(key, []) for key in ("imported", "retired", "pending")
        }
        result.update(
            ok=not bool(report.get("pending")),
            armed=armed,
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


def verify_report(base_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        from gideon.automation.triggers.verify import verify_home

        result = verify_home(base_dir)
        return result.to_dict()
    except Exception:
        logger.debug("verify-migration could not run at boot", exc_info=True)
        return {}


def _log_report(report: dict[str, Any]) -> None:
    imported = report.get("imported") or []
    retired = report.get("retired") or []
    armed = report.get("armed") or []
    if imported or armed:
        logger.info(
            "legacy triggers: %d imported, %d sources retired, %d armed",
            len(imported),
            len(retired),
            len(armed),
        )
    else:
        logger.debug("legacy trigger migration: nothing to do")
