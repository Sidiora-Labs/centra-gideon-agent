"""SQLite statement ownership and calendar projections for capture staging."""

from __future__ import annotations

import json
from datetime import datetime
from itertools import takewhile


class CaptureQueries:
    def __init__(self, cursor):
        self.cursor = cursor

    def rows(self, statement, *parameters):
        return self.cursor.execute(statement, parameters).fetchall()

    def value(self, statement, *parameters):
        return self.cursor.execute(statement, parameters).fetchone()[0]

    def insert(self, table, values):
        columns = ", ".join(values)
        placeholders = ", ".join(["?"] * len(values))
        self.cursor.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders});",
            tuple(values.values()),
        )
        return int(self.cursor.lastrowid or 0)

    def backlog(self):
        return int(
            self.value("SELECT COUNT(*) FROM staging WHERE consumed_by IS NULL;")
        )

    def newest(self, table, column):
        return self.value(f"SELECT MAX({column}) FROM {table};")


class CaptureReports:
    @staticmethod
    def health(days, outcomes, staged, recent, quiet, error):
        totals = dict((row["outcome"], int(row["n"])) for row in outcomes)
        return dict(
            days=days,
            passes=sum(totals.values()),
            by_outcome=totals,
            staged_entries=staged,
            errors=totals.get(error, 0),
            cost_usd=round(sum(float(row["cost"] or 0.0) for row in outcomes), 6),
            all_ok_streak=sum(
                1 for _ in takewhile(lambda row: row[0] == quiet, recent)
            ),
        )

    @staticmethod
    def costs(rows):
        result = []
        for row in rows:
            result.append(
                dict(
                    op=str(row["cadence"]),
                    passes=int(row["passes"] or 0),
                    cost_usd=round(float(row["cost"] or 0.0), 6),
                )
            )
        return sorted(
            result, key=lambda item: (-float(item["cost_usd"]), str(item["op"]))
        )

    @staticmethod
    def utilization(row):
        samples = int(row["n"])
        budget, used = float(row["budget"] or 0.0), float(row["used"] or 0.0)
        ratio = None
        if samples and budget > 0:
            ratio = round(used / budget, 4)
        return {"samples": samples, "mean": ratio}


class CalendarCapture:
    def __init__(self, span, now):
        self.span = span
        self.days = {}
        for age in range(span):
            key = self.date(now - age * 86400)
            self.days[key] = dict(
                day=key,
                passes=0,
                by_outcome={},
                produced=0,
                errors=0,
                staged=0,
                cost_usd=0.0,
                proposal_ids=[],
            )

    @staticmethod
    def date(timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")

    def consume(self, flushes, captures, error):
        for row in flushes:
            bucket = self.days.get(self.date(float(row["created_ts"])))
            if bucket is None:
                continue
            label = str(row["outcome"])
            bucket["passes"] += 1
            counters = bucket["by_outcome"]
            counters[label] = counters.get(label, 0) + 1
            bucket["cost_usd"] += float(row["cost_usd"] or 0.0)
            bucket["errors"] += int(label == error)
            try:
                proposals = json.loads(row["proposal_ids"] or "[]")
            except (TypeError, ValueError):
                proposals = []
            if isinstance(proposals, list) and proposals:
                bucket["proposal_ids"].extend(map(str, proposals))
                bucket["produced"] += len(proposals)
        for row in captures:
            bucket = self.days.get(self.date(float(row["created_ts"])))
            if bucket is not None:
                bucket["staged"] += 1

    def result(self, ever):
        ordered = [self.days[key] for key in sorted(self.days)]
        for bucket in ordered:
            bucket["cost_usd"] = round(bucket["cost_usd"], 6)
        return dict(
            days=self.span,
            buckets=ordered,
            silent_days=[b["day"] for b in ordered if not b["passes"]],
            error_days=[b["day"] for b in ordered if b["errors"]],
            produced_total=sum(b["produced"] for b in ordered),
            cost_usd=round(sum(b["cost_usd"] for b in ordered), 6),
            has_ever_run=ever,
        )


STAGING_SCHEMA = """
            CREATE TABLE IF NOT EXISTS staging (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                day           TEXT NOT NULL,
                cadence       TEXT NOT NULL,
                kind          TEXT NOT NULL,
                content       TEXT NOT NULL,
                content_hash  TEXT NOT NULL,
                session_key   TEXT NOT NULL DEFAULT '',
                created_ts    REAL NOT NULL,
                meta          TEXT NOT NULL DEFAULT '{}',
                consumed_by   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_staging_day ON staging(day);
            CREATE INDEX IF NOT EXISTS idx_staging_hash ON staging(content_hash);

            CREATE TABLE IF NOT EXISTS flush_records (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                cadence      TEXT NOT NULL,
                outcome      TEXT NOT NULL,
                detail       TEXT NOT NULL DEFAULT '',
                staged_count INTEGER NOT NULL DEFAULT 0,
                proposal_ids TEXT NOT NULL DEFAULT '[]',
                cost_usd     REAL NOT NULL DEFAULT 0.0,
                created_ts   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_flush_ts ON flush_records(created_ts);

            CREATE TABLE IF NOT EXISTS batch_passes (
                input_hash TEXT PRIMARY KEY,
                created_ts REAL NOT NULL,
                detail     TEXT NOT NULL DEFAULT ''
            );

            -- One row per ambient render (LEARN-R14b). `ambient.report()` computed
            -- exactly this and sent it to a debug log, so the budget-utilization the
            -- health composite needs had no persisted writer at all: a panel reading
            -- it would have rendered from a key nothing wrote.
            CREATE TABLE IF NOT EXISTS allocation_samples (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                used_tokens  INTEGER NOT NULL,
                budget_tokens INTEGER NOT NULL,
                created_ts   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alloc_ts ON allocation_samples(created_ts);

            -- One row per heuristic per sweep (§2.5's ablation-delta rule).
            CREATE TABLE IF NOT EXISTS ablation_sweeps (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_ts   REAL NOT NULL,
                heuristic  TEXT NOT NULL,
                delta      REAL NOT NULL,
                verdict    TEXT NOT NULL,
                items      INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ablation_ts ON ablation_sweeps(sweep_ts);
            """
