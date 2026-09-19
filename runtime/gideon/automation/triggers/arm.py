"""Clock cadence, excluded calendar days and authoring diagnostics."""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gideon.automation.triggers.models import Issue

logger = logging.getLogger(__name__)
MAX_SKIP_ADVANCE = 400
_CADENCE_SAMPLE_FIRES = 8
_MAX_SKIP_DATES_CHECKED = 64
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _mapping(trigger: Any, name: str) -> dict:
    value = getattr(trigger, name, None)
    return value if isinstance(value, dict) else {}


def _trigger_tz(trigger: Any) -> Any:
    from gideon.core.timezones import resolve_zone

    return resolve_zone(str(_mapping(trigger, "spec").get("timezone") or "").strip())


def _skipped_dates(trigger: Any) -> set[str]:
    entries = [
        entry
        for section in ("spec", "gates")
        for entry in list(_mapping(trigger, section).get("skip_dates") or [])
    ]
    return {str(entry).strip() for entry in entries if str(entry).strip()}


def _positive(value: Any) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        converted = 0.0
    return converted if converted > 0 else 0.0


def _created_at(trigger: Any) -> float:
    return _positive(_mapping(trigger, "spec").get("created_at"))


class ClockCadence:
    def __init__(self, trigger: Any, now: float, last_fire: float):
        self.trigger, self.now, self.last_fire = trigger, now, last_fire
        self.spec = _mapping(trigger, "spec")

    def cron(self) -> float:
        from croniter import croniter

        expression = str(self.spec.get("expr") or "").strip()
        if not expression:
            return 0.0
        if not croniter.is_valid(expression):
            logger.debug(
                "trigger %s has an invalid cron expr %r", self.trigger.id, expression
            )
            return 0.0
        origin = datetime.fromtimestamp(self.now, tz=_trigger_tz(self.trigger))
        return float(croniter(expression, origin).get_next(float))

    def interval(self) -> float:
        seconds = _positive(self.spec.get("interval_secs"))
        if seconds <= 0:
            return 0.0
        anchor = self.last_fire or _created_at(self.trigger) or self.now
        completed_slots = int(max(0.0, self.now - anchor) // seconds)
        return anchor + (completed_slots + 1) * seconds

    def adaptive(self) -> float:
        degraded = (
            str(self.spec.get("health_state") or "").strip().lower() == "degraded"
        )
        field = "interval_secs_degraded" if degraded else "interval_secs_healthy"
        delay = _positive(self.spec.get(field))
        return self.now + delay if delay > 0 else 0.0

    def once(self) -> float:
        instant = _positive(self.spec.get("at"))
        return instant if instant > self.now else 0.0

    def calculate(self) -> float:
        key = str(self.spec.get("kind") or "").strip().lower()
        policies = {
            "cron": self.cron,
            "interval": self.interval,
            "sequence": self.interval,
            "adaptive": self.adaptive,
            "at": self.once,
        }
        policy = policies.get(key)
        return policy() if policy else 0.0


def cadence_next_fire(
    trigger: Any, *, now: float = 0.0, last_fire: float = 0.0
) -> float:
    instant = now or time.time()
    if getattr(trigger, "kind", "") == "clock" and getattr(trigger, "enabled", False):
        try:
            return ClockCadence(trigger, instant, last_fire).calculate()
        except Exception:
            logger.warning(
                "could not compute next fire for %s",
                getattr(trigger, "id", "?"),
                exc_info=True,
            )
    return 0.0


class CalendarExclusions:
    def __init__(self, trigger: Any):
        self.trigger = trigger
        self.skips = _skipped_dates(trigger)

    def contains(self, instant: float, zone) -> bool:
        return (
            datetime.fromtimestamp(instant, tz=zone).strftime("%Y-%m-%d") in self.skips
        )

    def advance(self, initial: float) -> float:
        if not self.skips or initial <= 0:
            return initial
        zone = _trigger_tz(self.trigger)
        candidate = initial
        for _ in range(MAX_SKIP_ADVANCE):
            if not self.contains(candidate, zone):
                return candidate
            following = cadence_next_fire(
                self.trigger, now=candidate + 1.0, last_fire=candidate
            )
            if following <= candidate:
                return 0.0
            candidate = following
        logger.warning(
            "trigger %s: every candidate fire within %d steps is a skipped date",
            getattr(self.trigger, "id", "?"),
            MAX_SKIP_ADVANCE,
        )
        return 0.0


def next_fire(trigger: Any, *, now: float = 0.0, last_fire: float = 0.0) -> float:
    from gideon.core.timezones import UnknownTimeZone

    instant = now or time.time()
    try:
        _trigger_tz(trigger)
    except UnknownTimeZone as failure:
        logger.warning(
            "trigger %s will not arm: %s",
            getattr(trigger, "id", "?"),
            failure,
            exc_info=False,
        )
        return 0.0
    exclusions = CalendarExclusions(trigger)
    raw = cadence_next_fire(trigger, now=instant, last_fire=last_fire)
    return apply_jitter(trigger, exclusions.advance(raw))


def apply_jitter(trigger: Any, fire: float) -> float:
    spec = _mapping(trigger, "spec")
    if fire <= 0 or bool(spec.get("strict")):
        return fire
    window = _positive(spec.get("jitter_secs"))
    if window <= 0:
        return fire
    from gideon.automation.triggers.scheduling import jitter_offset

    shifted = fire + jitter_offset(str(getattr(trigger, "id", "") or ""), window)
    exclusions = CalendarExclusions(trigger)
    if exclusions.skips and exclusions.contains(shifted, _trigger_tz(trigger)):
        return fire
    return shifted


def arm(trigger: Any, *, now: float = 0.0, last_fire: float = 0.0) -> str:
    from gideon.automation.triggers.service import to_iso

    epoch = next_fire(trigger, now=now, last_fire=last_fire)
    return "" if epoch <= 0 else to_iso(epoch)


def needs_arming(trigger: Any) -> bool:
    if getattr(trigger, "kind", "") != "clock" or not getattr(
        trigger, "enabled", False
    ):
        return False
    return not str(getattr(trigger, "next_fire_at", "") or "").strip()


def _min_cron_gap_secs(expr: str) -> float:
    from croniter import croniter

    try:
        cursor = croniter(expr, datetime(2026, 1, 6, tzinfo=timezone.utc))
        samples = [cursor.get_next(float) for _ in range(_CADENCE_SAMPLE_FIRES)]
    except Exception:
        return 0.0
    return min(
        (later - earlier for earlier, later in pairwise(samples) if later > earlier),
        default=0.0,
    )


def _cron_fires_on_date(expr: str, day: date, tz_name: str) -> bool:
    from croniter import croniter

    from gideon.core.timezones import UnknownTimeZone, resolve_zone

    try:
        zone = resolve_zone(tz_name)
    except UnknownTimeZone:
        return True
    try:
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=zone)
        first = croniter(expr, midnight - timedelta(seconds=1)).get_next(datetime)
    except Exception:
        return True
    return first.astimezone(zone).strftime("%Y-%m-%d") == day.isoformat()


