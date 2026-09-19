from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gideon.automation.triggers.models import (
    LEGACY_FIELD_MAP,
    TriggerHealth,
    TriggerState,
)

NEVER_PERSISTED: frozenset[str] = frozenset({"dry_run", "last_outcome"})
_HEALTH_FROM_STATUS: dict[str, str] = {
    "ok": TriggerHealth.OK.value,
    "success": TriggerHealth.OK.value,
    "error": TriggerHealth.FAILING.value,
    "failed": TriggerHealth.FAILING.value,
    "timeout": TriggerHealth.DEGRADED.value,
}


@dataclass
class Converted:
    trigger: dict[str, Any]
    dropped: list[str] = field(default_factory=list)
    unaccounted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def lossless(self) -> bool:
        return len(self.unaccounted) == 0


@dataclass(frozen=True)
class LegacySchedule:
    schedule: dict[str, Any]
    job: dict[str, Any]

    def convert(self) -> tuple[dict[str, Any], list[str]]:
        source = self.schedule or {}
        kind = str(source.get("kind", "") or "")
        notes = []
        match kind:
            case "cron":
                spec = {"kind": "cron", "expr": str(source.get("cron_expr", "") or "")}
                if not spec["expr"]:
                    notes.append(
                        "a cron job with no expression cannot be scheduled; needs author attention"
                    )
            case "at":
                spec = {
                    "kind": "at",
                    "at": source.get("at_ts"),
                    "delete_after_run": bool(self.job.get("delete_after_run", True)),
                }
            case "every":
                spec = {"kind": "interval", "interval_secs": source.get("every_secs")}
                notes.append(
                    "legacy `every` has no trigger clock kind; converted to an explicit interval rather than `at`, which would turn a recurring job into a one-shot"
                )
            case _:
                spec: dict = {"kind": ""}
                notes.append(
                    f"unknown legacy schedule kind {kind!r}; the trigger loads disabled for review"
                )
        for original, target, transform in (
            ("timezone", "timezone", lambda value: value),
            ("skip_dates", "skip_dates", list),
            ("strict_schedule", "strict", bool),
        ):
            if self.job.get(original):
                spec[target] = transform(self.job[original])
        return spec, notes


def clock_spec(
    schedule: dict[str, Any], job: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    return LegacySchedule(schedule, job).convert()


def _delivery(job: dict[str, Any]) -> str:
    channel = str(job.get("channel", "") or "") if not job.get("silent") else ""
    return "channel:" + channel if channel else "none"


def _session(job: dict[str, Any]) -> str:
    key = str(job.get("session_key", "") or "")
    return "pinned:" + key if key and job.get("persistent_session") else "fresh"


def _workflow(job: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    sequence = list(job.get("agent_sequence") or [])
    if not sequence:
        action = job.get("action")
        return (
            {"inline": dict(action)} if isinstance(action, dict) and action else {}
        ), []
    preview = ", ".join(sequence[:3]) + ("…" if len(sequence) > 3 else "")
    note = (
        f"agent_sequence has {len(sequence)} steps ({preview}); §2 converts a sequence to a workflow "
        "DEF, which is authoring work — the trigger is left disabled rather than running only its first step"
    )
    return {}, [note]


@dataclass
class JobConversion:
    job: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def text(self, key: str, default: str = "") -> str:
        return str(self.job.get(key, default) or default)

    def build(self) -> Converted:
        raw = self.job.get("schedule")
        schedule = dict(raw) if isinstance(raw, dict) else {}
        spec, cadence_notes = clock_spec(schedule, self.job)
        workflow, action_notes = _workflow(self.job)
        self.notes.extend(cadence_notes)
        self.notes.extend(action_notes)
        gates = {}
        if self.job.get("last_posted_hash") or self.job.get("consecutive_dupes"):
            gates["idempotency"] = True
            self.notes.append(
                "duplicate-suppression state is delivery-layer; carried as gates.idempotency"
            )
        capabilities = {"env": dict(self.job["env"])} if self.job.get("env") else {}
        status = self.text("last_status").strip().lower()
        last_run = float(self.job.get("last_run_ts") or 0.0)
        document: dict = {key: self.text(key) for key in ("id", "name")}
        document.update(
            kind="clock",
            enabled=bool(self.job.get("enabled", False)) and not self.notes,
            created_by=self.text("created_by", "user"),
            spec=spec,
            gates=gates,
            capabilities=capabilities,
            workflow=workflow,
            session=_session(self.job),
            delivery=_delivery(self.job),
            failure_delivery="inbox",
            failure_policy=(
                {"dedupe_hash": True} if self.job.get("last_failure_hash") else {}
            ),
            state=(
                TriggerState.PAUSED.value if self.notes else TriggerState.ACTIVE.value
            ),
            health_status=_HEALTH_FROM_STATUS.get(status, TriggerHealth.OK.value),
            last_error_summary=self.text("last_error"),
            last_success_at=_iso(last_run) if status in {"ok", "success"} else "",
            last_failure_at=_iso(float(self.job.get("last_failure_at") or 0.0)),
        )
        excluded = {
            key
            for key, destination in LEGACY_FIELD_MAP["ScheduleJob"].items()
            if destination is None
        }
        return Converted(
            document,
            sorted(excluded.intersection(self.job)),
            unconverted_fields(self.job),
            self.notes,
        )


def convert_job(job: dict[str, Any]) -> Converted:
    return JobConversion(job).build()


def _iso(epoch: float) -> str:
    if epoch <= 0:
        return ""
    from datetime import datetime, timezone

    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    return stamp.replace("+00:00", "Z")


def unconverted_fields(job: dict[str, Any]) -> list[str]:
    return sorted(
        set(job or {}) - (set(LEGACY_FIELD_MAP["ScheduleJob"]) | NEVER_PERSISTED)
    )


@dataclass
class MigrationReport:
    converted: list[Converted] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)

    @property
    def lossless(self) -> bool:
        return not self.refused and all(row.lossless for row in self.converted)

    @property
    def needs_review(self) -> list[str]:
        return [
            row.trigger["id"]
            for row in filter(lambda value: value.notes, self.converted)
        ]

    def to_dict(self) -> dict[str, Any]:
        unaccounted = set().union(*(row.unaccounted for row in self.converted))
        return dict(
            converted=len(self.converted),
            refused=len(self.refused),
            lossless=self.lossless,
            needs_review=self.needs_review,
            unaccounted=sorted(unaccounted),
        )

    def accept(self, raw: Any) -> None:
        if isinstance(raw, dict) and str(raw.get("id", "") or "").strip():
            self.converted.append(convert_job(raw))
        else:
            self.refused.append(
                raw if isinstance(raw, dict) else {"row": repr(raw)[:120]}
            )


def migrate_crons(store: dict[str, Any]) -> MigrationReport:
    report = MigrationReport()
    rows = store.get("jobs") if isinstance(store, dict) else None
    for row in rows or []:
        report.accept(row)
    return report
