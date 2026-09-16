"""Persistent report definitions, schedule admission and completion receipts."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from uuid import uuid4

from croniter import croniter

from gideon.automation.schedule import ScheduleDefinition, validate_cron_expr
from gideon.cognition.knowledge.semantics import (
    RESEARCH_FINDING_KIND as _RESEARCH_FINDING_KIND,
)
from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.security.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)
FINDING_KIND = _RESEARCH_FINDING_KIND
CITE_SOURCE_ONLY = "cite-source-only"
ALLOW_CITING_CONTEXT = "allow-citing-context"
CLAIM_ID_PREFIX = "research-report:"
CITATION_POLICIES = (CITE_SOURCE_ONLY, ALLOW_CITING_CONTEXT)
_REPORTS_FILE = "research_reports.json"
MIN_ITERATION_CAP = 1
MAX_ITERATION_CAP = 10
_MAX_ERROR_CHARS = 500


@dataclass
class Scope:
    tags: tuple[str, ...] = ()
    window_secs: int = 0


@dataclass
class ReportDefinition:
    id: str
    name: str
    prompt: str
    schedule: ScheduleDefinition
    tz: str = ""
    source: Scope = field(default_factory=Scope)
    context: Scope | None = None
    citation_policy: str = CITE_SOURCE_ONLY
    iteration_cap: int = 3
    enabled: bool = True
    created_ts: float = 0.0
    last_run_ts: float | None = None
    last_status: str = ""
    last_error: str = ""
    watermark_ts: float = 0.0


def config_dir() -> Path:
    return config_loader.config_dir()


def report_claim_id(report_id: str) -> str:
    return f"{CLAIM_ID_PREFIX}{report_id}"


def _as_str(raw: object, default: str = "") -> str:
    return default if not isinstance(raw, str) else raw


def _as_bool(raw: object, default: bool) -> bool:
    return default if not isinstance(raw, bool) else raw


def _number(raw: object) -> bool:
    return not isinstance(raw, bool) and isinstance(raw, (int, float))


def _as_float(raw: object, default: float = 0.0) -> float:
    return float(raw) if _number(raw) else default


def _as_int(raw: object, default: int = 0) -> int:
    return int(raw) if _number(raw) else default


def _as_opt_float(raw: object) -> float | None:
    return float(raw) if _number(raw) else None


def _scope_to_dict(scope: Scope) -> dict:
    return dict(tags=list(scope.tags), window_secs=scope.window_secs)


def _scope_from_dict(raw: object) -> Scope:
    value = raw if isinstance(raw, dict) else {}
    tags = value.get("tags")
    chosen = (
        tuple(tag for tag in tags if isinstance(tag, str) and tag)
        if isinstance(tags, list)
        else ()
    )
    return Scope(chosen, max(0, _as_int(value.get("window_secs"))))


def _schedule_to_dict(sched: ScheduleDefinition) -> dict:
    return {
        name: getattr(sched, name)
        for name in ("kind", "every_secs", "at_ts", "cron_expr")
    }


def _schedule_from_dict(raw: object) -> ScheduleDefinition:
    if not isinstance(raw, dict):
        return ScheduleDefinition(kind="")
    interval = _as_int(raw.get("every_secs"))
    expression = raw.get("cron_expr")
    return ScheduleDefinition(
        kind=_as_str(raw.get("kind")),
        every_secs=interval if interval > 0 else None,
        at_ts=_as_opt_float(raw.get("at_ts")),
        cron_expr=expression if isinstance(expression, str) else None,
    )


@dataclass(frozen=True)
class _DefinitionCodec:
    @staticmethod
    def encode(definition: ReportDefinition) -> dict:
        result = {
            key: getattr(definition, key)
            for key in (
                "id",
                "name",
                "prompt",
                "tz",
                "citation_policy",
                "iteration_cap",
                "enabled",
                "created_ts",
                "last_run_ts",
                "last_status",
                "last_error",
                "watermark_ts",
            )
        }
        result.update(
            schedule=_schedule_to_dict(definition.schedule),
            source=_scope_to_dict(definition.source),
            context=(
                _scope_to_dict(definition.context)
                if definition.context is not None
                else None
            ),
        )
        return result

    @staticmethod
    def decode(raw: dict) -> ReportDefinition:
        strings = {
            key: _as_str(raw.get(key))
            for key in ("id", "name", "prompt", "tz", "last_status", "last_error")
        }
        policy = _as_str(raw.get("citation_policy"), CITE_SOURCE_ONLY)
        context = raw.get("context")
        return ReportDefinition(
            **strings,
            schedule=_schedule_from_dict(raw.get("schedule")),
            source=_scope_from_dict(raw.get("source")),
            context=None if context is None else _scope_from_dict(context),
            citation_policy=policy if policy in CITATION_POLICIES else CITE_SOURCE_ONLY,
            iteration_cap=_clamp_iteration_cap(_as_int(raw.get("iteration_cap"), 3)),
            enabled=_as_bool(raw.get("enabled"), True),
            created_ts=_as_float(raw.get("created_ts")),
            last_run_ts=_as_opt_float(raw.get("last_run_ts")),
            watermark_ts=_as_float(raw.get("watermark_ts")),
        )


def to_dict(defn: ReportDefinition) -> dict:
    return _DefinitionCodec.encode(defn)


def from_dict(raw: dict) -> ReportDefinition:
    return _DefinitionCodec.decode(raw)


def _store_path() -> Path:
    return config_dir().joinpath(_REPORTS_FILE)


@dataclass(frozen=True)
class _ReportArchive:
    path: Path

    def read(self) -> list[ReportDefinition]:
        try:
            decoded = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning(
                "Unreadable research-report store at %s, treating as empty", self.path
            )
            return []
        if isinstance(decoded, list):
            return [
                from_dict(row)
                for row in decoded
                if isinstance(row, dict) and _as_str(row.get("id"))
            ]
        logger.warning(
            "Research-report store at %s is not a list, treating as empty", self.path
        )
        return []

    def write(self, definitions: list[ReportDefinition]) -> None:
        document = [to_dict(definition) for definition in definitions]
        atomic_write(self.path, json.dumps(document, indent=2))


def load_reports() -> list[ReportDefinition]:
    return _ReportArchive(_store_path()).read()


def get_report(report_id: str) -> ReportDefinition | None:
    return next(
        (definition for definition in load_reports() if definition.id == report_id),
        None,
    )


def _write(defns: list[ReportDefinition]) -> None:
    _ReportArchive(_store_path()).write(defns)


def _clamp_iteration_cap(cap: int) -> int:
    return max(min(cap, MAX_ITERATION_CAP), MIN_ITERATION_CAP)


def save_report(defn: ReportDefinition) -> ReportDefinition:
    policy = defn.citation_policy
    if policy not in CITATION_POLICIES:
        raise ValueError(
            f"invalid citation_policy {policy!r} (expected one of {list(CITATION_POLICIES)})"
        )
    defn.id = defn.id or f"rpt-{uuid4().hex[:8]}"
    defn.created_ts = defn.created_ts or time.time()
    defn.iteration_cap = _clamp_iteration_cap(defn.iteration_cap)
    retained = [row for row in load_reports() if row.id != defn.id]
    _write([*retained, defn])
    _sync_schedule(defn)
    return defn


def delete_report(report_id: str) -> bool:
    original = load_reports()
    retained = [definition for definition in original if definition.id != report_id]
    changed = len(original) != len(retained)
    if changed:
        _write(retained)
        _remove_schedule(report_id)
    return changed


def _schedule_change(operation: str, value: ReportDefinition | str) -> str:
    try:
        from gideon.cognition.knowledge import report_schedules

        target = (
            report_schedules.sync if operation == "sync" else report_schedules.remove
        )
        return target(value)
    except Exception as exc:
        label = "sync" if operation == "sync" else "removal"
        identifier = value.id if isinstance(value, ReportDefinition) else value
        logger.warning(
            "research report %s: schedule %s failed (%s)", identifier, label, exc
        )
        return f"schedule {label} failed: {exc}"


def _sync_schedule(defn: ReportDefinition) -> str:
    return _schedule_change("sync", defn)


def _remove_schedule(report_id: str) -> str:
    return _schedule_change("remove", report_id)


def _report_tz(defn: ReportDefinition) -> tzinfo:
    from gideon.core.timezones import UnknownTimeZone, resolve_zone

    try:
        zone = resolve_zone(defn.tz)
    except UnknownTimeZone as exc:
        logger.warning(
            "Report %s: %s — using this machine's zone instead", defn.id, exc
        )
        zone = resolve_zone("")
    return zone


def _anchor_ts(defn: ReportDefinition) -> float | None:
    anchor = defn.last_run_ts
    if anchor is None and defn.created_ts > 0:
        anchor = defn.created_ts
    return anchor


@dataclass(frozen=True)
class _ReportCadence:
    definition: ReportDefinition
    now: float
    anchor: float

    def interval(self) -> tuple[bool, str]:
        seconds = self.definition.schedule.every_secs
        if not isinstance(seconds, int) or seconds <= 0:
            return (
                False,
                f"invalid every_secs {seconds!r} (expected a positive integer)",
            )
        boundary = self.anchor + seconds
        due = self.now >= boundary
        reason = (
            f"every {seconds}s elapsed since {self.anchor:.0f}"
            if due
            else f"next fire at {boundary:.0f}"
        )
        return due, reason

    def one_time(self) -> tuple[bool, str]:
        boundary = self.definition.schedule.at_ts
        if boundary is None:
            return False, "invalid at schedule: at_ts is unset"
        if self.definition.last_run_ts is not None:
            return False, "one-shot 'at' schedule already ran"
        due = self.now >= boundary
        return due, (
            f"one-shot time {boundary:.0f} reached"
            if due
            else f"next fire at {boundary:.0f}"
        )

    def cron(self) -> tuple[bool, str]:
        expression = self.definition.schedule.cron_expr or ""
        if not expression or not validate_cron_expr(expression):
            return False, f"invalid cron expression {expression!r}"
        local = datetime.fromtimestamp(self.now, tz=_report_tz(self.definition))
        boundary = float(croniter(expression, local).get_prev(float))
        due = boundary > self.anchor
        return due, (
            f"cron {expression!r} boundary at {boundary:.0f} passed"
            if due
            else f"no cron {expression!r} boundary since {self.anchor:.0f}"
        )

    def evaluate(self) -> tuple[bool, str]:
        callbacks = {"every": self.interval, "at": self.one_time, "cron": self.cron}
        kind = self.definition.schedule.kind
        callback = callbacks.get(kind)
        return (
            callback() if callback else (False, f"unsupported schedule kind {kind!r}")
        )


def is_due(defn: ReportDefinition, *, now: float) -> tuple[bool, str]:
    try:
        if not defn.enabled:
            return False, "disabled"
        anchor = _anchor_ts(defn)
        if anchor is not None:
            return _ReportCadence(defn, now, anchor).evaluate()
        return False, "no anchor: created_ts is unset (refusing to anchor on the epoch)"
    except Exception as exc:
        logger.warning(
            "Dueness evaluation failed for report %s", defn.id, exc_info=True
        )
        return False, f"schedule evaluation failed: {exc}"


def _redact(text: str) -> str:
    if text:
        for scrub in (redact_exfiltration_urls, redact_credentials):
            text, _ = scrub(text)
    return text[:_MAX_ERROR_CHARS] if text else ""


@dataclass(frozen=True)
class _RunReceipt:
    ok: bool
    error: str
    watermark: float | None

    def apply(self, definition: ReportDefinition) -> None:
        stamp = time.time()
        definition.last_status = "ok" if self.ok else "error"
        definition.last_error = "" if self.ok else _redact(self.error)
        if self.ok:
            definition.last_run_ts = stamp
            if self.watermark is not None:
                definition.watermark_ts = self.watermark


def record_run(
    report_id: str, *, ok: bool, error: str = "", watermark_ts: float | None = None
) -> None:
    definitions = load_reports()
    target = next((entry for entry in definitions if entry.id == report_id), None)
    if target is not None:
        _RunReceipt(ok, error, watermark_ts).apply(target)
        _write(definitions)
    else:
        logger.warning("record_run for unknown research report %s, ignoring", report_id)
