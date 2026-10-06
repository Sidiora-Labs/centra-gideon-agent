"""Real filesystem and SQLite checks for private database creation."""
import os
import stat

from gideon.core.database_privacy import prepare_database
from gideon.core.sqlite_compat import connect, connect_shared


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_private_before_first_byte_and_real_wal(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('GIDEON_HOME', str(home))
    path = home / 'nested' / 'db.sqlite3'
    prepare_database(path)
    assert path.stat().st_size == 0
    assert mode(path) == 0o600
    assert mode(home) == mode(path.parent) == 0o700
    connection = connect_shared(path)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE records(value TEXT)')
        connection.execute("INSERT INTO records VALUES ('durable')")
        connection.commit()
        assert connection.execute('SELECT value FROM records').fetchone()[0] == 'durable'
        assert mode(path) == 0o600
        for suffix in ('-wal', '-shm'):
            assert mode(path.with_name(path.name + suffix)) == 0o600
    finally:
        connection.close()


def test_existing_files_tightened_before_open(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    path = tmp_path / 'existing.db'
    path.touch(mode=0o644)
    sidecar = tmp_path / 'existing.db-journal'
    sidecar.touch(mode=0o644)
    prepare_database(path)
    assert mode(path) == mode(sidecar) == 0o600


def test_external_permissions_and_memory_locations_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    path = tmp_path / 'shared.db'
    path.touch()
    os.chmod(path, 0o644)
    connection = connect(path)
    connection.close()
    assert mode(path) == 0o644
    for location in (':memory:', 'file::memory:?cache=shared'):
        connection = connect(location, uri=True)
        connection.execute('CREATE TABLE records(value TEXT)')
        connection.close()
