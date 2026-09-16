"""Lexicon SQLite store (core LEX.2) — the personal vocabulary + learned corrections.

Its own ``lexicon.db`` (separate from knowledge.db) so it is trivially rebuildable from
the graph and keeps concerns apart (design open-question #2, leaning-that-way resolved to
its own file). Two tables:

* ``terms``       — canonical vocabulary (from graph entities / manual / learned), each
  with its Double Metaphone keys, an ``entity_type``, a ``weight`` (recency × frequency ×
  correction-count, drives bias-term ranking), ``source`` and ``enabled``.
* ``corrections`` — the learned ``heard → meant`` loop; ``count`` bumps each time the user
  fixes the same mishearing, and ``auto_apply`` flips once past threshold / "always fix".

Access mirrors KnowledgeStore (WAL, busy_timeout, Row factory, check_same_thread=False).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass

# -- public data classes -------------------------------------------------------


@dataclass
class LexiconTerm:
    id: str
    canonical: str
    aliases: list[str]
    phonetic_keys: list[str]
    entity_type: str
    weight: float
    source: str
    enabled: bool


@dataclass
class Correction:
    id: str
    heard: str
    meant: str
    phonetic_key: str
    count: int
    auto_apply: bool
    last_seen: str


# -- path helper ---------------------------------------------------------------


def lexicon_db_path() -> str:
    from gideon.core.config.loader import config_dir

    db_dir = os.path.join(str(config_dir()), "workspace", "lexicon")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "lexicon.db")


# -- schema DDL (table/column/index names are the on-disk contract) ------------

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS terms (
        id TEXT PRIMARY KEY,
        canonical TEXT NOT NULL,
        aliases_json TEXT DEFAULT '[]',
        phonetic_keys_json TEXT DEFAULT '[]',
        entity_type TEXT DEFAULT '',
        weight REAL DEFAULT 1.0,
        source TEXT DEFAULT 'graph',
        enabled INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_terms_canonical ON terms(canonical);
    CREATE INDEX IF NOT EXISTS idx_terms_weight ON terms(weight DESC);

    CREATE TABLE IF NOT EXISTS corrections (
        id TEXT PRIMARY KEY,
        heard TEXT NOT NULL,
        meant TEXT NOT NULL,
        phonetic_key TEXT DEFAULT '',
        count INTEGER DEFAULT 1,
        auto_apply INTEGER DEFAULT 0,
        last_seen TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_corr_pair ON corrections(heard, meant);

    -- phonetic_key → term ids, for O(1) same-sound lookup during correction.
    CREATE TABLE IF NOT EXISTS phonetic_index (
        phonetic_key TEXT NOT NULL,
        term_id TEXT NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
        PRIMARY KEY (phonetic_key, term_id)
    );
    CREATE INDEX IF NOT EXISTS idx_phon_key ON phonetic_index(phonetic_key);
"""


# -- connection owner ----------------------------------------------------------


