"""Transactional counter operations for lesson support and reversal records."""

import threading
from contextlib import contextmanager

EVIDENCE_SCHEMA = """
                CREATE TABLE IF NOT EXISTS lesson_evidence (
                    lesson_key        TEXT PRIMARY KEY,
                    observations      INTEGER NOT NULL DEFAULT 0,
                    contradictions    INTEGER NOT NULL DEFAULT 0,
                    reversals         INTEGER NOT NULL DEFAULT 0,
                    voided            INTEGER NOT NULL DEFAULT 0,
                    human_authored    INTEGER NOT NULL DEFAULT 0,
                    first_observed_at TEXT NOT NULL DEFAULT '',
                    last_observed_at  TEXT NOT NULL DEFAULT '',
                    last_reversed_at  TEXT NOT NULL DEFAULT ''
                );
                -- The vacation-proof clock, shared with `usage.UsageStore` which
                -- also creates it. Two idempotent creators rather than one store
                -- depending on the other having opened the db first: whichever
                -- gets there first wins, and neither can be the reason the other
                -- reads a missing table.
                CREATE TABLE IF NOT EXISTS active_days (
                    day TEXT PRIMARY KEY
                );
                """


class LessonRepository:
    def __init__(self, owner, base_dir):
        from gideon.cognition.learning.staging import StagingStore

        self.owner = owner
        owner._staging = StagingStore(base_dir)
        owner._lock = threading.RLock()
        owner._bootstrapped = False

    def ensure(self):
        if not self.owner._bootstrapped:
            with self.owner._staging._cursor() as cursor:
                cursor.executescript(EVIDENCE_SCHEMA)
            self.owner._bootstrapped = True

    @contextmanager
    def transaction(self):
        with self.owner._lock, self.owner._staging._cursor() as cursor:
            yield cursor

    @staticmethod
    def upsert(cursor, key, values, updates):
        columns = ["lesson_key", *values]
        parameters = [key, *values.values()]
        query = "INSERT INTO lesson_evidence (" + ", ".join(columns) + ") VALUES ("
        query += (
            ", ".join("?" for _ in columns) + ") ON CONFLICT(lesson_key) DO UPDATE SET "
        )
        query += (
            ", ".join(
                f"{column} = {expression}" for column, expression in updates.items()
            )
            + ";"
        )
        cursor.execute(query, tuple(parameters))

    def record(self, api, event, key, human_authored=False):
        if not key:
            return 0 if event == "observation" else None
        self.owner._ensure()
        timestamp = api._now()
        if event == "observation":
            values = dict(
                observations=1,
                human_authored=1 if human_authored else 0,
                first_observed_at=timestamp,
                last_observed_at=timestamp,
            )
            updates = dict(
                observations="observations + 1",
                human_authored="MAX(human_authored, excluded.human_authored)",
                last_observed_at="excluded.last_observed_at",
            )
        elif event == "contradiction":
            values = dict(contradictions=1, last_observed_at=timestamp)
            updates = dict(contradictions="contradictions + 1")
        else:
            values = dict(reversals=1, voided=0, last_reversed_at=timestamp)
            updates = dict(
                reversals="reversals + 1",
                voided="observations",
                last_reversed_at="excluded.last_reversed_at",
            )
        with self.transaction() as cursor:
            self.upsert(cursor, key, values, updates)
            if event == "observation":
                row = cursor.execute(
                    "SELECT observations FROM lesson_evidence WHERE lesson_key = ?;",
                    (key,),
                ).fetchone()
            else:
                return None
        return int(row[0]) if row else 0

    def transfer(self, api, old_key, new_key):
        if not old_key or not new_key or old_key == new_key:
            return
        evidence = self.owner.evidence_for(old_key)
        if (
            evidence.observations <= 0
            and evidence.contradictions <= 0
            and evidence.reversals <= 0
        ):
            return
        self.owner._ensure()
        timestamp = api._now()
        counters = ("observations", "contradictions", "reversals", "voided")
        values: dict = {name: getattr(evidence, name) for name in counters}
        values.update(
            human_authored=1 if evidence.human_authored else 0,
            first_observed_at=evidence.first_observed_at or timestamp,
            last_observed_at=evidence.last_observed_at or timestamp,
            last_reversed_at=evidence.last_reversed_at,
        )
        updates = {name: f"{name} + excluded.{name}" for name in counters}
        updates.update(
            human_authored="MAX(human_authored, excluded.human_authored)",
            last_observed_at="excluded.last_observed_at",
        )
        with self.transaction() as cursor:
            self.upsert(cursor, new_key, values, updates)
            cursor.execute(
                "DELETE FROM lesson_evidence WHERE lesson_key = ?;", (old_key,)
            )

    def one(self, api, key):
        if not key:
            return api.LessonEvidence()
        self.owner._ensure()
        with self.owner._staging._cursor() as cursor:
            row = cursor.execute(
                "SELECT * FROM lesson_evidence WHERE lesson_key = ?;", (key,)
            ).fetchone()
        if row:
            return api._to_evidence(row)
        return api.LessonEvidence()

    def many(self, api, keys):
        selected = tuple(key for key in keys if key)
        if not selected:
            return {}
        self.owner._ensure()
        placeholders = ",".join("?" for _ in selected)
        with self.owner._staging._cursor() as cursor:
            rows = cursor.execute(
                f"SELECT * FROM lesson_evidence WHERE lesson_key IN ({placeholders});",
                selected,
            ).fetchall()
        result = {}
        for row in rows:
            result[str(row["lesson_key"])] = api._to_evidence(row)
        return result

    def days(self):
        self.owner._ensure()
        with self.owner._staging._cursor() as cursor:
            rows = cursor.execute("SELECT day FROM active_days ORDER BY day;")
            return list(map(lambda row: str(row[0]), rows))

    @staticmethod
    def decode(api, row):
        values: dict = {}
        for name in (
            "observations",
            "contradictions",
            "reversals",
            "voided",
            "human_authored",
        ):
            try:
                value = int(row[name])
            except (KeyError, IndexError, TypeError, ValueError):
                value = 0
            values[name] = bool(value) if name == "human_authored" else value
        for name in ("first_observed_at", "last_observed_at", "last_reversed_at"):
            try:
                values[name] = str(row[name] or "")
            except (KeyError, IndexError, TypeError):
                values[name] = ""
        return api.LessonEvidence(**values)


class EvidenceCache:
    @staticmethod
    def acquire(api, base_dir):
        location = "" if base_dir is None else str(api.Path(base_dir).resolve())
        with api._INSTANCE_LOCK:
            try:
                existing = api._INSTANCES[location]
            except KeyError:
                existing = None
            if existing is not None:
                return existing
            created = api.LessonEvidenceStore(base_dir)
            api._INSTANCES[location] = created
            return created

    @staticmethod
    def clear(api):
        with api._INSTANCE_LOCK:
            handles = iter(api._INSTANCES.values())
            for handle in handles:
                try:
                    handle.close()
                except Exception:
                    api.logger.debug(
                        "lesson evidence store close failed", exc_info=True
                    )
            api._INSTANCES.clear()
