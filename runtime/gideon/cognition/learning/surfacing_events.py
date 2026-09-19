from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)
DEFAULT_RETENTION_DAYS = 90
DEFAULT_READ_LIMIT = 5000

_COLUMNS = (
    "kind",
    "entity",
    "arm",
    "confidence",
    "used",
    "query",
    "session",
    "created_ts",
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS surfacing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    entity TEXT NOT NULL DEFAULT '',
    arm TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    used INTEGER NOT NULL DEFAULT 0,
    query TEXT NOT NULL DEFAULT '',
    session TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_surfacing_ts ON surfacing_events(created_ts);
CREATE INDEX IF NOT EXISTS idx_surfacing_kind ON surfacing_events(kind, arm);
"""


def _historical_number(data: dict[str, Any], key: str) -> float:
    try:
        return float(data.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class SurfacingEvent:
    kind: str
    entity: str
    arm: str = ""
    confidence: float = 0.0
    used: bool = False
    query: str = ""
    session: str = ""
    created_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        values = (
            self.kind,
            self.entity,
            self.arm,
            round(float(self.confidence), 4),
            bool(self.used),
            self.query,
            self.session,
            self.created_ts,
        )
        return dict(zip(_COLUMNS, values))

    @classmethod
    def from_dict(cls, data: Any) -> SurfacingEvent | None:
        if isinstance(data, dict):
            kind = str(data.get("kind", "") or "")
            if kind:
                confidence = _historical_number(data, "confidence")
                stamp = _historical_number(data, "created_ts")
                return cls(
                    kind=kind,
                    entity=str(data.get("entity", "") or ""),
                    arm=str(data.get("arm", "") or ""),
                    confidence=confidence,
                    used=bool(data.get("used")),
                    query=str(data.get("query", "") or ""),
                    session=str(data.get("session", "") or ""),
                    created_ts=stamp,
                )
        return None


class _EventBatch:
    def __init__(self, events: Iterable[SurfacingEvent], stamp: float) -> None:
        self.rows: list[tuple[Any, ...]] = []
        for event in events:
            if event is None or not event.kind:
                continue
            self.rows.append(
                (
                    event.kind,
                    event.entity,
                    event.arm,
                    float(event.confidence),
                    1 if event.used else 0,
                    event.query,
                    event.session,
                    event.created_ts or stamp,
                )
            )

    def append(self, cursor: Any) -> int:
        fields = ", ".join(_COLUMNS)
        slots = ", ".join("?" for _ in _COLUMNS)
        cursor.executemany(
            f"INSERT INTO surfacing_events ({fields}) VALUES ({slots});", self.rows
        )
        return len(self.rows)


class _EventSelection:
    def __init__(
        self, days: int | None, kind: str, limit: int, now: float | None
    ) -> None:
        predicates: list[tuple[str, Any]] = []
        if days is not None:
            stamp = time.time() if now is None else now
            predicates.append(("created_ts >= ?", stamp - max(1, int(days)) * 86400))
        if kind:
            predicates.append(("kind = ?", kind))
        where = (
            " WHERE " + " AND ".join(clause for clause, _ in predicates)
            if predicates
            else ""
        )
        self.arguments = tuple(value for _, value in predicates) + (
            max(1, int(limit or DEFAULT_READ_LIMIT)),
        )
        self.statement = (
            f"SELECT {', '.join(_COLUMNS)} FROM surfacing_events{where} "
            "ORDER BY created_ts DESC, id DESC LIMIT ?;"
        )

    def fetch(self, cursor: Any) -> list[Any]:
        return cursor.execute(self.statement, self.arguments).fetchall()


class SurfacingEventStore:
    def __init__(self, base_dir: Path | str | None = None) -> None:
        from gideon.cognition.learning.staging import StagingStore

        self._staging = StagingStore(base_dir)
        self._lock = threading.RLock()
        self._bootstrapped = False

    @property
    def path(self) -> Path:
        return self._staging.path

    def close(self) -> None:
        self._staging.close()

    def _ensure(self) -> None:
        if not self._bootstrapped:
            with self._staging._cursor() as cursor:
                cursor.executescript(_SCHEMA)
            self._bootstrapped = True

    @contextmanager
    def _write(self):
        self._ensure()
        with self._lock:
            with self._staging._cursor() as cursor:
                yield cursor

    def record(
        self, events: Iterable[SurfacingEvent], *, now: float | None = None
    ) -> int:
        stamp = time.time() if now is None else now
        batch = _EventBatch(events, stamp)
        if batch.rows:
            with self._write() as cursor:
                return batch.append(cursor)
        return 0

    def read(
        self,
        *,
        days: int | None = None,
        kind: str = "",
        limit: int = DEFAULT_READ_LIMIT,
        now: float | None = None,
    ) -> list[SurfacingEvent]:
        try:
            self._ensure()
        except Exception:
            logger.debug("surfacing_events unavailable", exc_info=True)
            return []
        selection = _EventSelection(days, kind, limit, now)
        try:
            with self._staging._cursor() as cursor:
                rows = selection.fetch(cursor)
        except Exception:
            logger.debug("surfacing_events read failed", exc_info=True)
            return []
        parsed = map(lambda row: SurfacingEvent.from_dict(dict(row)), rows)
        return [event for event in parsed if event is not None]

    def prune(
        self, *, retention_days: int = DEFAULT_RETENTION_DAYS, now: float | None = None
    ) -> int:
        stamp = time.time() if now is None else now
        cutoff = stamp - max(1, int(retention_days)) * 86400
        try:
            with self._write() as cursor:
                cursor.execute(
                    "DELETE FROM surfacing_events WHERE created_ts < ?;", (cutoff,)
                )
                removed = int(cursor.rowcount or 0)
            return removed
        except Exception:
            logger.debug("surfacing_events prune failed", exc_info=True)
            return 0
