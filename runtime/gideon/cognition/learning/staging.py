"""Capture staging, batch admission and operational history for learning."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from gideon.cognition.learning.hygiene import fingerprint

from .staging_journal import (
    STAGING_SCHEMA,
    CalendarCapture,
    CaptureQueries,
    CaptureReports,
)

logger = logging.getLogger(__name__)

DB_FILE = "learning.db"

DEFAULT_RETENTION_DAYS = 30

DEFAULT_BATCH_WINDOW_SECS = 900.0


class FlushOutcome(str, Enum):
    """The three things an extraction pass can honestly report."""

    FLUSH_OK = "flush_ok"
    FLUSH_PRODUCED = "flush_produced"
    FLUSH_ERROR = "flush_error"
    FLUSH_SKIPPED = "flush_skipped"


@dataclass(frozen=True)
class StagingEntry:
    """One raw captured signal, as stored."""

    id: int
    day: str
    cadence: str
    kind: str
    content: str
    content_hash: str
    session_key: str
    created_ts: float
    meta: dict[str, Any]


def input_hash(items: list[str]) -> str:
    """A stable hash of an expensive pass's inputs, for idempotence.

    Order-insensitive: the same set of staging entries yields the same hash
    regardless of the order the query returned them, so a re-run that happens to
    read rows differently is still recognised as the same work.
    """
    return fingerprint("\n".join(sorted(items)))


class StagingStore:
    """Serialize capture writes and expose bounded reports over the journal."""

    ALLOCATION_KEEP = 500

    def __init__(self, base_dir: Path | str | None = None) -> None:
        directory = Path(base_dir) if base_dir else _default_home()
        self._base, self._path = directory, directory / DB_FILE
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._base.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._bootstrap(self._conn)
        return self._conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            connection = self._connect()
            cursor = connection.cursor()
            try:
                yield cursor
            except BaseException:
                raise
            else:
                connection.commit()
            finally:
                cursor.close()

    @staticmethod
    def _bootstrap(conn: sqlite3.Connection) -> None:
        for operation, statement in (
            (conn.executescript, STAGING_SCHEMA),
            (
                conn.execute,
                "CREATE INDEX IF NOT EXISTS idx_staging_consumed ON staging(consumed_by) WHERE consumed_by IS NOT NULL;",
            ),
        ):
            operation(statement)
        conn.commit()

    def close(self) -> None:
        with self._lock:
            connection = self._conn
            if connection is None:
                return
            connection.close()
            self._conn = None

    def stage(
        self,
        *,
        cadence: str,
        kind: str,
        content: str,
        session_key: str = "",
        meta: dict[str, Any] | None = None,
    ) -> int:
        if not content or not content.strip():
            return 0
        digest, day = fingerprint(content), _today()
        with self._cursor() as cursor:
            query = CaptureQueries(cursor)
            duplicate = query.rows(
                "SELECT id FROM staging WHERE day = ? AND content_hash = ? LIMIT 1;",
                day,
                digest,
            )
            if duplicate:
                return 0
            return query.insert(
                "staging",
                dict(
                    day=day,
                    cadence=str(cadence),
                    kind=str(kind),
                    content=content,
                    content_hash=digest,
                    session_key=session_key or "",
                    created_ts=time.time(),
                    meta=json.dumps(meta or {}, ensure_ascii=False),
                ),
            )

    def record_flush(
        self,
        *,
        cadence: str,
        outcome: FlushOutcome,
        detail: str = "",
        staged_count: int = 0,
        proposal_ids: list[str] | None = None,
        cost_usd: float = 0.0,
    ) -> int:
        with self._cursor() as cursor:
            return CaptureQueries(cursor).insert(
                "flush_records",
                dict(
                    cadence=str(cadence),
                    outcome=outcome.value,
                    detail=detail[:2000],
                    staged_count=int(staged_count),
                    proposal_ids=json.dumps(list(proposal_ids or [])),
                    cost_usd=float(cost_usd),
                    created_ts=time.time(),
                ),
            )

    @contextmanager
    def flush(self, cadence: str) -> Iterator[dict[str, Any]]:
        receipt: dict[str, Any] = {
            "staged": 0,
            "proposals": [],
            "cost_usd": 0.0,
        }
        try:
            yield receipt
        except Exception as failure:
            self.record_flush(
                cadence=cadence,
                outcome=FlushOutcome.FLUSH_ERROR,
                detail=f"{type(failure).__name__}: {failure}",
                cost_usd=float(receipt.get("cost_usd") or 0.0),
            )
            raise
        else:
            count = int(receipt.get("staged") or 0)
            identifiers = list(receipt.get("proposals") or [])
            outcome = FlushOutcome.FLUSH_OK
            if count or identifiers:
                outcome = FlushOutcome.FLUSH_PRODUCED
            self.record_flush(
                cadence=cadence,
                outcome=outcome,
                detail=str(receipt.get("detail") or ""),
                staged_count=count,
                proposal_ids=list(map(str, identifiers)),
                cost_usd=float(receipt.get("cost_usd") or 0.0),
            )

    def pending(self, *, limit: int = 500, cadence: str = "") -> list[StagingEntry]:
        filters = ["consumed_by IS NULL"]
        arguments: list[Any] = []
        if cadence:
            filters.append("cadence = ?")
            arguments.append(cadence)
        arguments.append(int(limit))
        statement = (
            "SELECT * FROM staging WHERE "
            + " AND ".join(filters)
            + " ORDER BY created_ts ASC LIMIT ?;"
        )
        with self._cursor() as cursor:
            return list(
                map(_row_to_entry, CaptureQueries(cursor).rows(statement, *arguments))
            )

    def pending_count(self) -> int:
        with self._cursor() as cursor:
            return CaptureQueries(cursor).backlog()

    def mark_consumed(self, ids: list[int], marker: str) -> int:
        if not ids:
            return 0
        with self._cursor() as cursor:
            bindings = [(marker, int(identifier)) for identifier in ids]
            cursor.executemany(
                "UPDATE staging SET consumed_by = ? WHERE id = ? AND consumed_by IS NULL;",
                bindings,
            )
            affected = cursor.rowcount
            return affected if affected and affected > 0 else len(ids)

    def sources_for(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join(["?"] * len(ids))
        with self._cursor() as cursor:
            rows = CaptureQueries(cursor).rows(
                f"SELECT id, day, cadence, kind, content_hash FROM staging WHERE id IN ({placeholders});",
                *[int(identifier) for identifier in ids],
            )
        return list(map(dict, rows))

    def should_batch(
        self,
        *,
        min_entries: int = 5,
        window_secs: float = DEFAULT_BATCH_WINDOW_SECS,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self._cursor() as cursor:
            query = CaptureQueries(cursor)
            if query.backlog() < max(1, min_entries):
                return False
            previous = query.newest("batch_passes", "created_ts")
        if previous is None:
            return True
        return timestamp - float(previous) >= window_secs

    def claim_batch(self, ihash: str, *, detail: str = "") -> bool:
        with self._cursor() as cursor:
            try:
                CaptureQueries(cursor).insert(
                    "batch_passes",
                    dict(input_hash=ihash, created_ts=time.time(), detail=detail[:500]),
                )
            except sqlite3.IntegrityError:
                return False
            else:
                return True

    def health(self, *, days: int = 7, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        cutoff = timestamp - days * 86400
        with self._cursor() as cursor:
            query = CaptureQueries(cursor)
            outcomes = query.rows(
                "SELECT outcome, COUNT(*) AS n, SUM(cost_usd) AS cost FROM flush_records WHERE created_ts >= ? GROUP BY outcome;",
                cutoff,
            )
            staged = int(
                query.value(
                    "SELECT COUNT(*) FROM staging WHERE created_ts >= ?;", cutoff
                )
            )
            recent = query.rows(
                "SELECT outcome FROM flush_records ORDER BY created_ts DESC LIMIT 50;"
            )
        return CaptureReports.health(
            days,
            outcomes,
            staged,
            recent,
            FlushOutcome.FLUSH_OK.value,
            FlushOutcome.FLUSH_ERROR.value,
        )

    def cost_by_op(
        self, *, days: int = 7, now: float | None = None
    ) -> list[dict[str, Any]]:
        cutoff = (time.time() if now is None else now) - max(1, days) * 86400
        with self._cursor() as cursor:
            grouped = CaptureQueries(cursor).rows(
                "SELECT cadence, COUNT(*) AS passes, SUM(cost_usd) AS cost FROM flush_records WHERE created_ts >= ? GROUP BY cadence;",
                cutoff,
            )
        return CaptureReports.costs(grouped)

    def record_allocation(
        self, *, used_tokens: int, budget_tokens: int, now: float | None = None
    ) -> bool:
        if budget_tokens <= 0:
            return False
        timestamp = time.time() if now is None else now
        with self._cursor() as cursor:
            CaptureQueries(cursor).insert(
                "allocation_samples",
                dict(
                    used_tokens=max(0, int(used_tokens)),
                    budget_tokens=int(budget_tokens),
                    created_ts=timestamp,
                ),
            )
            cursor.execute(
                "DELETE FROM allocation_samples WHERE id <= (SELECT MAX(id) - ? FROM allocation_samples);",
                (self.ALLOCATION_KEEP,),
            )
        return True

    def utilization(self, *, days: int = 7, now: float | None = None) -> dict[str, Any]:
        cutoff = (time.time() if now is None else now) - max(1, days) * 86400
        with self._cursor() as cursor:
            row = CaptureQueries(cursor).rows(
                "SELECT COUNT(*) AS n, SUM(used_tokens) AS used, SUM(budget_tokens) AS budget FROM allocation_samples WHERE created_ts >= ?;",
                cutoff,
            )[0]
        return CaptureReports.utilization(row)

    def record_ablation(
        self, rows: list[dict[str, Any]], *, now: float | None = None
    ) -> int:
        if not rows:
            return 0
        timestamp = time.time() if now is None else now
        with self._cursor() as cursor:
            query = CaptureQueries(cursor)
            for row in rows:
                query.insert(
                    "ablation_sweeps",
                    dict(
                        sweep_ts=timestamp,
                        heuristic=str(row.get("heuristic", "")),
                        delta=float(row.get("delta", 0.0) or 0.0),
                        verdict=str(row.get("verdict", "")),
                        items=int(row.get("items", 0) or 0),
                    ),
                )
        return len(rows)

    def latest_ablation(self) -> dict[str, Any]:
        with self._cursor() as cursor:
            query = CaptureQueries(cursor)
            timestamp = query.newest("ablation_sweeps", "sweep_ts")
            if not timestamp:
                return {}
            rows = query.rows(
                "SELECT heuristic, delta, verdict, items FROM ablation_sweeps WHERE sweep_ts = ? ORDER BY delta;",
                timestamp,
            )
        return dict(
            at=datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat(),
            rows=[
                dict(
                    heuristic=str(row["heuristic"]),
                    delta=round(float(row["delta"]), 6),
                    verdict=str(row["verdict"]),
                    items=int(row["items"]),
                )
                for row in rows
            ],
        )

    def ablation_due(
        self, *, every_secs: float = 86400.0, now: float | None = None
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self._cursor() as cursor:
            latest = CaptureQueries(cursor).newest("ablation_sweeps", "sweep_ts")
        elapsed = timestamp - (float(latest) if latest else 0.0)
        return elapsed >= max(1.0, every_secs)

    def week(self, *, days: int = 7, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        span = max(1, days)
        cutoff = timestamp - span * 86400
        with self._cursor() as cursor:
            query = CaptureQueries(cursor)
            flushes = query.rows(
                "SELECT outcome, cost_usd, proposal_ids, created_ts FROM flush_records WHERE created_ts >= ? ORDER BY created_ts;",
                cutoff,
            )
            captures = query.rows(
                "SELECT created_ts FROM staging WHERE created_ts >= ?;", cutoff
            )
            ever = bool(
                query.value("SELECT EXISTS(SELECT 1 FROM flush_records LIMIT 1);")
            )
        calendar = CalendarCapture(span, timestamp)
        calendar.consume(flushes, captures, FlushOutcome.FLUSH_ERROR.value)
        return calendar.result(ever)

    def prune(
        self, *, retention_days: int = DEFAULT_RETENTION_DAYS, now: float | None = None
    ):
        cutoff = (time.time() if now is None else now) - max(1, retention_days) * 86400
        with self._cursor() as cursor:
            cursor.execute(
                "DELETE FROM staging WHERE consumed_by IS NOT NULL AND created_ts < ?;",
                (cutoff,),
            )
            return int(cursor.rowcount or 0)


_INSTANCE: StagingStore | None = None
_INSTANCE_LOCK = threading.Lock()


def get_store(base_dir: Path | str | None = None) -> StagingStore:
    if base_dir is not None:
        return StagingStore(base_dir)
    global _INSTANCE
    with _INSTANCE_LOCK:
        cached = _INSTANCE
        if cached is None:
            cached = StagingStore()
            _INSTANCE = cached
        return cached


def reset_store() -> None:
    global _INSTANCE
    with _INSTANCE_LOCK:
        cached = _INSTANCE
        if cached is None:
            return
        cached.close()
        _INSTANCE = None


def _default_home() -> Path:
    try:
        from gideon.core.config.loader import config_dir

        return Path(config_dir())
    except Exception:  # pragma: no cover - config import is exercised elsewhere
        return Path.home() / ".gideon"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _row_to_entry(row: sqlite3.Row) -> StagingEntry:
    try:
        metadata = json.loads(row["meta"] or "{}")
    except Exception:
        metadata = {}
    fields: dict = {"id": int(row["id"])}
    fields.update(
        (key, str(row[key]))
        for key in ("day", "cadence", "kind", "content", "content_hash")
    )
    fields.update(
        session_key=str(row["session_key"] or ""),
        created_ts=float(row["created_ts"]),
        meta=metadata if isinstance(metadata, dict) else {},
    )
    return StagingEntry(**fields)
