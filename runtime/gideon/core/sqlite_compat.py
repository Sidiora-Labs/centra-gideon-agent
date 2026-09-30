"""Select one SQLite driver and measure its available query capabilities."""

from __future__ import annotations

import importlib
import threading
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


def _select_driver():
    try:
        return importlib.import_module("pysqlite3"), "pysqlite3"
    except ImportError:
        return importlib.import_module("sqlite3"), "sqlite3"


sqlite3, _DRIVER = _select_driver()
__all__ = [
    "sqlite3",
    "SharedConnection",
    "connect_shared",
    "SqliteCapabilities",
    "probe",
    "driver_name",
    "FTS5_REMEDY",
]
FTS5_REMEDY = (
    "This SQLite build has no FTS5 (full-text search) module compiled in. "
    "Install the 'pysqlite3-binary' wheel (pip install pysqlite3-binary), which "
    "bundles a SQLite built with FTS5, or run Gideon against a SQLite build "
    "that has FTS5 enabled."
)


@dataclass(frozen=True)
class SqliteCapabilities:
    driver: str
    version: str
    fts5: bool
    json1: bool


class _CursorOperationLock:
    """Mixin that serializes native cursor operations through the owning connection."""
    def execute(self, *args, **kwargs):
        with self.connection._operation_lock:
            return super().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self.connection._operation_lock:
            return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self.connection._operation_lock:
            return super().executescript(*args, **kwargs)

    def fetchone(self):
        with self.connection._operation_lock:
            return super().fetchone()

    def fetchmany(self, size=None):
        with self.connection._operation_lock:
            if size is None:
                return super().fetchmany()
            return super().fetchmany(size)

    def fetchall(self):
        with self.connection._operation_lock:
            return super().fetchall()

    def __iter__(self):
        return self

    def __next__(self):
        with self.connection._operation_lock:
            return super().__next__()

    def close(self):
        with self.connection._operation_lock:
            return super().close()


class _SharedCursor(_CursorOperationLock, sqlite3.Cursor):
    """A native cursor whose SQLite operations share its connection's lock."""


@lru_cache(maxsize=None)
def _shared_cursor_factory(factory):
    if not isinstance(factory, type) or not issubclass(factory, sqlite3.Cursor):
        return factory
    if issubclass(factory, _CursorOperationLock):
        return factory
    return type(
        f"Shared{factory.__name__}",
        (_CursorOperationLock, factory),
        {"__module__": factory.__module__},
    )


class SharedConnection(sqlite3.Connection):
    """A real driver connection that serializes each connection/cursor operation.

    It deliberately remains a subclass of the selected driver's native connection, so
    DB-API type checks, row factories, exceptions and cursor results retain their native
    behavior. The reentrant lock also lets Connection.execute delegate through the
    guarded cursor methods without deadlocking.
    """

    def __init__(self, *args, **kwargs):
        self._operation_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def cursor(self, factory=None):
        with self._operation_lock:
            return super().cursor(
                _SharedCursor if factory is None else _shared_cursor_factory(factory)
            )

    def execute(self, *args, **kwargs):
        with self._operation_lock:
            return self.cursor().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._operation_lock:
            return self.cursor().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._operation_lock:
            return self.cursor().executescript(*args, **kwargs)

    def commit(self):
        with self._operation_lock:
            return super().commit()

    def rollback(self):
        with self._operation_lock:
            return super().rollback()

    def close(self):
        with self._operation_lock:
            return super().close()

    def __enter__(self):
        with self._operation_lock:
            super().__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        with self._operation_lock:
            return super().__exit__(exc_type, exc_value, traceback)


def connect_shared(database, *args, **kwargs) -> SharedConnection:
    """Open the selected SQLite driver with serialized shared-connection access."""
    if "factory" in kwargs:
        raise TypeError("connect_shared manages the SQLite connection factory")
    return sqlite3.connect(database, *args, factory=SharedConnection, **kwargs)


def driver_name() -> str:
    return str(_DRIVER)


def _has_module(conn: Any, create_sql: str) -> bool:
    try:
        conn.execute(create_sql)
    except sqlite3.Error:
        return False
    return True


@lru_cache(maxsize=1)
def probe() -> SqliteCapabilities:
    supported = dict(fts5=False, json1=False)
    queries = {
        "fts5": "CREATE VIRTUAL TABLE _probe_fts USING fts5(x)",
        "json1": """SELECT json_extract('{"a":1}', '$.a')""",
    }
    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            for name, query in queries.items():
                supported[name] = _has_module(connection, query)
    except sqlite3.Error:
        pass
    return SqliteCapabilities(
        driver=_DRIVER,
        version=getattr(sqlite3, "sqlite_version", "unknown"),
        **supported,
    )
