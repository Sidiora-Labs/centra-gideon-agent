from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
_CHANNEL_PREFIX = "channel:"
_PINNED_PREFIX = "pinned:"


@dataclass
class AutomationCounts:
    enabled: dict[str, bool] = field(default_factory=dict)
    broken: set[str] = field(default_factory=set)

    def load(self, store: Any, legacy: Any) -> dict[str, int]:
        for row in store.load():
            self.enabled[row.trigger.id] = bool(row.trigger.enabled)
            if not row.ok:
                self.broken.add(row.trigger.id)
        if legacy is not None:
            try:
                for job in legacy.list_jobs(include_disabled=True):
                    if job.id not in self.enabled:
                        self.enabled[job.id] = bool(job.enabled)
            except Exception:
                logger.debug("legacy job counts unavailable", exc_info=True)
        return dict(
            total=len(self.enabled),
            enabled=sum(self.enabled.values()),
            broken=len(self.broken),
        )


def counts(store: Any, *, legacy: Any = None) -> dict[str, int]:
    return AutomationCounts().load(store, legacy)


def _section(trigger: Any, key: str) -> dict[str, Any]:
    value = getattr(trigger, key, None)
    return value if isinstance(value, dict) else {}


def _inline_action(trigger: Any) -> dict[str, Any]:
    workflow = _section(trigger, "workflow")
    nested = workflow.get("inline")
    if isinstance(nested, dict):
        return nested
    if not workflow.get("provider"):
        return {}
    return dict(provider=workflow.get("provider"), config=workflow.get("config") or {})


def _action_config(trigger: Any) -> dict[str, Any]:
    value = _inline_action(trigger).get("config")
    return value if isinstance(value, dict) else {}


def _prefixed(trigger: Any, field: str, prefix: str) -> str:
    value = str(getattr(trigger, field, "") or "")
    return value.removeprefix(prefix) if value.startswith(prefix) else ""


def channel_of(trigger: Any) -> str:
    return _prefixed(trigger, "delivery", _CHANNEL_PREFIX)


def is_silent(trigger: Any) -> bool:
    return str(getattr(trigger, "delivery", "") or "") == "none"


def session_key_of(trigger: Any) -> str:
    return _prefixed(trigger, "session", _PINNED_PREFIX)


def cadence_kind(trigger: Any) -> str:
    return str(_section(trigger, "spec").get("kind") or "")


@dataclass(frozen=True)
class ScheduleProjection:
    trigger: Any
    now: float
    base_dir: Any
    last_run_status: str

    def status_fields(self) -> dict[str, Any]:
        from gideon.automation.triggers import claims

        trigger = self.trigger
        return {
            "health": str(getattr(trigger, "health_status", "") or ""),
            "state": str(getattr(trigger, "state", "") or ""),
            "last_status": str(getattr(trigger, "health_status", "") or ""),
            "last_run_status": self.last_run_status or None,
            "last_run_ts": _last_run_ts(trigger),
            "has_result": bool(getattr(trigger, "last_run_id", "")),
            "last_result": None,
            "last_error": str(getattr(trigger, "last_error_summary", "") or "") or None,
            "next_run_ts": _next_run_ts(trigger, now=self.now),
            "is_running": claims.is_running(
                trigger.id, now=self.now, base_dir=self.base_dir
            ),
            "running_since": claims.running_since(
                trigger.id, now=self.now, base_dir=self.base_dir
            ),
            "run_count": int(getattr(trigger, "run_count", 0) or 0),
            "next_fire_at": str(getattr(trigger, "next_fire_at", "") or ""),
        }

    def cadence_fields(self) -> dict[str, Any]:
        spec = _section(self.trigger, "spec")
        kind = cadence_kind(self.trigger)
        return {
            "schedule": describe_cadence(self.trigger),
            "cron_expr": str(spec.get("expr") or "") if kind == "cron" else None,
            "every_secs": (
                _int_or_none(spec.get("interval_secs")) if kind == "interval" else None
            ),
            "strict_schedule": bool(spec.get("strict", False)),
            "timezone": str(spec.get("timezone") or "") or None,
            "skip_dates": list(spec.get("skip_dates") or []),
        }

    def document(self) -> dict[str, Any]:
        trigger = self.trigger
        action = _inline_action(trigger)
        config = _action_config(trigger)
        fields: dict = {
            name: str(config.get(name) or "") or None
            for name in ("agent", "model", "approval_mode", "script", "command")
        }
        fields.update(
            kind="schedule",
            id=f"schedule:{trigger.id}",
            raw_id=trigger.id,
            name=trigger.name,
            enabled=bool(trigger.enabled),
            action=action,
            message=str(config.get("task_template") or config.get("message") or ""),
            created_ts=None,
            channel=channel_of(trigger) or None,
            silent=is_silent(trigger),
            has_session=bool(session_key_of(trigger)),
        )
        fields.update(self.cadence_fields())
        fields.update(self.status_fields())
        return fields


