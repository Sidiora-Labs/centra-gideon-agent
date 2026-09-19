from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

TIMING_FIELDS: tuple[str, ...] = (
    "skip_dates",
    "timezone",
    "strict_schedule",
    "delete_after_run",
)


@dataclass
class RowDiff:
    job_id: str
    present: bool = False
    paused: bool = False
    note: str = ""
    field_drift: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.present and not any((self.paused, self.field_drift, self.errors))

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self), clean=self.clean)


@dataclass
class VerifyReport:
    rows: list[RowDiff] = field(default_factory=list)
    unreadable: str = ""

    def select(self, predicate: Callable[[RowDiff], Any]) -> list[str]:
        return [row.job_id for row in filter(predicate, self.rows)]

    @property
    def missing(self) -> list[str]:
        return self.select(lambda row: not row.present)

    @property
    def paused(self) -> list[str]:
        return self.select(lambda row: row.paused)

    @property
    def drifted(self) -> list[str]:
        return self.select(lambda row: row.field_drift)

    @property
    def broken(self) -> list[str]:
        return self.select(lambda row: row.errors)

    @property
    def ok(self) -> bool:
        return not self.unreadable and all(map(lambda row: row.clean, self.rows))

    def to_dict(self) -> dict[str, Any]:
        fields = {
            name: getattr(self, name)
            for name in ("missing", "paused", "drifted", "broken", "unreadable")
        }
        return dict(
            ok=self.ok,
            total=len(self.rows),
            **fields,
            rows=[row.to_dict() for row in self.rows],
        )


def _read_json(path: Path) -> tuple[Any, str]:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return None, f"{path.name} is unreadable: {exc}"
        return payload, ""
    return None, f"{path.name} does not exist"


def _legacy_jobs(payload: Any) -> list[dict[str, Any]]:
    values = payload.get("jobs") if isinstance(payload, dict) else payload
    return list(filter(lambda row: isinstance(row, dict), values or []))


def _spec_and_gates(trigger: Any) -> dict[str, Any]:
    return {
        key: value
        for name in ("spec", "gates")
        for section in [getattr(trigger, name, None)]
        if isinstance(section, dict)
        for key, value in section.items()
    }


def _field_drift(job: dict[str, Any], trigger: Any) -> list[str]:
    present = _spec_and_gates(trigger).keys()
    aliases = {"strict_schedule": "strict"}
    return [
        name
        for name in TIMING_FIELDS
        if job.get(name) not in (None, "", [], False)
        and name not in present
        and aliases.get(name, name) not in present
    ]


@dataclass(frozen=True)
class MigrationComparison:
    loaded: dict[str, Any]
    notes: dict[str, str]

    def compare(self, job: dict[str, Any]) -> RowDiff:
        identity = str(job.get("id") or "")
        if not identity:
            return RowDiff("<no id>", note="legacy row has no id")
        found = self.loaded.get(identity)
        if found is None:
            return RowDiff(identity)
        trigger = found.trigger
        return RowDiff(
            job_id=identity,
            present=True,
            paused=bool(job.get("enabled", False)) and not trigger.enabled,
            note=self.notes.get(identity, ""),
            field_drift=_field_drift(job, trigger),
            errors=[issue.message for issue in getattr(found, "errors", [])],
        )


def verify(
    *, crons_path: Path | str, store: Any, notes_by_id: dict[str, str] | None = None
) -> VerifyReport:
    payload, error = _read_json(Path(crons_path))
    if error:
        return VerifyReport(unreadable=error)
    comparison = MigrationComparison(
        {row.trigger.id: row for row in store.load()}, notes_by_id or {}
    )
    return VerifyReport(rows=list(map(comparison.compare, _legacy_jobs(payload))))


def report_notes(crons_path: Path | str) -> dict[str, str]:
    payload, error = _read_json(Path(crons_path))
    if error or not isinstance(payload, dict):
        return {}
    from gideon.automation.triggers.migrate import migrate_crons

    report = migrate_crons(payload)
    notes = {}
    for converted in getattr(report, "converted", None) or []:
        row = getattr(converted, "trigger", None) or {}
        identity = str(row.get("id") or "") if isinstance(row, dict) else ""
        reasons = list(getattr(converted, "notes", None) or [])
        if identity and reasons:
            notes[identity] = reasons[0]
    return notes


def verify_home(base_dir: Path | str | None = None) -> VerifyReport:
    from gideon.automation.triggers.store import TriggerStore
    from gideon.core.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    source = root / "crons.json"
    notes = report_notes(source)
    store = TriggerStore(base_dir=root)
    return verify(crons_path=source, store=store, notes_by_id=notes)


@dataclass(frozen=True)
class MigrationDisplay:
    report: VerifyReport

    def sections(self) -> list[str]:
        lines: list = []
        report = self.report
        missing, paused, drifted, broken = (
            report.missing,
            report.paused,
            report.drifted,
            report.broken,
        )
        if missing:
            lines.extend(
                (
                    f"✗ MISSING from triggers.json ({len(missing)}): {', '.join(missing)}",
                    "   These jobs did not migrate. crons.json is still intact — re-run the migration.",
                )
            )
        if paused:
            lines.append(
                f"⚠ PAUSED by the migration ({len(paused)}) — these were running and are not:"
            )
            lines.extend(
                f"   · {row.job_id}: {row.note or 'the migration could not fully interpret this row'}"
                for row in report.rows
                if row.paused
            )
            lines.append("   Read the note, then re-enable each one you still want.")
        if drifted:
            lines.append(f"⚠ FIELDS not carried ({len(drifted)}):")
            lines.extend(
                f"   · {row.job_id}: {', '.join(row.field_drift)}"
                for row in report.rows
                if row.field_drift
            )
        if broken:
            lines.append(
                f"✗ UNPARSEABLE after migration ({len(broken)}): {', '.join(broken)}"
            )
        return lines

    def render(self) -> str:
        report = self.report
        if report.unreadable:
            return f"✗ cannot verify: {report.unreadable}"
        lines = self.sections()
        if not lines:
            return f"✓ {len(report.rows)} job(s) migrated cleanly — all still enabled, no fields lost."
        summary = f"{len(report.rows)} legacy job(s): {sum(row.clean for row in report.rows)} clean, {len(report.paused)} paused, {len(report.missing)} missing, {len(report.drifted)} drifted."
        return "\n".join(
            [
                *lines,
                "",
                summary,
                "crons.json is READ-ONLY for one release and was not modified by this check.",
            ]
        )


def render(report: VerifyReport) -> str:
    return MigrationDisplay(report).render()
