"""Disposable transcript search with atomic FTS updates and live privacy filtering."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.core.sqlite_compat import FTS5_REMEDY, probe, sqlite3

logger = logging.getLogger(__name__)
_DB_FILE = "session_search.db"
_RESTRICTED_MODES = frozenset({"temporary", "incognito"})
MIN_QUERY_CHARS = 2
_MAX_SESSION_CHARS = 200_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed (
    session_key TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    mtime REAL NOT NULL DEFAULT 0,
    chars INTEGER NOT NULL DEFAULT 0,
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
            opened = sqlite3.connect(
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
                title, text, modified = self.replacement
                connection.execute(
                    "INSERT INTO sessions_fts (session_key, title, body) VALUES (?, ?, ?)",
                    (self.key, title, text),
                )
                connection.execute(
                    "INSERT INTO indexed (session_key, title, mtime, chars, indexed_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_key) DO UPDATE SET title=excluded.title, mtime=excluded.mtime, "
                    "chars=excluded.chars, indexed_at=excluded.indexed_at",
                    (self.key, title, float(modified or 0.0), len(text), time.time()),
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
) -> bool:
    key = (session_key or "").strip()
    if not key:
        return False
    if is_restricted(key, memory_mode=memory_mode):
        forget_session(key)
        return False
    connection = _connect()
    if connection is None:
        return False
    return _IndexMutation(
        connection, key, (title or "", (body or "")[:_MAX_SESSION_CHARS], mtime)
    ).commit()


def forget_session(session_key: str) -> None:
    key = (session_key or "").strip()
    if key:
        connection = _connect()
        if connection is not None:
            _IndexMutation(connection, key).commit()


def index_turn(
    session_key: str, role: str, text: str, *, memory_mode: str = ""
) -> None:
    key = (session_key or "").strip()
    if key:
        if is_restricted(key, memory_mode=memory_mode):
            forget_session(key)
        else:
            try:
                reindex_session(key)
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

    @classmethod
    def read(cls, log, key):
        try:
            metadata = log.get_metadata(key) or {}
        except Exception:
            metadata = {}
        mode = str(metadata.get("memory_mode", "") or "")
        if is_restricted(key, memory_mode=mode):
            forget_session(key)
            return None
        try:
            messages = log.read_messages(key) or []
        except Exception:
            logger.debug("session_search: cannot read %s", key, exc_info=True)
            return None
        return cls(
            key,
            str(metadata.get("title", "") or ""),
            mode,
            _transcript_body(messages),
            _source_mtime(log, key),
        )

    def write(self):
        return index_session(
            self.key, self.title, self.body, memory_mode=self.mode, mtime=self.modified
        )


def reindex_session(session_key: str, log=None) -> bool:
    source = log or _conversation_log()
    key = (session_key or "").strip()
    if not key:
        return False
    snapshot = _TranscriptSnapshot.read(source, key)
    return snapshot.write() if snapshot is not None else False


def _still_same_size(log, key: str, indexed_chars: int) -> bool:
    try:
        messages = log.read_messages(key) or []
    except Exception:
        return False
    return len(_transcript_body(messages)[:_MAX_SESSION_CHARS]) == int(
        indexed_chars or 0
    )


def _source_mtime(log, key: str) -> float:
    try:
        return float(log._path(key).stat().st_mtime)
    except Exception:
        return 0.0


def purge_orphans(log=None) -> int:
    source = log or _conversation_log()
    connection = _connect()
    if connection is None:
        return 0
    try:
        keys = [
            row[0]
            for row in connection.execute(
                "SELECT session_key FROM sessions_fts UNION SELECT session_key FROM indexed"
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
        forget_session(key)
        removed += 1
    if removed:
        logger.info("session_search: purged %d orphaned index row(s)", removed)
    return removed


@dataclass
class _RefreshPass:
    source: object
    known: dict
    force: bool
    live: set = field(default_factory=set)
    writes: int = 0

    def visit(self, entry):
        key = str(entry.get("key", "") or "")
        if not key:
            return False
        if is_restricted(key, memory_mode=str(entry.get("memory_mode", "") or "")):
            forget_session(key)
            return False
        self.live.add(key)
        modified = float(entry.get("modified", 0) or 0)
        prior = self.known.get(key)
        if not self.force and prior is not None:
            stamp, size = prior
            if float(stamp or 0) >= modified and _still_same_size(
                self.source, key, size
            ):
                return False
        if reindex_session(key, log=self.source):
            self.writes += 1
        return True

    def prune(self):
        for key in self.known.keys() - self.live:
            forget_session(key)
        purge_orphans(self.source)


def reindex_all(log=None, *, limit: int | None = None, force: bool = False) -> int:
    source = log or _conversation_log()
    connection = _connect()
    if connection is None:
        return 0
    try:
        entries = source.list_sessions() or []
    except Exception:
        logger.debug("session_search: cannot list sessions", exc_info=True)
        return 0
    try:
        known = {
            row["session_key"]: (row["mtime"], row["chars"])
            for row in connection.execute(
                "SELECT session_key, mtime, chars FROM indexed"
            ).fetchall()
        }
    except Exception:
        known = {}
    refresh = _RefreshPass(source, known, force)
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
    query: str, *, limit: int = 30, folder: str | None = None
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
        rows = connection.execute(
            "SELECT session_key, title, snippet(sessions_fts, 2, '<<', '>>', '…', 24) AS snippet, rank "
            "FROM sessions_fts WHERE sessions_fts MATCH ? ORDER BY rank LIMIT ?",
            (expression, max(1, min(int(limit or 30), 200))),
        ).fetchall()
    except Exception:
        logger.debug("session_search: query error", exc_info=True)
        return []
    return [
        {
            "session_key": row["session_key"],
            "key": row["session_key"],
            "title": row["title"] or row["session_key"],
            "snippet": row["snippet"] or "",
            "rank": float(row["rank"] or 0.0),
        }
        for row in rows
        if not is_restricted(row["session_key"])
    ]


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
