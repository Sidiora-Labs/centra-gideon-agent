"""Disposable transcript search with atomic FTS updates and live privacy filtering."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.core.sqlite_compat import FTS5_REMEDY, connect, probe, sqlite3

logger = logging.getLogger(__name__)
_DB_FILE = "session_search.db"
_RESTRICTED_MODES = frozenset({"temporary", "incognito"})
MIN_QUERY_CHARS = 2
_MAX_SESSION_CHARS = 200_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed (
    session_key TEXT PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    mtime REAL NOT NULL DEFAULT 0,
    chars INTEGER NOT NULL DEFAULT 0,
    source_identity TEXT NOT NULL DEFAULT '',
    long INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    session_key UNINDEXED,
    title,
    body,
    tokenize='porter unicode61'
);
"""
_FTS_TOKEN = re.compile(r"[0-9A-Za-z_]+")
_DB_LOCK = threading.RLock()


def _scope_for(log) -> str:
    try:
        root = Path(log._dir).resolve()
    except Exception:
        root = Path(os.environ.get("GIDEON_HOME", ".")).resolve() / "sessions"
    return str(root)


def _stored_key(session_key: str, scope: str = "") -> str:
    return f"{scope}\x1f{session_key}" if scope else session_key


class _DatabaseLease:
    def __init__(self):
        self.connection = None
        self.location = ""
        self.warned = False

    def release(self):
        previous, self.connection = self.connection, None
        if previous is not None:
            try:
                previous.close()
            except sqlite3.Error:
                pass

    def acquire(self):
        if not probe().fts5:
            if not self.warned:
                logger.warning("Session full-text search disabled. %s", FTS5_REMEDY)
                self.warned = True
            return None
        location = str(db_path())
        if self.connection is not None and location == self.location:
            return self.connection
        self.release()
        opened = None
        try:
            Path(location).parent.mkdir(parents=True, exist_ok=True)
            opened = connect(
                location, timeout=15, isolation_level=None, check_same_thread=False
            )
            opened.row_factory = sqlite3.Row
            try:
                for statement in (
                    "PRAGMA journal_mode=WAL",
                    "PRAGMA busy_timeout=10000",
                ):
                    opened.execute(statement)
            except sqlite3.DatabaseError:
                logger.debug("session_search: pragma setup skipped", exc_info=True)
            opened.executescript(_SCHEMA)
            columns = {
                row[1] for row in opened.execute("PRAGMA table_info(indexed)")
            }
            legacy_scope = "scope" not in columns
            for name, declaration in (
                ("scope", "TEXT NOT NULL DEFAULT ''"),
                ("source_identity", "TEXT NOT NULL DEFAULT ''"),
                ("long", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    opened.execute(f"ALTER TABLE indexed ADD COLUMN {name} {declaration}")
            if legacy_scope:
                opened.execute("DELETE FROM sessions_fts")
                opened.execute("DELETE FROM indexed")
        except Exception:
            if opened is not None:
                try:
                    opened.close()
                except sqlite3.Error:
                    pass
            logger.debug("session_search: cannot open index", exc_info=True)
            return None
        self.connection, self.location = opened, location
        return opened


_database = _DatabaseLease()


def db_path() -> Path:
    from gideon.core.config.loader import config_dir

    return Path(os.environ.get("GIDEON_HOME", config_dir())) / _DB_FILE


def _connect() -> "sqlite3.Connection | None":
    return _database.acquire()


def reset_for_tests() -> None:
    _database.release()
    _database.location = ""
    _database.warned = False


def is_restricted(session_key: str, *, memory_mode: str = "") -> bool:
    if memory_mode and memory_mode.strip().lower() in _RESTRICTED_MODES:
        return True
    try:
        from gideon.engine import session_restrictions

        return bool(session_restrictions.is_restricted(session_key))
    except Exception:
        return False


@dataclass
class _IndexMutation:
    connection: Any
    key: str
    replacement: tuple | None = None

    def commit(self):
        with _DB_LOCK:
            return self._commit_unlocked()

    def _commit_unlocked(self):
        connection = self.connection
        try:
            connection.execute("BEGIN")
            connection.execute(
                "DELETE FROM sessions_fts WHERE session_key = ?", (self.key,)
            )
            if self.replacement is None:
                connection.execute(
                    "DELETE FROM indexed WHERE session_key = ?", (self.key,)
                )
            else:
                scope, title, text, modified, identity, long = self.replacement
                connection.execute(
                    "INSERT INTO sessions_fts (session_key, title, body) VALUES (?, ?, ?)",
                    (self.key, title, text),
                )
                connection.execute(
                    "INSERT INTO indexed (session_key, scope, title, mtime, chars, source_identity, long, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_key) DO UPDATE SET scope=excluded.scope, title=excluded.title, "
                    "mtime=excluded.mtime, chars=excluded.chars, source_identity=excluded.source_identity, "
                    "long=excluded.long, indexed_at=excluded.indexed_at",
                    (
                        self.key,
                        scope,
                        title,
                        float(modified or 0.0),
                        len(text),
                        identity,
                        int(bool(long)),
                        time.time(),
                    ),
                )
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            logger.debug(
                "session_search: index mutation failed for %s", self.key, exc_info=True
            )
            return False
        return True


def index_session(
    session_key: str,
    title: str,
    body: str,
    *,
    memory_mode: str = "",
    mtime: float = 0.0,
    scope: str = "",
    source_identity: str = "",
    long: bool = False,
) -> bool:
    raw_key = (session_key or "").strip()
    key = _stored_key(raw_key, scope)
    if not key:
        return False
    if is_restricted(raw_key, memory_mode=memory_mode):
        forget_session(raw_key, scope=scope)
        return False
    connection = _connect()
    if connection is None:
        return False
    return _IndexMutation(
        connection,
        key,
        (
            scope,
            title or "",
            (body or "")[:_MAX_SESSION_CHARS],
            mtime,
            source_identity,
            long,
        ),
    ).commit()


def forget_session(session_key: str, *, scope: str = "") -> None:
    raw_key = (session_key or "").strip()
    key = _stored_key(raw_key, scope)
    if key:
        connection = _connect()
        if connection is not None:
            _IndexMutation(connection, key).commit()


def index_turn(
    session_key: str, role: str, text: str, *, memory_mode: str = "", log=None
) -> None:
    key = (session_key or "").strip()
    if key:
        source = log or _conversation_log()
        scope = _scope_for(source)
        if is_restricted(key, memory_mode=memory_mode):
            forget_session(key, scope=scope)
        else:
            try:
                reindex_session(key, log=source)
            except Exception:
                logger.debug(
                    "session_search: index_turn failed for %s", key, exc_info=True
                )


def _conversation_log():
    from gideon.cognition.history import ConversationLog

    return ConversationLog()


def _transcript_body(messages):
    return "\n".join(
        str(message.get("content", "") or "")
        for message in messages
        if message.get("role") != "system"
    )


@dataclass(frozen=True)
class _TranscriptSnapshot:
    key: str
    title: str
    mode: str
    body: str
    modified: float
    identity: str
    long: bool
    scope: str

    @classmethod
    def read(cls, log, key):
        try:
            metadata = log.get_metadata(key) or {}
        except Exception:
            metadata = {}
        mode = str(metadata.get("memory_mode", "") or "")
        if is_restricted(key, memory_mode=mode):
            forget_session(key, scope=_scope_for(log))
            return None
        try:
            path = log._path(key)
            stat = path.stat()
            identity = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"
            chunks = []
            used = 0
            long = False
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        message = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    role = str(message.get("role", "") or "")
                    if role == "system" or message.get("_type") == "metadata":
                        continue
                    content = str(message.get("content", "") or "")
                    if used >= _MAX_SESSION_CHARS:
                        long = True
                        break
                    part = content[: _MAX_SESSION_CHARS - used]
                    chunks.append(part)
                    used += len(part)
                    if len(part) != len(content):
                        long = True
                        break
            body = "\n".join(chunks)
        except Exception:
            logger.debug("session_search: cannot read %s", key, exc_info=True)
            return None
        return cls(
            key,
            str(metadata.get("title", "") or ""),
            mode,
            body,
            float(stat.st_mtime),
            identity,
            long,
            _scope_for(log),
        )

    def write(self):
        return index_session(
            self.key,
            self.title,
            self.body,
            memory_mode=self.mode,
            mtime=self.modified,
            scope=self.scope,
            source_identity=self.identity,
            long=self.long,
        )


def reindex_session(session_key: str, log=None) -> bool:
    source = log or _conversation_log()
    key = (session_key or "").strip()
    if not key:
        return False
    snapshot = _TranscriptSnapshot.read(source, key)
    return snapshot.write() if snapshot is not None else False


def _source_identity(log, key: str) -> str:
    try:
        stat = log._path(key).stat()
        return f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"
    except Exception:
        return ""


def _source_mtime(log, key: str) -> float:
    try:
        return float(log._path(key).stat().st_mtime)
    except Exception:
        return 0.0


def purge_orphans(log=None) -> int:
    source = log or _conversation_log()
    scope = _scope_for(source)
    connection = _connect()
    if connection is None:
        return 0
    try:
        keys = [
            row[0].split("\x1f", 1)[-1]
            for row in connection.execute(
                "SELECT session_key FROM indexed WHERE scope = ?", (scope,)
            ).fetchall()
        ]
    except Exception:
        logger.debug("session_search: purge_orphans enumeration failed", exc_info=True)
        return 0
    removed = 0
    for key in keys:
        try:
            exists = bool(source.has_log(key))
        except Exception:
            continue
        if exists:
            continue
        forget_session(key, scope=scope)
        removed += 1
    if removed:
        logger.info("session_search: purged %d orphaned index row(s)", removed)
    return removed


@dataclass
class _RefreshPass:
    source: object
    known: dict
    force: bool
    scope: str
    live: set = field(default_factory=set)
    writes: int = 0

    def visit(self, entry):
        key = str(entry.get("key", "") or "")
        if not key:
            return False
        if is_restricted(key, memory_mode=str(entry.get("memory_mode", "") or "")):
            forget_session(key, scope=self.scope)
            return False
        self.live.add(key)
        prior = self.known.get(key)
        identity = _source_identity(self.source, key)
        if not self.force and prior is not None and prior == identity:
            return False
        if reindex_session(key, log=self.source):
            self.writes += 1
        return True

    def prune(self):
        for key in self.known.keys() - self.live:
            forget_session(key, scope=self.scope)
        purge_orphans(self.source)


def reindex_all(log=None, *, limit: int | None = None, force: bool = False) -> int:
    source = log or _conversation_log()
    scope = _scope_for(source)
    connection = _connect()
    if connection is None:
        return 0
    try:
        entries = source.list_session_records() or []
    except Exception:
        logger.debug("session_search: cannot list sessions", exc_info=True)
        return 0
    try:
        prefix = scope + "\x1f"
        with _DB_LOCK:
            known = {
                row["session_key"][len(prefix) :]: row["source_identity"]
                for row in connection.execute(
                    "SELECT session_key, source_identity FROM indexed WHERE scope = ?",
                    (scope,),
                ).fetchall()
                if row["session_key"].startswith(prefix)
            }
    except Exception:
        known = {}
    refresh = _RefreshPass(source, known, force, scope)
    for entry in entries:
        attempted = refresh.visit(entry)
        if attempted and limit is not None and refresh.writes >= limit:
            break
    if limit is None:
        refresh.prune()
    return refresh.writes


def _fts_query(raw: str) -> str:
    words = _FTS_TOKEN.findall(raw or "")
    return " ".join(
        '"' + word + '"' + ("*" if position == len(words) - 1 else "")
        for position, word in enumerate(words)
    )


def search_sessions(
    query: str,
    *,
    limit: int = 30,
    folder: str | None = None,
    log=None,
    scope: str | None = None,
    strict: bool = False,
) -> list[dict]:
    text = (query or "").strip()
    if len(text) < MIN_QUERY_CHARS:
        return []
    connection = _connect()
    if connection is None:
        return []
    expression = _fts_query(text)
    if not expression:
        return []
    try:
        scope = scope if scope is not None else (_scope_for(log) if log else None)
        scope_filter = "JOIN indexed i ON i.session_key = sessions_fts.session_key " if scope is not None else ""
        where_scope = " AND i.scope = ?" if scope is not None else ""
        params = (expression, scope, max(1, min(int(limit or 30), 200))) if scope is not None else (expression, max(1, min(int(limit or 30), 200)))
        with _DB_LOCK:
            rows = connection.execute(
                "SELECT sessions_fts.session_key, sessions_fts.title, snippet(sessions_fts, 2, '<<', '>>', '…', 24) AS snippet, rank "
                "FROM sessions_fts " + scope_filter +
                "WHERE sessions_fts MATCH ?" + where_scope + " ORDER BY rank LIMIT ?",
                params,
            ).fetchall()
    except Exception:
        logger.debug("session_search: query error", exc_info=True)
        if strict:
            raise
        return []
    results = []
    for row in rows:
        raw_key = row["session_key"].split("\x1f", 1)[-1]
        if is_restricted(raw_key):
            continue
        results.append(
            {
                "session_key": raw_key,
                "key": raw_key,
                "title": row["title"] or raw_key,
                "snippet": row["snippet"] or "",
                "rank": float(row["rank"] or 0.0),
            }
        )
    return results


def stats() -> dict:
    connection = _connect()
    if connection is not None:
        try:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(chars), 0) FROM indexed"
            ).fetchone()
            return {
                "available": True,
                "sessions": int(row[0]),
                "indexed_chars": int(row[1]),
                "db_path": str(db_path()),
            }
        except Exception:
            pass
    return {"available": False, "sessions": 0}


