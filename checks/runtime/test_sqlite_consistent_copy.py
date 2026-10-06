"""Archive and restore correctness using databases with live WAL state."""

import sqlite3
from pathlib import Path

from gideon.operations.durability import sqlite_files


def test_live_wal_copy_preserves_committed_rows_and_skips_sidecars(tmp_path):
    source = tmp_path / "source" / "opaque.data"
    source.parent.mkdir()
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE records(value TEXT)")
        connection.commit()
        connection.execute("INSERT INTO records VALUES ('wal-only')")
        connection.commit()
        destination = tmp_path / "backup" / "opaque.data"
        sqlite_files.copy_file(source, destination)
        copied = sqlite3.connect(destination)
        try:
            assert copied.execute("SELECT value FROM records").fetchall() == [
                ("wal-only",)
            ]
        finally:
            copied.close()
        assert sqlite_files.sidecars_in(
            source.parent, [source.name, source.name + "-wal"]
        ) == {source.name + "-wal"}
    finally:
        connection.close()


def test_absent_database_import_removes_orphan_journal(tmp_path):
    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE records(value TEXT)")
    connection.commit()
    connection.close()
    destination = tmp_path / "dest.db"
    orphan = tmp_path / "dest.db-wal"
    orphan.write_bytes(b"old-wal")
    assert sqlite_files.bring_in(source, destination)
    assert not orphan.exists()
    assert not sqlite_files.bring_in(source, destination)
    assert sqlite_files.is_database(destination)


def test_database_detection_preserves_non_sqlite_db_files(tmp_path):
    text = tmp_path / "notes.db"
    text.write_text("ordinary notes")
    assert not sqlite_files.is_database(text)
    target = tmp_path / "copy.db"
    sqlite_files.copy_file(text, target)
    assert target.read_text() == "ordinary notes"
    assert list(sqlite_files.databases_in(tmp_path)) == []


def test_snapshot_tree_copy_consistent_for_nested_database(tmp_path):
    from gideon.workspace.snapshot import _copytree_safe

    source = tmp_path / "source"
    source.mkdir()
    database = source / "records.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records(value TEXT)")
        connection.execute("INSERT INTO records VALUES ('live')")
        connection.commit()
        destination = tmp_path / "destination"
        _copytree_safe(source, destination)
        assert not (destination / "records.db-wal").exists()
        copied = sqlite3.connect(destination / database.name)
        try:
            assert copied.execute("SELECT value FROM records").fetchone()[0] == "live"
        finally:
            copied.close()
    finally:
        connection.close()


def test_rollback_moves_database_with_pending_sidecars(tmp_path):
    source = tmp_path / "live.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE records(value TEXT)")
    connection.commit()
    connection.close()
    sidecar = tmp_path / "live.db-journal"
    sidecar.write_bytes(b"rollback-state")
    destination = tmp_path / "backup" / "live.db"
    sqlite_files.move_aside(source, destination)
    assert not source.exists() and not sidecar.exists()
    assert destination.exists()
    assert Path(str(destination) + "-journal").read_bytes() == b"rollback-state"


def test_app_partition_census_rejects_text_db(tmp_path):
    from gideon.operations.durability.inventory import partition_paths

    data = tmp_path / "apps" / "sample" / "data"
    data.mkdir(parents=True)
    connection = sqlite3.connect(data / "state.db")
    connection.execute("CREATE TABLE records(value TEXT)")
    connection.close()
    (data / "notes.db").write_text("notes")
    assert partition_paths(tmp_path) == ["apps/sample/data/state.db"]
