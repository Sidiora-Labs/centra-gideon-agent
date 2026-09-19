"""Select one SQLite driver and measure its available query capabilities."""

from __future__ import annotations

import importlib
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
__all__ = ["sqlite3", "SqliteCapabilities", "probe", "driver_name", "FTS5_REMEDY"]
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
