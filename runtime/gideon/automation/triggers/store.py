"""Local trigger records with locked mutations, atomic publication and visible parse issues."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from gideon.automation.triggers.models import Issue, Trigger, parse_trigger
from gideon.automation.triggers.provider import TriggerStoreProvider
from gideon.operations.durability import record_files

logger = logging.getLogger(__name__)
_REPORTED_UNREADABLE: set[tuple[str, str]] = set()

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
        from gideon.automation.triggers.arm import semantic_spec_issues

        trigger, issues = parse_trigger(record)
        return cls(
            trigger,
            [*issues, *semantic_spec_issues(trigger.kind, trigger.spec)],
        )

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

    def unreadable_status(self) -> list[dict[str, str]]:
        try:
            self.read(strict=True)
        except (OSError, ValueError):
            return [self.unreadable] if self.unreadable else []
        return []

    def read(self, *, strict: bool = False) -> list[dict[str, Any]]:
        self.unreadable: dict[str, str] | None = None
        if not self.path.exists():
            self.observed_mtime = 0.0
            return []
        content = None
        try:
            self.observed_mtime = self.path.stat().st_mtime
            content = self.path.read_bytes()
            envelope = json.loads(content.decode("utf-8"))
            records = (
                envelope.get("triggers") if isinstance(envelope, dict) else envelope
            )
            if not isinstance(records, list) or any(
                not isinstance(item, dict) for item in records
            ):
                raise ValueError("triggers.json has an invalid record envelope")
            return records
        except (OSError, ValueError) as exc:
            digest = hashlib.sha256(content or str(exc).encode()).hexdigest()[:16]
            copied = None
            if content is not None:
                try:
                    copied = next(
                        self.path.parent.glob(f"{self.path.name}.broken-*-{digest}"),
                        None,
                    )
                    if copied is None:
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                        copied = self.path.with_name(
                            f"{self.path.name}.broken-{stamp}-{digest}"
                        )
                        fd = os.open(
                            copied, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                        )
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                except OSError:
                    copied = None
            kept = (
                f" A copy is kept at {copied}."
                if copied
                else " Its contents have not been changed."
            )
            self.unreadable = {
                "file": str(self.path),
                "said": f"{self.path} could not be read, so its automations cannot be listed or changed."
                + kept,
                "remedy": "Repair the file or restore a readable copy, then reload this page.",
            }
            identity = (str(self.path), digest)
            if identity not in _REPORTED_UNREADABLE:
                _REPORTED_UNREADABLE.add(identity)
                logger.warning(
                    "%s %s", self.unreadable["said"], self.unreadable["remedy"]
                )
            if strict:
                raise ValueError("triggers.json is unreadable or malformed") from exc
            return []

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
            if enabled:
                from gideon.automation.triggers.arm import arm, needs_arming

                if needs_arming(loaded.trigger):
                    loaded.trigger.next_fire_at = arm(loaded.trigger)
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

    def unreadable_status(self) -> list[dict[str, str]]:
        return self._document.unreadable_status()

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
            with record_files.locked_store(self.base_dir):
                changes = RecordMutation(self._document.read(strict=True))
                yield changes
                if changes.changed:
                    self._write(changes.rows)

    def save_all(self, triggers: list[Trigger]) -> int:
        with self._file_lock():
            with record_files.locked_store(self.base_dir):
                snapshot = [trigger.to_dict() for trigger in triggers]
                self._write(snapshot)
        return len(snapshot)

    def upsert(self, trigger: Trigger) -> Trigger:
        from gideon.automation.triggers import routing

        routed = routing.route_upsert(trigger, native=self)
        if routed is not None:
            return routed
        with self._mutation() as changes:
            from gideon.automation.triggers.grants import narrow

            previous = next(
                (
                    LoadedTrigger.parse(record).trigger
                    for record in changes.rows
                    if RecordMutation.identity(record) == trigger.id
                ),
                None,
            )
            existing = previous is not None
            if previous is None:
                from gideon.automation.triggers.models import Trigger

                previous = Trigger(id=trigger.id, name=trigger.name, kind=trigger.kind)
            narrow(trigger, previous)
            if existing:
                from gideon.automation.triggers.arm import next_fire_after_edit

                rearmed = next_fire_after_edit(previous, trigger)
                if rearmed is not None:
                    trigger.next_fire_at = rearmed
            changes.replace(trigger)
        return trigger

    def delete(self, trigger_id: str) -> bool:
        from gideon.automation.triggers import routing

        routed = routing.route_delete(trigger_id, native=self)
        if routed is not None:
            return routed
        with self._mutation() as changes:
            from gideon.automation.triggers import grants

            try:
                grants.revoke(trigger_id)
            except OSError:
                logger.warning("could not revoke trigger grant for %s", trigger_id)
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