def to_schedule_row(
    trigger: Any, *, now: float = 0.0, base_dir: Any = None, last_run_status: str = ""
) -> dict[str, Any]:
    return ScheduleProjection(trigger, now, base_dir, last_run_status).document()


def _int_or_none(value: Any) -> int | None:
    try:
        number = float(value)
        return int(number)
    except (TypeError, ValueError):
        return None


def _last_run_ts(trigger: Any) -> float | None:
    from gideon.automation.triggers.service import to_epoch

    newest = max(
        to_epoch(getattr(trigger, field, ""))
        for field in ("last_success_at", "last_failure_at")
    )
    return newest if newest > 0 else None


def _next_run_ts(trigger: Any, *, now: float) -> float | None:
    from gideon.automation.triggers.arm import next_fire
    from gideon.automation.triggers.service import to_epoch

    persisted = to_epoch(getattr(trigger, "next_fire_at", ""))
    stamp = persisted if persisted > 0 else next_fire(trigger, now=now)
    return stamp if stamp > 0 else None


@dataclass(frozen=True)
class CadenceDescription:
    spec: dict[str, Any]
    kind: str

    def format(self) -> str:
        from gideon.automation.schedule import ScheduleDefinition, format_schedule

        if self.kind == "adaptive":
            return _describe_adaptive(self.spec)
        builders = {
            "cron": lambda: ScheduleDefinition(
                kind="cron", cron_expr=str(self.spec.get("expr") or "")
            ),
            "interval": lambda: ScheduleDefinition(
                kind="every", every_secs=_int_or_none(self.spec.get("interval_secs"))
            ),
            "sequence": lambda: ScheduleDefinition(
                kind="every", every_secs=_int_or_none(self.spec.get("interval_secs"))
            ),
            "at": lambda: ScheduleDefinition(
                kind="at", at_ts=_float_or_none(self.spec.get("at"))
            ),
        }
        build = builders.get(self.kind)
        if build is None:
            return self.kind or "unknown"
        return format_schedule(build(), tz_name=str(self.spec.get("timezone") or ""))


def describe_cadence(trigger: Any) -> str:
    description = CadenceDescription(_section(trigger, "spec"), cadence_kind(trigger))
    try:
        return description.format()
    except Exception:
        logger.debug(
            "could not describe cadence for %s",
            getattr(trigger, "id", "?"),
            exc_info=True,
        )
        return description.kind or "unknown"


def _describe_adaptive(spec: dict[str, Any]) -> str:
    intervals = {
        name: _int_or_none(spec.get("interval_secs_" + name))
        for name in ("healthy", "degraded")
    }
    if not all(intervals.values()):
        return "adaptive"
    healthy = intervals["healthy"]
    degraded = intervals["degraded"]
    if healthy is None or degraded is None:
        return "adaptive"
    state = str(spec.get("health_state") or "").strip().lower()
    current = "degraded" if state == "degraded" else "healthy"
    return f"adaptive — every {_mins(healthy)} healthy, {_mins(degraded)} degraded (now: {current})"


def _mins(secs: int) -> str:
    minutes = max(1, round(secs / 60))
    return f"{minutes}m"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