def indexed_state(log=None, *, scope: str | None = None) -> dict:
    """Return keys represented by this log in the disposable FTS index."""
    resolved_scope = scope if scope is not None else (_scope_for(log) if log else "")
    connection = _connect()
    if connection is None:
        return {"available": False, "keys": set(), "identities": {}, "long": 0}
    try:
        with _DB_LOCK:
            rows = connection.execute(
                "SELECT session_key, long, source_identity FROM indexed WHERE scope = ?",
                (resolved_scope,),
            ).fetchall()
        prefix = resolved_scope + "\x1f" if resolved_scope else ""
        return {
            "available": True,
            "keys": {row["session_key"][len(prefix) :] for row in rows if not prefix or row["session_key"].startswith(prefix)},
            "identities": {
                row["session_key"][len(prefix) :]: row["source_identity"]
                for row in rows
                if not prefix or row["session_key"].startswith(prefix)
            },
            "long_keys": {
                row["session_key"][len(prefix) :]
                for row in rows
                if row["long"] and (not prefix or row["session_key"].startswith(prefix))
            },
            "long": sum(int(row["long"] or 0) for row in rows),
        }
    except Exception:
        logger.debug("session_search: coverage read failed", exc_info=True)
        return {"available": False, "keys": set(), "identities": {}, "long": 0}
