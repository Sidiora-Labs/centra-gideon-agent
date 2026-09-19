"""Buffered entity usage and context evidence stored alongside learning staging."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
TRACKED_KINDS = ("skill", "template")
KIND_EVENTS: dict[str, tuple[str, ...]] = {
    "skill": ("surfaced", "loaded"),
    "template": ("surfaced", "run", "run_success", "run_failure"),
}
PROMOTE_MIN_USES = 3
PROMOTE_MIN_CONTEXTS = 2
PROMOTE_MAX_IDLE_DAYS = 30.0
_COUNTER_EVENTS = {
    "surfaced": ("surfaced",),
    "used": ("loaded", "run"),
    "successes": ("run_success",),
    "failures": ("run_failure",),
}
_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    kind TEXT NOT NULL, entity TEXT NOT NULL,
    surfaced INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT '', last_used_at TEXT NOT NULL DEFAULT '',
    last_surfaced_at TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT 'agent',
    pinned INTEGER NOT NULL DEFAULT 0, contexts TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (kind, entity)
);
CREATE TABLE IF NOT EXISTS active_days (day TEXT PRIMARY KEY);
"""


@dataclass
class UsageRecord:
    kind: str
    entity: str
    surfaced: int = 0
    used: int = 0
    successes: int = 0
    failures: int = 0
    first_seen_at: str = ""
    last_used_at: str = ""
    last_surfaced_at: str = ""
    source_type: str = "agent"
    pinned: bool = False
    contexts: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float | None:
        completed = self.successes + self.failures
        return self.successes / completed if completed else None

    @property
    def context_diversity(self) -> int:
        return len(self.contexts)


class _UsageDelta:
    def __init__(self, key, slot, stamp):
        self.kind, self.entity, self.event = key
        self.slot, self.stamp = slot, stamp
        self.count = max(1, int(round(slot["count"])))

    def fields(self, row):
        count = self.count
        contexts = (
            set(filter(None, (row["contexts"] or "").split("\x1f"))) if row else set()
        )
        contexts.update(self.slot["contexts"])
        values = dict(kind=self.kind, entity=self.entity)
        values.update(
            {
                column: count if self.event in events else 0
                for column, events in _COUNTER_EVENTS.items()
            }
        )
        active = any(values[column] for column in ("used", "successes", "failures"))
        values.update(
            first_seen_at=(row["first_seen_at"] if row else "") or self.stamp,
            last_used_at=self.stamp if active else "",
            last_surfaced_at=self.stamp if values["surfaced"] else "",
            source_type=self.slot["source_type"] or "agent",
            contexts="\x1f".join(sorted(contexts)),
        )
        return values


class _UsageRows:
    def __init__(self, cursor):
        self.cursor = cursor

    def accumulate(self, delta):
        row = self.cursor.execute(
            "SELECT contexts, first_seen_at FROM usage WHERE kind = ? AND entity = ?;",
            (delta.kind, delta.entity),
        ).fetchone()
        values = delta.fields(row)
        columns = list(values)
        changes = [
            f"{column} = {column} + excluded.{column}" for column in _COUNTER_EVENTS
        ]
        changes.extend(
            (
                "last_used_at = CASE WHEN excluded.used > 0 OR excluded.successes > 0 OR excluded.failures > 0 "
                "THEN excluded.last_used_at ELSE usage.last_used_at END",
                "last_surfaced_at = CASE WHEN excluded.surfaced > 0 THEN excluded.last_surfaced_at ELSE usage.last_surfaced_at END",
                "contexts = excluded.contexts",
            )
        )
        sql = (
            "INSERT INTO usage ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ") ON CONFLICT(kind, entity) DO UPDATE SET "
            + ", ".join(changes)
            + ";"
        )
        self.cursor.execute(sql, tuple(values.values()))

    def mark(self, day):
        self.cursor.execute(
            "INSERT OR IGNORE INTO active_days (day) VALUES (?);", (day,)
        )

    def flags(self, kind, entity, pinned, source_type):
        self.cursor.execute(
            "INSERT OR IGNORE INTO usage (kind, entity, first_seen_at) VALUES (?, ?, ?);",
            (kind, entity, _now()),
        )
        changes = []
        if pinned is not None:
            changes.append(("pinned", 1 if pinned else 0))
        if source_type:
            changes.append(("source_type", source_type))
        for column, value in changes:
            self.cursor.execute(
                f"UPDATE usage SET {column} = ? WHERE kind = ? AND entity = ?;",
                (value, kind, entity),
            )


