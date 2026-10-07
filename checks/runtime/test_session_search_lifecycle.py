"""SQLite search workers must finish before their shared connection is closed."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from gideon.cognition import session_search as search
from gideon.cognition.history import ConversationLog
from gideon.engine import session_search as fts


@pytest.fixture(autouse=True)
def isolated_search(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    search.shutdown_indexers()
    fts.reset_for_tests()
    yield
    search.shutdown_indexers()
    fts.reset_for_tests()


def blocked_worker(tmp_path):
    log = ConversationLog(base_dir=tmp_path / "history")
    log.append("chat", "user", "Searchable lifecycle evidence")
    connection = fts._connect()
    assert connection is not None
    entered = threading.Event()
    release = threading.Event()

    def trace(statement):
        if (
            threading.current_thread().name == "gideon-session-index"
            and statement.startswith("SELECT session_key")
        ):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test did not release the real SQLite query")

    connection.set_trace_callback(trace)
    indexer = search.get_indexer(log)
    assert indexer.start()
    assert entered.wait(5), "worker did not execute its real SQLite coverage query"
    return connection, indexer, release


def test_shutdown_joins_query_worker_before_closing_and_can_restart(
    tmp_path, monkeypatch
):
    connection, indexer, release = blocked_worker(tmp_path)
    started = threading.Event()
    finished = threading.Event()
    failures = []

    def teardown():
        started.set()
        try:
            search.shutdown_indexers()
            fts.reset_for_tests()
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=teardown)
    thread.start()
    try:
        assert started.wait(5)
        assert not finished.wait(0.05)
        assert fts._database.connection is connection
        assert indexer._thread.is_alive()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not failures
    assert finished.is_set()
    assert not indexer._thread.is_alive()
    assert not search._INDEXERS
    assert fts._database.connection is None
    with pytest.raises(fts.sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "next-home"))
    log = ConversationLog(base_dir=tmp_path / "next-history")
    log.append("next-chat", "user", "Freshly reopened searchable evidence")
    restarted = search.get_indexer(log)
    assert restarted is not indexer
    assert restarted.start()
    restarted.stop(wait=True)
    assert fts.reindex_session("next-chat", log=log)
    assert [hit["key"] for hit in fts.search_sessions("reopened", log=log)] == [
        "next-chat"
    ]
    assert fts._database.connection is not connection
    assert fts.db_path().parent == tmp_path / "next-home"


def test_shutdown_timeout_keeps_registry_and_database_owned(tmp_path):
    connection, indexer, release = blocked_worker(tmp_path)
    try:
        with pytest.raises(TimeoutError, match="did not stop"):
            search.shutdown_indexers()
        assert indexer._thread.is_alive()
        assert search._INDEXERS[indexer.root] is indexer
        assert fts._database.connection is connection
    finally:
        release.set()
        indexer.stop(wait=True)
        search.shutdown_indexers()
        fts.reset_for_tests()
    assert connection is not fts._database.connection


def test_concurrent_cold_acquires_share_one_initialized_sqlite_connection():
    barrier = threading.Barrier(4)

    def acquire():
        barrier.wait(timeout=5)
        connection = fts._connect()
        assert connection is not None
        with fts._DB_LOCK:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master"
                ).fetchall()
            }
        assert {"indexed", "sessions_fts"} <= names
        return connection

    with ThreadPoolExecutor(max_workers=4) as workers:
        connections = list(workers.map(lambda _: acquire(), range(4)))
    assert len({id(connection) for connection in connections}) == 1
    assert fts._database.connection is connections[0]
