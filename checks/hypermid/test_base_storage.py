from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.hypermid.storage import (
    LeaseContended,
    LeaseKey,
    Migration,
    SQLiteStore,
    StaleFence,
    StorageError,
    postgres_database_name,
)


def _key() -> LeaseKey:
    return LeaseKey("memory", "sqlite", "owner:project")


def _migrations() -> tuple[Migration, ...]:
    return (
        Migration(1, "records", "CREATE TABLE records(value TEXT NOT NULL);"),
        Migration(2, "seed", "INSERT INTO records(value) VALUES ('seed');"),
    )


def test_sqlite_lease_contention_crash_reclamation_and_stale_fence(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    first = SQLiteStore.open(path, _key(), 2, _migrations())
    stale = first.fence
    with pytest.raises(LeaseContended):
        SQLiteStore.open(path, _key(), 2, _migrations())
    first.close()

    replacement = SQLiteStore.open(path, _key(), 2, _migrations())
    try:
        assert replacement.fence.epoch == stale.epoch + 1
        with pytest.raises(StaleFence):
            replacement.fenced_transaction(stale, lambda connection: None)
        replacement.fenced_transaction(
            replacement.fence,
            lambda connection: connection.execute(
                "INSERT INTO records(value) VALUES ('current')"
            ),
        )
    finally:
        replacement.close()

    script = """
import os, sys
from gideon.hypermid.storage import LeaseKey, Migration, SQLiteStore
store = SQLiteStore.open(sys.argv[1], LeaseKey('memory', 'sqlite', 'owner:project'), 2, (
    Migration(1, 'records', 'CREATE TABLE records(value TEXT NOT NULL);'),
    Migration(2, 'seed', "INSERT INTO records(value) VALUES ('seed');"),
))
print(store.fence.epoch, flush=True)
os._exit(0)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "runtime"},
    )
    crashed_epoch = int(child.stdout.strip())
    reclaimed = SQLiteStore.open(path, _key(), 2, _migrations())
    try:
        assert reclaimed.fence.epoch == crashed_epoch + 1
    finally:
        reclaimed.close()


def test_migrations_are_ordered_atomic_and_store_ahead_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    with pytest.raises(StorageError):
        SQLiteStore.open(
            path,
            _key(),
            2,
            (Migration(2, "late", "SELECT 1;"), Migration(1, "early", "SELECT 1;")),
        )
    assert not path.exists()

    broken = (
        Migration(
            1,
            "broken",
            "CREATE TABLE partial(value TEXT); INSERT INTO absent(value) VALUES ('x');",
        ),
    )
    with pytest.raises(sqlite3.OperationalError):
        SQLiteStore.open(path, _key(), 1, broken)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='partial'"
        ).fetchone() == (0,)

    path.unlink()
    Path(f"{path}.lease").unlink()
    store = SQLiteStore.open(path, _key(), 2, _migrations())
    store.close()
    ahead = SQLiteStore.open(path, _key(), 1, _migrations()[:1])
    try:
        assert ahead.status.state == "store_ahead"
        with pytest.raises(Exception) as error:
            ahead.fenced_transaction(ahead.fence, lambda connection: None)
        assert "newer than supported" in str(error.value)
    finally:
        ahead.close()


def test_local_store_files_are_private_and_postgres_names_do_not_slug_collide(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.sqlite3"
    store = SQLiteStore.open(path, _key(), 2, _migrations())
    store.close()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(Path(f"{path}.lease").stat().st_mode) == 0o600
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert postgres_database_name("module-a") != postgres_database_name("module_a")
    assert len(postgres_database_name("9." + "x" * 200)) <= 63
