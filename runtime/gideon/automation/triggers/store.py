"""Local trigger records with locked mutations, atomic publication and visible parse issues."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from gideon.automation.triggers.models import Issue, Trigger, parse_trigger
from gideon.automation.triggers.provider import TriggerStoreProvider

logger = logging.getLogger(__name__)
STORE_VERSION = 1
STORE_FILENAME = "triggers.json"
LOCK_FILENAME = ".triggers.lock"
RUNTIME_FIELDS = (
    "next_fire_at",
    "last_run_id",
    "run_count",
    "last_success_at",
    "last_failure_at",
    "health_status",
    "last_error_summary",
    "state",
    "enabled",
)


@dataclass
class LoadedTrigger:
    trigger: Trigger
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return list(filter(lambda issue: issue.severity == "error", self.issues))

    @property
    def warnings(self) -> list[Issue]:
        return list(filter(lambda issue: issue.severity != "error", self.issues))

    @property
    def ok(self) -> bool:
        return all(issue.severity != "error" for issue in self.issues)

    @classmethod
    def parse(cls, record: dict) -> LoadedTrigger:
        trigger, issues = parse_trigger(record)
        return cls(trigger, list(issues))

    def to_dict(self) -> dict[str, Any]:
        names = ("path", "message", "severity", "closest")
        issues = [
            {name: getattr(issue, name) for name in names} for issue in self.issues
        ]
        return dict(trigger=self.trigger.to_dict(), issues=issues, ok=self.ok)


class TriggerDocument:
    def __init__(self, path: Path):
        self.path = path
        self.observed_mtime = 0.0

    def timestamp(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            self.observed_mtime = 0.0
            return []
        try:
            self.observed_mtime = self.path.stat().st_mtime
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning(
                "triggers.json is unreadable or malformed; treating as empty"
            )
            return []
        records = envelope.get("triggers") if isinstance(envelope, dict) else envelope
        return list(filter(lambda item: isinstance(item, dict), records or []))

    def publish(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            dict(version=STORE_VERSION, triggers=rows, saved_at=time.time()), indent=2
        )
        staged = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with staged.open("x", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, self.path)
            self.observed_mtime = self.timestamp()
        finally:
            staged.unlink(missing_ok=True)


@dataclass
class RecordMutation:
    rows: list[dict[str, Any]]
    changed: bool = False

    @staticmethod
    def identity(row: dict) -> str:
        return str(row.get("id") or "")

    def remove(self, identity: str) -> bool:
        retained = [row for row in self.rows if self.identity(row) != identity]
        removed = len(retained) != len(self.rows)
        self.rows, self.changed = retained, self.changed or removed
        return removed

    def replace(self, trigger: Trigger) -> None:
        self.remove(trigger.id)
        self.rows.append(trigger.to_dict())
        self.changed = True

    def set_enabled(self, identity: str, enabled: bool) -> Trigger | None:
        for position, record in enumerate(self.rows):
            if self.identity(record) != identity:
                continue
            loaded = LoadedTrigger.parse(record)
            if enabled and not loaded.ok:
                logger.info("refusing to enable %s: it has parse errors", identity)
                return None
            loaded.trigger.enabled = enabled
            self.rows[position] = loaded.trigger.to_dict()
            self.changed = True
            return loaded.trigger
        return None


class TriggerStore(TriggerStoreProvider):
    def __init__(self, base_dir: Path | str | None = None) -> None:
        from gideon.core.config.loader import config_dir

        self._dir = Path(base_dir) if base_dir else config_dir()
        self._path, self._lock_path = (
            self._dir / STORE_FILENAME,
            self._dir / LOCK_FILENAME,
        )
        self._document = TriggerDocument(self._path)

    @property
    def _last_mtime(self) -> float:
        return self._document.observed_mtime

    @_last_mtime.setter
    def _last_mtime(self, value: float) -> None:
        self._document.observed_mtime = value

    @property
    def base_dir(self) -> Path:
        return self._path.parent

    @property
    def path(self) -> Path:
        return self._document.path

    def exists(self) -> bool:
        return self.path.exists()

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lease, fcntl.LOCK_UN)

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self._document.publish(rows)

    def changed_on_disk(self) -> bool:
        return self._document.timestamp() > self._last_mtime

    def _read_rows(self) -> list[dict[str, Any]]:
        return self._document.read()

    def load(self) -> list[LoadedTrigger]:
        return list(map(LoadedTrigger.parse, self._read_rows()))

    def list_triggers(
        self, *, kind: str = "", include_broken: bool = True
    ) -> list[Trigger]:
        return [
            entry.trigger
            for entry in self.load()
            if (include_broken or entry.ok) and (not kind or entry.trigger.kind == kind)
        ]

    def get(self, trigger_id: str) -> LoadedTrigger | None:
        return next(
            (entry for entry in self.load() if entry.trigger.id == trigger_id), None
        )

    @contextmanager
    def _mutation(self) -> Iterator[RecordMutation]:
        with self._file_lock():
            changes = RecordMutation(self._read_rows())
            yield changes
            if changes.changed:
                self._write(changes.rows)

    def save_all(self, triggers: list[Trigger]) -> int:
        with self._file_lock():
            snapshot = [trigger.to_dict() for trigger in triggers]
            self._write(snapshot)
        return len(snapshot)

    def upsert(self, trigger: Trigger) -> Trigger:
        from gideon.automation.triggers import routing

        routed = routing.route_upsert(trigger, native=self)
        if routed is not None:
            return routed
        with self._mutation() as changes:
            changes.replace(trigger)
        return trigger

    def delete(self, trigger_id: str) -> bool:
        from gideon.automation.triggers import routing

        routed = routing.route_delete(trigger_id, native=self)
        if routed is not None:
            return routed
        with self._mutation() as changes:
            removed = changes.remove(trigger_id)
        return removed

    def set_enabled(self, trigger_id: str, enabled: bool) -> Trigger | None:
        with self._mutation() as changes:
            selected = changes.set_enabled(trigger_id, enabled)
        return selected

    def migrate_from_crons(
        self, crons_path: Path | str | None = None
    ) -> dict[str, Any]:
        source = Path(crons_path) if crons_path else self.base_dir / "crons.json"
        return CronImport(self, source).run()


class CronImport:
    def __init__(self, destination: TriggerStore, source: Path):
        self.destination, self.source = destination, source

    @staticmethod
    def unavailable(reason: str, *, lossless: bool) -> dict:
        return dict(converted=0, refused=0, lossless=lossless, written=0, reason=reason)

    def run(self) -> dict[str, Any]:
        from gideon.automation.triggers.migrate import migrate_crons

        if not self.source.exists():
            return self.unavailable("no crons.json", lossless=True)
        try:
            original = json.loads(self.source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self.unavailable("crons.json is unreadable", lossless=False)
        report = migrate_crons(original if isinstance(original, dict) else {})
        summary = report.to_dict()
        invalid, written = [], 0
        for converted in getattr(report, "converted", None) or []:
            record = getattr(converted, "trigger", None)
            if not isinstance(record, dict):
                continue
            loaded = LoadedTrigger.parse(record)
            incoming = loaded.trigger
            if loaded.errors:
                incoming.enabled = False
                invalid.append(
                    dict(
                        id=incoming.id,
                        errors=[issue.message for issue in loaded.errors],
                    )
                )
            else:
                written += 1
            previous = self.destination.get(incoming.id)
            if previous is not None:
                _carry_runtime_state(previous.trigger, incoming)
            self.destination.upsert(incoming)
        summary.update(written=written, unparseable=invalid, source_kept=True)
        return summary


def _carry_runtime_state(existing: Trigger, incoming: Trigger) -> None:
    values = ((name, getattr(existing, name, None)) for name in RUNTIME_FIELDS)
    retained = {
        name: value
        for name, value in values
        if isinstance(value, bool) or value not in (None, "", 0)
    }
    for name, value in retained.items():
        setattr(incoming, name, value)


def health(store: TriggerStore) -> dict[str, Any]:
    rows = store.load()
    broken = tuple(row for row in rows if not row.ok)
    return dict(
        path=str(store.path),
        exists=store.exists(),
        total=len(rows),
        enabled=sum(1 for row in rows if row.trigger.enabled),
        broken=len(broken),
        broken_ids=[row.trigger.id for row in broken if row.trigger.id],
        warnings=sum(len(row.warnings) for row in rows),
        by_kind=_by_kind(rows),
    )


def _by_kind(rows: list[LoadedTrigger]) -> dict[str, int]:
    return dict(sorted(Counter(row.trigger.kind for row in rows).items()))