class ClockDiagnostics:
    def __init__(self, specification: dict[str, Any]):
        self.spec = specification
        self.issues: list[Issue] = []
        self.expression = str(specification.get("expr", "") or "").strip()

    def add(self, path: str, message: str, severity: str = "warning") -> None:
        from gideon.automation.triggers.models import Issue

        self.issues.append(Issue(path=path, message=message, severity=severity))

    def timezone(self) -> None:
        from gideon.core.timezones import is_known_zone

        name = str(self.spec.get("timezone", "") or "").strip()
        if name and not is_known_zone(name):
            self.add(
                "spec.timezone",
                f"{name!r} is not an IANA timezone name — use one like "
                "'America/Los_Angeles' or 'Europe/London' (abbreviations such as "
                "'PDT' or 'CEST' are not zones). Leave it empty to use this machine's zone",
                "error",
            )

    def cron(self) -> bool:
        from croniter import croniter

        from gideon.automation.triggers.models import MIN_CLOCK_INTERVAL_SECS

        expression = self.expression
        if str(self.spec.get("kind", "") or "") != "cron" or not expression:
            return False
        if not croniter.is_valid(expression):
            self.add(
                "spec.expr",
                f"{expression!r} is not a valid cron expression — the trigger would arm to nothing and never fire",
                "error",
            )
            return False
        count = len(expression.split())
        if not expression.startswith("@") and count != 5:
            self.add(
                "spec.expr",
                f"cron expressions are 5 fields (min hour dom month dow) or an "
                f"@alias; {expression!r} has {count} — a seconds field would fire "
                f"far below the {MIN_CLOCK_INTERVAL_SECS}s cadence floor",
                "error",
            )
            return False
        gap = _min_cron_gap_secs(expression)
        if 0 < gap < MIN_CLOCK_INTERVAL_SECS:
            self.add(
                "spec.expr",
                f"fires every {int(gap)}s at its fastest — below the "
                f"{MIN_CLOCK_INTERVAL_SECS}s floor for an LLM-invoking trigger; it will still run, but confirm this is intended",
            )
        return True

    def skip_dates(self, usable: bool) -> None:
        name = str(self.spec.get("timezone", "") or "")
        for raw in list(self.spec.get("skip_dates") or [])[:_MAX_SKIP_DATES_CHECKED]:
            value = str(raw).strip()
            day = None
            if _ISO_DATE_RE.fullmatch(value):
                try:
                    day = date.fromisoformat(value)
                except ValueError:
                    pass
            if day is None:
                self.add(
                    "spec.skip_dates",
                    f"skip date {value!r} is not a real YYYY-MM-DD date — the fire "
                    "path matches dates as YYYY-MM-DD strings, so this entry can never suppress a fire",
                    "error",
                )
            elif usable and not _cron_fires_on_date(self.expression, day, name):
                self.add(
                    "spec.skip_dates",
                    f"the schedule never fires on {value} ({day.strftime('%A')}), "
                    "so this skip date is inert — check the date or the cron",
                )

    def collect(self) -> list[Any]:
        self.timezone()
        usable = self.cron()
        self.skip_dates(usable)
        return self.issues


def semantic_spec_issues(kind: str, spec: dict[str, Any] | None) -> list[Any]:
    return (
        ClockDiagnostics(spec).collect()
        if kind == "clock" and isinstance(spec, dict)
        else []
    )