class _LexiconDB:
    """Owns the raw sqlite3 connection and its access pragmas (no schema side effects)."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(
            path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.row_factory = sqlite3.Row

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn


# -- row mappers ---------------------------------------------------------------


def _row_to_term(r: sqlite3.Row) -> LexiconTerm:
    return LexiconTerm(
        id=r["id"],
        canonical=r["canonical"],
        aliases=json.loads(r["aliases_json"] or "[]"),
        phonetic_keys=json.loads(r["phonetic_keys_json"] or "[]"),
        entity_type=r["entity_type"] or "",
        weight=float(r["weight"] or 0.0),
        source=r["source"] or "graph",
        enabled=bool(r["enabled"]),
    )


def _correction_from_row(r: sqlite3.Row) -> Correction:
    return Correction(
        r["id"],
        r["heard"],
        r["meant"],
        r["phonetic_key"],
        int(r["count"]),
        bool(r["auto_apply"]),
        r["last_seen"],
    )


# -- term selection planner ----------------------------------------------------


class _TermQuery:
    """Composes the WHERE clause for term selections; owns filter semantics."""

    __slots__ = ("_clauses", "_args")

    def __init__(self) -> None:
        self._clauses: list[str] = ["1=1"]
        self._args: list = []

    def source(self, source: str) -> "_TermQuery":
        if source:
            self._clauses.append("source = ?")
            self._args.append(source)
        return self

    def canonical_like(self, fragment: str) -> "_TermQuery":
        if fragment:
            self._clauses.append("canonical LIKE ?")
            self._args.append(f"%{fragment}%")
        return self

    def where(self) -> str:
        return " AND ".join(self._clauses)

    def bindings(self) -> list:
        return list(self._args)


# -- term owner ----------------------------------------------------------------


class _TermRepo:
    """Term admission, phonetic-index replacement, selection and pruning."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    # admission -----------------------------------------------------------

    def admit(
        self,
        term_id: str,
        canonical: str,
        *,
        aliases: list[str] | None = None,
        phonetic_keys: list[str] | None = None,
        entity_type: str = "",
        weight: float = 1.0,
        source: str = "graph",
        enabled: bool = True,
    ) -> None:
        """Insert or refresh a term without losing provenance or user pruning."""
        now = _now()
        aliases_json = json.dumps(aliases or [])
        index_keys = phonetic_keys or []
        keys_json = json.dumps(index_keys)
        prior = self._c.execute(
            "SELECT source, created_at, enabled FROM terms WHERE id = ?", (term_id,)
        ).fetchone()
        eff_source, eff_enabled, created = self._resolve_admission(
            prior, source, enabled, now
        )
        self._c.execute(
            """INSERT INTO terms (id, canonical, aliases_json, phonetic_keys_json, entity_type,
                                  weight, source, enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 canonical=excluded.canonical, aliases_json=excluded.aliases_json,
                 phonetic_keys_json=excluded.phonetic_keys_json, entity_type=excluded.entity_type,
                 weight=excluded.weight, source=excluded.source, enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (
                term_id,
                canonical,
                aliases_json,
                keys_json,
                entity_type,
                weight,
                eff_source,
                1 if eff_enabled else 0,
                created,
                now,
            ),
        )
        self._replace_index(term_id, index_keys)

    @staticmethod
    def _resolve_admission(
        prior: sqlite3.Row | None, source: str, enabled: bool, now: str
    ) -> tuple[str, bool, str]:
        """Return (effective_source, effective_enabled, created_at)."""
        if prior is None:
            return source, enabled, now
        source_kept = (
            prior["source"]
            if prior["source"] in ("manual", "learned") and source == "graph"
            else source
        )
        enabled_kept = bool(prior["enabled"]) if source == "graph" else enabled
        return source_kept, enabled_kept, prior["created_at"]

    def _replace_index(self, term_id: str, keys: list[str]) -> None:
        self._c.execute("DELETE FROM phonetic_index WHERE term_id = ?", (term_id,))
        bindings = ((key, term_id) for key in set(keys))
        self._c.executemany(
            "INSERT OR IGNORE INTO phonetic_index (phonetic_key, term_id) VALUES (?,?)",
            bindings,
        )

    # weight / selection --------------------------------------------------

    def bump_weight(self, canonical: str, delta: float) -> None:
        self._c.execute(
            "UPDATE terms SET weight = weight + ?, updated_at = ? WHERE canonical = ?",
            (delta, _now(), canonical),
        )

    def select(
        self, *, source: str = "", search: str = "", limit: int = 500
    ) -> list[LexiconTerm]:
        plan = _TermQuery().source(source).canonical_like(search)
        sql = f"SELECT * FROM terms WHERE {plan.where()} ORDER BY weight DESC, canonical LIMIT ?"
        params = plan.bindings()
        params.append(limit)
        return [_row_to_term(r) for r in self._c.execute(sql, params)]

    def top(self, limit: int) -> list[LexiconTerm]:
        rows = self._c.execute(
            "SELECT * FROM terms WHERE enabled = 1 ORDER BY weight DESC, canonical LIMIT ?",
            (limit,),
        )
        return [_row_to_term(r) for r in rows]

    def by_key(self, key: str) -> list[LexiconTerm]:
        rows = self._c.execute(
            """SELECT t.* FROM terms t JOIN phonetic_index p ON p.term_id = t.id
               WHERE p.phonetic_key = ? AND t.enabled = 1""",
            (key,),
        )
        return [_row_to_term(r) for r in rows]

    def by_key_prefix(self, prefix: str) -> list[LexiconTerm]:
        """Terms whose key shares a prefix with *prefix* (either direction)."""
        if len(prefix) < 3:
            return []
        rows = self._c.execute(
            """SELECT DISTINCT t.* FROM terms t JOIN phonetic_index p ON p.term_id = t.id
               WHERE t.enabled = 1 AND (p.phonetic_key LIKE ? OR ? LIKE p.phonetic_key || '%')""",
            (prefix + "%", prefix),
        )
        return [_row_to_term(r) for r in rows]

    def by_canonical(self, canonical: str) -> LexiconTerm | None:
        row = self._c.execute(
            "SELECT * FROM terms WHERE canonical = ? COLLATE NOCASE", (canonical,)
        ).fetchone()
        return _row_to_term(row) if row else None

    # pruning / lifecycle -------------------------------------------------

    def prune_graph(self, keep: set[str]) -> int:
        candidates = self._c.execute("SELECT id FROM terms WHERE source = 'graph'")
        stale = [row["id"] for row in candidates if row["id"] not in keep]
        for term_id in stale:
            self.remove(term_id)
        return len(stale)

    def set_enabled(self, term_id: str, enabled: bool) -> bool:
        cur = self._c.execute(
            "UPDATE terms SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, _now(), term_id),
        )
        return cur.rowcount > 0

    def remove(self, term_id: str) -> bool:
        self._c.execute("DELETE FROM phonetic_index WHERE term_id = ?", (term_id,))
        cur = self._c.execute("DELETE FROM terms WHERE id = ?", (term_id,))
        return cur.rowcount > 0

    def total(self) -> int:
        return int(self._c.execute("SELECT COUNT(*) FROM terms").fetchone()[0])


# -- correction owner ----------------------------------------------------------


class _CorrectionRepo:
    """Correction admission, count/auto-apply control flow and read models."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def record(
        self,
        heard: str,
        meant: str,
        *,
        phonetic_key: str = "",
        auto_apply: bool | None = None,
        threshold: int = 2,
    ) -> Correction:
        now = _now()
        prior = self._c.execute(
            "SELECT * FROM corrections WHERE heard = ? AND meant = ?", (heard, meant)
        ).fetchone()
        if prior is None:
            return self._admit(heard, meant, phonetic_key, auto_apply, now)
        return self._count_up(prior, heard, meant, auto_apply, threshold, now)

    def _admit(
        self,
        heard: str,
        meant: str,
        phonetic_key: str,
        auto_apply: bool | None,
        now: str,
    ) -> Correction:
        cid = f"corr_{abs(hash((heard, meant))) & 0xFFFFFFFF:x}"
        flag = 1 if auto_apply else 0
        self._c.execute(
            """INSERT INTO corrections (id, heard, meant, phonetic_key, count, auto_apply,
                                        last_seen, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (cid, heard, meant, phonetic_key, 1, flag, now, now),
        )
        return Correction(cid, heard, meant, phonetic_key, 1, bool(flag), now)

    def _count_up(
        self,
        prior: sqlite3.Row,
        heard: str,
        meant: str,
        forced: bool | None,
        threshold: int,
        now: str,
    ) -> Correction:
        count = int(prior["count"]) + 1
        flag = self._should_auto_apply(
            bool(forced), bool(prior["auto_apply"]), count, threshold
        )
        self._c.execute(
            "UPDATE corrections SET count = ?, auto_apply = ?, last_seen = ? WHERE id = ?",
            (count, 1 if flag else 0, now, prior["id"]),
        )
        return Correction(
            prior["id"], heard, meant, prior["phonetic_key"], count, flag, now
        )

    @staticmethod
    def _should_auto_apply(
        forced: bool, already: bool, count: int, threshold: int
    ) -> bool:
        """auto_apply latches on: forced this time, forced before, or past threshold."""
        return forced or already or count >= threshold

    def auto_map(self) -> dict[str, str]:
        """heard (lowercased) → meant for every auto-applying correction."""
        return {
            r["heard"].lower(): r["meant"]
            for r in self._c.execute(
                "SELECT heard, meant FROM corrections WHERE auto_apply = 1"
            )
        }

    def listing(self, limit: int = 500) -> list[Correction]:
        rows = self._c.execute(
            "SELECT * FROM corrections ORDER BY count DESC, last_seen DESC LIMIT ?",
            (limit,),
        )
        return [_correction_from_row(r) for r in rows]

    def set_auto_apply(self, corr_id: str, auto_apply: bool) -> bool:
        cur = self._c.execute(
            "UPDATE corrections SET auto_apply = ? WHERE id = ?",
            (1 if auto_apply else 0, corr_id),
        )
        return cur.rowcount > 0


# -- timestamp helper ----------------------------------------------------------


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


# -- public store --------------------------------------------------------------


class LexiconStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or lexicon_db_path()
        self._backend = _LexiconDB(self.db_path)
        self.db = self._backend.conn
        self._init_schema()
        self._terms = _TermRepo(self.db)
        self._corrections = _CorrectionRepo(self.db)

    def _init_schema(self) -> None:
        """Idempotently ensure tables/indexes exist (subclass-overridable hook)."""
        self.db.executescript(_SCHEMA_SQL)

    def upsert_term(
        self,
        term_id: str,
        canonical: str,
        *,
        aliases: list[str] | None = None,
        phonetic_keys: list[str] | None = None,
        entity_type: str = "",
        weight: float = 1.0,
        source: str = "graph",
        enabled: bool = True,
    ) -> None:
        self._terms.admit(
            term_id,
            canonical,
            aliases=aliases,
            phonetic_keys=phonetic_keys,
            entity_type=entity_type,
            weight=weight,
            source=source,
            enabled=enabled,
        )

    def bump_weight(self, canonical: str, delta: float = 1.0) -> None:
        self._terms.bump_weight(canonical, delta)

    def list_terms(
        self, *, source: str = "", search: str = "", limit: int = 500
    ) -> list[LexiconTerm]:
        return self._terms.select(source=source, search=search, limit=limit)

    def top_terms(self, limit: int) -> list[LexiconTerm]:
        return self._terms.top(limit)

    def terms_for_phonetic_key(self, key: str) -> list[LexiconTerm]:
        return self._terms.by_key(key)

    def terms_for_phonetic_prefix(self, prefix: str) -> list[LexiconTerm]:
        return self._terms.by_key_prefix(prefix)

    def get_term_by_canonical(self, canonical: str) -> LexiconTerm | None:
        return self._terms.by_canonical(canonical)

    def prune_graph_terms(self, keep: set[str]) -> int:
        return self._terms.prune_graph(keep)

    def set_enabled(self, term_id: str, enabled: bool) -> bool:
        return self._terms.set_enabled(term_id, enabled)

    def delete_term(self, term_id: str) -> bool:
        return self._terms.remove(term_id)

    def count_terms(self) -> int:
        return self._terms.total()

    def upsert_correction(
        self,
        heard: str,
        meant: str,
        *,
        phonetic_key: str = "",
        auto_apply: bool | None = None,
        threshold: int = 2,
    ) -> Correction:
        return self._corrections.record(
            heard,
            meant,
            phonetic_key=phonetic_key,
            auto_apply=auto_apply,
            threshold=threshold,
        )

    def auto_corrections(self) -> dict[str, str]:
        return self._corrections.auto_map()

    def list_corrections(self, limit: int = 500) -> list[Correction]:
        return self._corrections.listing(limit)

    def set_correction_auto_apply(self, corr_id: str, auto_apply: bool) -> bool:
        return self._corrections.set_auto_apply(corr_id, auto_apply)

    def reset(self) -> None:
        """Drop all learned/graph state (the user-facing 'reset' — rebuild repopulates)."""
        self.db.executescript(
            "DELETE FROM phonetic_index; DELETE FROM terms; DELETE FROM corrections;"
        )
