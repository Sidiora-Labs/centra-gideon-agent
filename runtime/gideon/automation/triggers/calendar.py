"""Calendar policy, bounded occurrence previews and trigger configuration diagnostics."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

DUTY_GATE_TIMEOUT_SECS = 2.0
UNMETERED_CAPS = frozenset({"cost_cap", "idempotency", "threshold"})
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_INDEX = dict(zip(DAYS, range(7)))
DAY_ALIASES = {
    "weekdays": DAYS[:5],
    "weekends": DAYS[5:],
    "daily": DAYS,
    "all": DAYS,
}
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MAX_OCCURRENCES_PER_TRIGGER = 200
BROAD_GLOB_SEGMENTS = 1


class QuietResolution(str, Enum):
    SKIP = "skip"
    CATCH_UP = "catch_up"


class GateOutcome(str, Enum):
    ALLOWED = "allowed"
    QUIET = "quiet"
    OFF_DUTY = "off_duty"
    SKIPPED = "skipped"


def run_budget_for(gates: dict[str, Any] | None) -> Any:
    from gideon.security.guardrails.budgets import Budget

    raw = gates.get("max_cost_usd_per_run") if isinstance(gates, dict) else None
    try:
        amount = float(raw or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    return Budget(max_dollars=amount if amount > 0 else 0.0)


def parse_hhmm(value: str) -> int | None:
    parts = _HHMM_RE.match((value or "").strip())
    return (
        sum(int(part) * scale for part, scale in zip(parts.groups(), (60, 1)))
        if parts
        else None
    )


@dataclass
class QuietWindow:
    start: str
    end: str
    days: tuple[str, ...] = DAYS

    @property
    def valid(self) -> bool:
        return bool(self.days) and MinuteBand.read(self) is not None

    @property
    def wraps(self) -> bool:
        band = MinuteBand.read(self)
        return band is not None and band.start > band.end

    def to_dict(self) -> dict[str, Any]:
        return dict(start=self.start, end=self.end, days=list(self.days))


@dataclass(frozen=True)
class MinuteBand:
    start: int
    end: int
    weekdays: tuple[str, ...]

    @classmethod
    def read(cls, window: QuietWindow) -> MinuteBand | None:
        bounds = (parse_hhmm(window.start), parse_hhmm(window.end))
        if None in bounds or bounds[0] == bounds[1]:
            return None
        start, end = bounds
        if start is None or end is None:
            return None
        return cls(start, end, window.days)

    def includes(self, weekday: int, minute: int) -> bool:
        if self.start < self.end:
            belongs = self.start <= minute < self.end
        else:
            belongs = minute >= self.start or minute < self.end
            weekday -= int(minute < self.end)
        return belongs and DAYS[weekday % 7] in self.weekdays


def _parse_days(raw: Any) -> tuple[tuple[str, ...], list[str]]:
    if raw is None:
        return DAYS, []
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, (list, tuple, set, frozenset)):
        return DAYS, [
            f"days must be a list, got {type(raw).__name__}; treated as every day"
        ]
    selected: set[str] = set()
    issues = []
    for token in values:
        key = str(token).strip().lower()[:9]
        if key in DAY_ALIASES:
            selected.update(DAY_ALIASES[key])
        elif key[:3] in _DAY_INDEX:
            selected.add(key[:3])
        else:
            issues.append(f"unknown day {token!r}")
    if selected:
        return tuple(day for day in DAYS if day in selected), issues
    return DAYS, [*issues, "no valid days; treated as every day"]


@dataclass
class WindowParser:
    windows: list[QuietWindow] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def add(self, index: int, entry: Any) -> None:
        prefix = f"quiet window {index}"
        if not isinstance(entry, dict):
            self.issues.append(f"{prefix} must be an object")
            return
        days, notes = _parse_days(entry.get("days"))
        self.issues.extend(f"{prefix}: {note}" for note in notes)
        window = QuietWindow(
            str(entry.get("start") or ""), str(entry.get("end") or ""), days
        )
        if window.valid:
            self.windows.append(window)
        else:
            self.issues.append(
                f"{prefix} is invalid (start={window.start!r} end={window.end!r}); "
                "expected HH:MM values that differ — dropped rather than applied"
            )

    def read(self, raw: Any) -> tuple[list[QuietWindow], list[str]]:
        if not raw:
            return self.windows, self.issues
        candidates = raw
        if isinstance(raw, dict):
            if isinstance(raw.get("windows"), (list, tuple)):
                candidates = raw["windows"]
            else:
                candidates = [raw] if raw.get("start") or raw.get("end") else []
        if not isinstance(candidates, (list, tuple)):
            self.issues.append(
                f"quiet_hours must be an object or a list, got {type(raw).__name__}"
            )
        else:
            for index, entry in enumerate(candidates):
                self.add(index, entry)
        return self.windows, self.issues


def parse_windows(raw: Any) -> tuple[list[QuietWindow], list[str]]:
    return WindowParser().read(raw)


def resolution_of(raw: Any) -> str:
    candidate = (
        str(raw.get("resolution") or "").strip().lower()
        if isinstance(raw, dict)
        else ""
    )
    return (
        candidate
        if candidate in QuietResolution._value2member_map_
        else QuietResolution.SKIP.value
    )


def in_quiet_window(windows: list[QuietWindow], moment: datetime) -> QuietWindow | None:
    minute = moment.hour * 60 + moment.minute
    for window in windows:
        band = MinuteBand.read(window)
        if band is not None and band.includes(moment.weekday(), minute):
            return window
    return None


def window_closes_at(window: QuietWindow, moment: datetime) -> datetime:
    minute = parse_hhmm(window.end)
    if minute is None:
        return moment
    hour, minute = divmod(minute, 60)
    closing = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return closing + timedelta(days=int(closing <= moment))


@dataclass
class CalendarDecision:
    outcome: str
    reason: str = ""
    catch_up_at: float = 0.0
    window: QuietWindow | None = None

    @property
    def allowed(self) -> bool:
        return GateOutcome.ALLOWED.value == self.outcome

    def to_dict(self) -> dict[str, Any]:
        result: dict = dict(outcome=self.outcome, reason=self.reason)
        if self.catch_up_at:
            result.update(catch_up_at=self.catch_up_at)
        if self.window is not None:
            result.update(window=self.window.to_dict())
        return result


def evaluate_quiet(
    gates: dict[str, Any] | None, moment: datetime
) -> tuple[CalendarDecision, list[str]]:
    setting = (gates or {}).get("quiet_hours")
    windows, issues = parse_windows(setting)
    match = in_quiet_window(windows, moment)
    if match is None:
        return CalendarDecision(GateOutcome.ALLOWED.value), issues
    release = (
        window_closes_at(match, moment).timestamp()
        if resolution_of(setting) == QuietResolution.CATCH_UP.value
        else 0.0
    )
    day_label = "every day" if len(match.days) == 7 else ", ".join(match.days)
    result_label = (
        "the fire will run once the window closes"
        if release
        else "the fire is dropped (resolution: skip)"
    )
    return (
        CalendarDecision(
            GateOutcome.QUIET.value,
            f"quiet hours {match.start}–{match.end} ({day_label}); {result_label}",
            release,
            match,
        ),
        issues,
    )


@dataclass
class DutyVerdict:
    on_duty: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(on_duty=self.on_duty, reason=self.reason)


_DUTY_GATES: dict[str, Callable[[datetime, dict[str, Any]], Awaitable[DutyVerdict]]] = (
    {}
)


def register_duty_gate(
    name: str, fn: Callable[[datetime, dict[str, Any]], Awaitable[DutyVerdict]]
) -> None:
    _DUTY_GATES.update({name: fn})


def duty_gate_names() -> list[str]:
    return sorted(_DUTY_GATES.keys())


def clear_duty_gates() -> None:
    _DUTY_GATES.clear()


async def _manual_gate(now: datetime, config: dict[str, Any]) -> DutyVerdict:
    if bool(config.get("on_duty", True)):
        return DutyVerdict(True)
    return DutyVerdict(False, "the manual duty toggle is set to off-duty")


register_duty_gate("manual", _manual_gate)


@dataclass(frozen=True)
class DutyRequest:
    name: str
    config: dict[str, Any]

    @classmethod
    def read(cls, gates: dict[str, Any] | None) -> DutyRequest | None:
        raw = (gates or {}).get("duty_gate")
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("provider") or "").strip()
        config = raw.get("config")
        return (
            cls(name, dict(config) if isinstance(config, dict) else {})
            if name
            else None
        )

    async def evaluate(self, moment: datetime, timeout: float) -> CalendarDecision:
        allowed = GateOutcome.ALLOWED.value
        provider = _DUTY_GATES.get(self.name)
        if provider is None:
            return CalendarDecision(
                allowed,
                f"duty gate {self.name!r} is not registered; the fire proceeds (this gate fails open)",
            )
        try:
            verdict = await asyncio.wait_for(
                provider(moment, self.config), timeout=max(0.1, timeout)
            )
        except asyncio.TimeoutError:
            return CalendarDecision(
                allowed,
                f"duty gate {self.name!r} did not answer within {timeout:g}s; the fire proceeds",
            )
        except Exception as exc:
            return CalendarDecision(
                allowed,
                f"duty gate {self.name!r} failed ({type(exc).__name__}); the fire proceeds",
            )
        if verdict.on_duty:
            return CalendarDecision(allowed)
        return CalendarDecision(
            GateOutcome.OFF_DUTY.value,
            verdict.reason or f"the {self.name!r} duty gate reports off-duty",
        )


async def evaluate_duty(
    gates: dict[str, Any] | None,
    moment: datetime,
    *,
    timeout: float = DUTY_GATE_TIMEOUT_SECS,
) -> CalendarDecision:
    request = DutyRequest.read(gates)
    return (
        await request.evaluate(moment, timeout)
        if request
        else CalendarDecision(GateOutcome.ALLOWED.value)
    )


@dataclass
class Occurrence:
    trigger_id: str
    trigger_name: str
    at: float
    suppressed_by: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in ("trigger_id", "trigger_name", "at", "suppressed_by", "reason")
        }


def _resolve_zone(tz_name: str):
    from gideon.core.timezones import UnknownTimeZone, resolve_zone

    try:
        return resolve_zone(tz_name)
    except UnknownTimeZone:
        return resolve_zone("")


@dataclass
class OccurrenceProjection:
    trigger_id: str
    trigger_name: str
    windows: list[QuietWindow]
    skipped_dates: set[str]
    zone: Any

    def annotate(self, at: float) -> Occurrence:
        local = datetime.fromtimestamp(at, tz=timezone.utc).astimezone(self.zone)
        date = local.strftime("%Y-%m-%d")
        window = in_quiet_window(self.windows, local)
        row = Occurrence(self.trigger_id, self.trigger_name, at)
        if date in self.skipped_dates:
            row.suppressed_by, row.reason = (
                GateOutcome.SKIPPED.value,
                f"skip date {date}",
            )
        elif window is not None:
            row.suppressed_by, row.reason = (
                GateOutcome.QUIET.value,
                f"quiet hours {window.start}–{window.end}",
            )
        return row

    def collect(
        self, at: float, end: float, cap: int, advance: Callable[[float], float]
    ) -> tuple[list[Occurrence], bool]:
        rows: list = []
        while at < end:
            if len(rows) >= max(1, cap):
                return rows, True
            rows.append(self.annotate(at))
            following = advance(at)
            if following <= at:
                break
            at = following
        return rows, False


def project_occurrences(
    *,
    trigger_id: str,
    trigger_name: str,
    interval_secs: float,
    first_fire_at: float,
    start: datetime,
    days: int = 7,
    gates: dict[str, Any] | None = None,
    cap: int = MAX_OCCURRENCES_PER_TRIGGER,
    skip_dates: list[str] | None = None,
    tz_name: str = "",
    next_after: Any = None,
    until: datetime | None = None,
) -> tuple[list[Occurrence], bool]:
    stepping = callable(next_after)
    if not stepping and (interval_secs <= 0 or first_fire_at <= 0):
        return [], False
    settings = gates or {}
    windows, _ = parse_windows(settings.get("quiet_hours"))
    projection = OccurrenceProjection(
        trigger_id,
        trigger_name,
        windows,
        {
            str(date).strip()
            for date in (skip_dates or settings.get("skip_dates") or [])
            if date
        },
        _resolve_zone(tz_name),
    )
    lower = start.timestamp()
    upper = (until if until is not None else start + timedelta(days=days)).timestamp()
    if stepping:
        at = float(next_after(lower - 1))
        if at <= 0:
            return [], False

        def advance(value):
            return float(next_after(value))

    else:
        at = first_fire_at
        if at < lower:
            at += int((lower - at) // interval_secs) * interval_secs
            at += interval_secs if at < lower else 0

        def advance(value):
            return value + interval_secs

    return projection.collect(at, upper, cap, advance)


@dataclass
class Finding:
    trigger_id: str
    code: str
    detail: str
    fix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            key: getattr(self, key) for key in ("trigger_id", "code", "detail", "fix")
        }


@dataclass
class DoctorReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return len(self.findings) == 0

    def to_dict(self) -> dict[str, Any]:
        rows = [finding.to_dict() for finding in self.findings]
        return dict(healthy=self.healthy, findings=rows, count=len(rows))


def _is_broad_glob(pattern: str) -> bool:
    wildcard = re.search(r"[*?\[]", pattern) if pattern else None
    if wildcard is None:
        return False
    head = pattern[: wildcard.start()].replace("\\", "/")
    return (
        sum(part not in {"", "~", "."} for part in head.split("/"))
        < BROAD_GLOB_SEGMENTS
    )


def _mapping(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


@dataclass
class TriggerDiagnosis:
    entry: dict[str, Any]
    workflows: set[str] | None
    duty_gates: set[str]
    action_providers: set[str]
    findings: list[Finding] = field(default_factory=list)

    def add(self, code: str, detail: str, fix: str) -> None:
        self.findings.append(
            Finding(str(self.entry.get("id") or ""), code, detail, fix)
        )

    def references(self) -> None:
        workflow = _mapping(self.entry.get("workflow"))
        inline = _mapping(workflow.get("inline"))
        config = _mapping(inline.get("config"))
        inline_ref = (
            str(config.get("workflow") or "")
            if str(inline.get("provider") or "").strip() == "run-workflow"
            else ""
        )
        reference = str(workflow.get("def") or workflow.get("name") or "") or inline_ref
        if reference and self.workflows is not None and reference not in self.workflows:
            self.add(
                "orphaned_workflow_ref",
                f"references the workflow {reference!r}, which does not exist",
                "point the trigger at an existing workflow, or re-create the missing one",
            )

    def watch_scope(self) -> None:
        spec = _mapping(self.entry.get("spec"))
        for key in ("glob", "pattern", "path"):
            value = str(spec.get(key) or "")
            if value and _is_broad_glob(value):
                self.add(
                    "broad_watch_glob",
                    f"watches {value!r}, which matches nearly every file",
                    "scope the glob to a project directory, e.g. ~/projects/<name>/**",
                )

    def quiet_schedule(self) -> None:
        raw = _mapping(self.entry.get("gates")).get("quiet_hours")
        windows, issues = parse_windows(raw)
        for issue in issues:
            self.add(
                "invalid_quiet_window",
                issue,
                "use HH:MM start/end values; an invalid window is dropped, so the trigger is NOT protected by it",
            )
        if windows and _covers_whole_week(windows):
            self.add(
                "quiet_hours_cover_everything",
                "the quiet windows cover every hour of every day, so this trigger can never fire",
                "narrow the windows, or disable the trigger if that was the intent",
            )
        if not windows and resolution_of(raw) == QuietResolution.CATCH_UP.value:
            self.add(
                "catch_up_without_quiet_hours",
                "quiet_resolution is catch_up but no quiet window is configured",
                "add a quiet window, or drop the resolution setting",
            )

    def write_fence(self) -> None:
        workflow = self.entry.get("workflow")
        if not isinstance(workflow, dict):
            return
        from gideon.automation.triggers.screen import provider_is_read_only

        inline = workflow.get("inline")
        action = str(
            (inline if isinstance(inline, dict) and inline else workflow).get(
                "provider"
            )
            or ""
        ).strip()
        granted = _mapping(self.entry.get("capabilities")).get("providers") or []
        if action and not provider_is_read_only(action) and action not in granted:
            self.add(
                "unfenced_write_action",
                f"runs the write-capable action {action!r} with no capability grant, so the frozen-capability fence refuses it",
                "re-save the automation to freeze its capability set, or switch it to a read-only action",
            )

    def webhook_secret(self) -> None:
        spec = self.entry.get("spec")
        if not isinstance(spec, dict):
            return
        from gideon.automation.triggers.models import _token_ref_issues

        if _token_ref_issues(spec):
            self.add(
                "verbatim_webhook_token",
                "stores its webhook bearer token verbatim in triggers.json, which is snapshotted, echoed into run records and rendered in the UI",
                "store it with `gideon auth` and set token_ref to {{secret:KEY}}, then rotate the exposed token",
            )

    def caps(self) -> None:
        gates = _mapping(self.entry.get("gates"))
        unread = sorted(key for key in UNMETERED_CAPS if gates.get(key))
        if unread:
            self.add(
                "unmetered_cap",
                f"sets {', '.join(unread)}, which no meter reads yet — this automation is NOT bounded by that cap",
                "use gates.max_fires (enforced) to bound total fires, or remove the cap until its meter lands",
            )

    def agent_scope(self) -> None:
        if _mapping(self.entry.get("spec")).get("agent_scope"):
            self.add(
                "unenforced_agent_scope",
                "declares agent_scope, but no fire path reads it — this trigger is NOT limited to those agents",
                "remove agent_scope until the chat-turn event source lands, or use a lifecycle trigger referenced from the agent's own bindings",
            )

    def path_fence(self) -> None:
        paths = _mapping(self.entry.get("capabilities")).get("paths")
        if not paths:
            return
        from gideon.automation.triggers.pathguard import unsafe_entries

        for path, issue in unsafe_entries(paths):
            self.add(
                "unbounded_path_fence",
                f"allowlists the path {path!r}, which {issue}",
                "replace it with an absolute directory (e.g. `/Users/you/notes/*`) so the fence bounds a real location",
            )

    def duty_provider(self) -> None:
        setting = _mapping(self.entry.get("gates")).get("duty_gate")
        if not isinstance(setting, dict):
            return
        name = str(setting.get("provider") or "")
        if name and name not in self.duty_gates:
            self.add(
                "unknown_duty_gate",
                f"names the duty gate {name!r}, which is not registered; the gate fails open so this trigger runs UNFILTERED",
                "install the app that provides the gate, or remove the duty_gate block",
            )

    def action_provider(self) -> None:
        workflow = _mapping(self.entry.get("workflow"))
        inline = _mapping(workflow.get("inline"))
        name = str((inline or workflow).get("provider") or "").strip()
        if name and name not in self.action_providers:
            self.add(
                "unknown_action_provider",
                f"names the action provider {name!r}, which is not registered and cannot dispatch",
                "install the app that provides the action, or choose a dispatchable action provider",
            )

    def run(self) -> list[Finding]:
        checks = (
            self.references,
            self.watch_scope,
            self.quiet_schedule,
            self.write_fence,
            self.webhook_secret,
            self.caps,
            self.agent_scope,
            self.path_fence,
            self.duty_provider,
            self.action_provider,
        )
        for check in checks:
            check()
        return self.findings


def diagnose(
    triggers: list[dict[str, Any]],
    *,
    known_workflows: set[str] | frozenset[str] | None = None,
    known_duty_gates: set[str] | frozenset[str] | None = None,
    known_action_providers: set[str] | frozenset[str] | None = None,
) -> DoctorReport:
    workflows = None if known_workflows is None else set(known_workflows)
    providers = set(_DUTY_GATES if known_duty_gates is None else known_duty_gates)
    if known_action_providers is None:
        from gideon.integrations.action_providers.registry import (
            dispatchable_action_providers,
        )

        action_providers = set(dispatchable_action_providers())
    else:
        action_providers = set(known_action_providers)
    rows = []
    for entry in triggers or []:
        rows.extend(
            TriggerDiagnosis(entry, workflows, providers, action_providers).run()
        )
    return DoctorReport(rows)


def _covers_whole_week(windows: list[QuietWindow]) -> bool:
    bands = [
        band for window in windows if (band := MinuteBand.read(window)) is not None
    ]
    return all(
        any(band.includes(day, hour * 60) for band in bands)
        for day in range(7)
        for hour in range(24)
    )


def parse_default_window(value: str) -> QuietWindow | None:
    text = (value or "").strip()
    separator = next(
        (candidate for candidate in ("-", "–", "to") if candidate in text), None
    )
    if separator is None:
        return None
    start, end = text.split(separator, 1)
    window = QuietWindow(start.strip(), end.strip())
    return window if window.valid else None


def _workflows_config() -> object | None:
    try:
        from gideon.core.config.loader import AppConfig

        return AppConfig.load().workflows
    except Exception:
        return None


def default_quiet_window() -> QuietWindow | None:
    return parse_default_window(
        str(getattr(_workflows_config(), "default_quiet_windows", "") or "")
    )


def default_duty_gate() -> str:
    return str(getattr(_workflows_config(), "duty_gate_default", "") or "").strip()


def apply_defaults(gates: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(gates or {})
    if "quiet_hours" not in resolved:
        configured = default_quiet_window()
        if configured is not None:
            resolved.update(quiet_hours=configured.to_dict())
    if "duty_gate" not in resolved:
        configured_name = default_duty_gate()
        if configured_name:
            resolved.update(duty_gate=dict(provider=configured_name, config={}))
    return resolved