class UsageStore:
    def __init__(self, base_dir: Path | str | None = None) -> None:
        from gideon.cognition.learning.staging import StagingStore

        self._staging = StagingStore(base_dir)
        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._bootstrapped = False

    @property
    def path(self) -> Path:
        return self._staging.path

    def close(self) -> None:
        self._staging.close()

    def _ensure(self) -> None:
        if not self._bootstrapped:
            with self._staging._cursor() as cursor:
                cursor.executescript(_USAGE_SCHEMA)
            self._bootstrapped = True

    def record(
        self,
        *,
        kind: str,
        entity: str,
        event: str,
        context: str = "",
        source_type: str = "",
        immediate: bool = False,
    ) -> bool:
        if kind not in TRACKED_KINDS:
            logger.debug("usage not tracked for kind %r (by design)", kind)
            return False
        if event not in KIND_EVENTS.get(kind, ()):
            logger.debug("event %r is not defined for kind %r", event, kind)
            return False
        if not entity:
            return False
        with self._lock:
            self._reinforce((kind, entity, event), context, source_type)
        if immediate:
            self.flush()
        return True

    def _reinforce(self, key, context, source_type):
        if key not in self._pending:
            self._pending[key] = dict(
                count=0, contexts=set(), source_type=source_type, ts=0.0
            )
        slot = self._pending[key]
        from gideon.cognition.learning.decay import reinforcement_weight

        stamp = time.time()
        increment = reinforcement_weight(stamp - slot["ts"]) if slot["ts"] else 1.0
        slot["count"] = slot["count"] + increment
        slot["ts"] = stamp
        if context:
            slot["contexts"].add(context)
        if source_type:
            slot["source_type"] = source_type

    def flush(self) -> int:
        with self._lock:
            snapshot = self._pending
            self._pending = {}
        if not snapshot:
            return 0
        self._ensure()
        stamp = _now()
        written = 0
        with self._staging._cursor() as cursor:
            table = _UsageRows(cursor)
            for key, slot in snapshot.items():
                table.accumulate(_UsageDelta(key, slot, stamp))
                written += 1
            table.mark(stamp[:10])
        return written

    def get(self, kind: str, entity: str) -> UsageRecord | None:
        self._ensure()
        with self._staging._cursor() as cursor:
            rows = cursor.execute(
                "SELECT * FROM usage WHERE kind = ? AND entity = ?;", (kind, entity)
            )
            row = rows.fetchone()
        return None if not row else _to_record(row)

    def list_kind(self, kind: str) -> list[UsageRecord]:
        self._ensure()
        with self._staging._cursor() as cursor:
            result = cursor.execute(
                "SELECT * FROM usage WHERE kind = ? ORDER BY entity;", (kind,)
            ).fetchall()
        return list(map(_to_record, result))

    def set_flags(
        self,
        kind: str,
        entity: str,
        *,
        pinned: bool | None = None,
        source_type: str = "",
    ) -> bool:
        self._ensure()
        with self._staging._cursor() as cursor:
            _UsageRows(cursor).flags(kind, entity, pinned, source_type)
        return True

    def active_days(self) -> list[str]:
        self._ensure()
        with self._staging._cursor() as cursor:
            dates = cursor.execute("SELECT day FROM active_days ORDER BY day;")
            return [row[0] for row in dates]

    def mark_active(self, day: str = "") -> None:
        self._ensure()
        with self._staging._cursor() as cursor:
            _UsageRows(cursor).mark(day or _now()[:10])

    def import_skill_sidecar(self, sidecar: Path) -> int:
        import json

        try:
            document = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(document, dict):
            return 0
        self._ensure()
        imported = 0
        with self._staging._cursor() as cursor:
            for name, legacy in document.items():
                if isinstance(legacy, dict):
                    count = int(legacy.get("count", 0) or 0)
                    last = str(legacy.get("last_used_at", "") or "")
                    parameters = (str(name), count, last, last or _now())
                    cursor.execute(
                        "INSERT OR IGNORE INTO usage (kind, entity, used, last_used_at, first_seen_at, source_type) "
                        "VALUES ('skill', ?, ?, ?, ?, 'agent');",
                        parameters,
                    )
                    imported += 1
        if imported:
            logger.info(
                "imported %d legacy skill-usage row(s) from %s", imported, sidecar.name
            )
        return imported


def _to_record(row: Any) -> UsageRecord:
    values: dict = {name: str(row[name]) for name in ("kind", "entity")}
    values.update(
        {
            name: int(row[name] or 0)
            for name in ("surfaced", "used", "successes", "failures")
        }
    )
    values.update(
        {
            name: str(row[name] or "")
            for name in ("first_seen_at", "last_used_at", "last_surfaced_at")
        }
    )
    values.update(
        source_type=str(row["source_type"] or "agent"),
        pinned=bool(row["pinned"]),
        contexts=list(filter(None, str(row["contexts"] or "").split("\x1f"))),
    )
    return UsageRecord(**values)


def _now() -> str:
    stamp = datetime.now(timezone.utc)
    return stamp.isoformat()


def promotion_ready(
    record: UsageRecord, *, active_days_idle: float
) -> tuple[bool, str]:
    checks = (
        (
            lambda: record.used < PROMOTE_MIN_USES,
            lambda: f"only {record.used} use(s), need {PROMOTE_MIN_USES}",
        ),
        (
            lambda: record.context_diversity < PROMOTE_MIN_CONTEXTS,
            lambda: f"used in {record.context_diversity} context(s), need {PROMOTE_MIN_CONTEXTS}",
        ),
        (
            lambda: active_days_idle > PROMOTE_MAX_IDLE_DAYS,
            lambda: f"idle {active_days_idle:.0f} active days",
        ),
        (
            lambda: record.success_rate is not None and record.success_rate < 0.5,
            lambda: f"success rate {record.success_rate:.0%}",
        ),
    )
    for rejected, reason in checks:
        if rejected():
            return False, reason()
    return True, "multi-gate evidence met"
