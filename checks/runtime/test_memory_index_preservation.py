from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.memory import MemoryJournal, degraded_keyword_indexes
from gideon.core.sqlite_compat import sqlite3
from gideon.interfaces.dashboard.handlers.memory import api_memory_observability
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations.resilience.remediation import _job_rebuild_memory_fts


def _shared_database(journal):
    connection = sqlite3.connect(str(journal._index_db))
    connection.execute("CREATE TABLE facts (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO facts VALUES ('owner.preference', 'keep this fact')")
    connection.commit()
    return connection


def test_locked_projection_never_deletes_shared_memories_or_sidecars(tmp_path):
    journal = MemoryJournal(workspace=tmp_path)
    journal.init()
    held = _shared_database(journal)
    held.execute("PRAGMA journal_mode=WAL")
    held.execute("BEGIN IMMEDIATE")
    held.execute("UPDATE facts SET value='preserved concurrent write'")
    sidecars = [tmp_path / 'memory_index.db-wal', tmp_path / 'memory_index.db-shm']
    before = {path: path.read_bytes() for path in [journal._index_db, *sidecars]}
    try:
        assert journal.rebuild_index() == 0
        assert 'locked' in journal.search_degraded()
        assert all(path.read_bytes() == content for path, content in before.items())
    finally:
        held.commit()
        held.close()
    connection = sqlite3.connect(str(journal._index_db))
    try:
        assert connection.execute('SELECT value FROM facts').fetchone()[0] == 'preserved concurrent write'
    finally:
        connection.close()
    assert journal.rebuild_index() == 2
    assert journal.search_degraded() == ''


def test_unreadable_database_is_left_whole_with_original_sidecars(tmp_path):
    journal = MemoryJournal(workspace=tmp_path)
    journal.init()
    journal._index_db.write_bytes(b'original damaged database bytes')
    files = [journal._index_db]
    for suffix in ['-wal', '-shm']:
        sidecar = tmp_path / ('memory_index.db' + suffix)
        sidecar.write_bytes(('preserved' + suffix).encode())
        files.append(sidecar)
    before = {path: path.read_bytes() for path in files}
    assert journal.rebuild_index() == 0
    assert journal.search('preferences') == []
    assert journal.keyword_index_status()['state'] == 'degraded'
    assert journal.fts_desync_count() > 0
    assert all(path.read_bytes() == content for path, content in before.items())
    assert str(journal._index_db.resolve()) in degraded_keyword_indexes()


def test_index_schema_repaired_in_place_without_touching_facts(tmp_path):
    journal = MemoryJournal(workspace=tmp_path)
    journal.init()
    connection = _shared_database(journal)
    connection.execute('CREATE TABLE memory_fts (obsolete TEXT)')
    connection.commit()
    connection.close()
    identity = journal._index_db.stat().st_ino
    assert journal.rebuild_index() == 2
    assert journal._index_db.stat().st_ino == identity
    journal.write_preferences('# User Preferences\n\n- green tea\n')
    assert journal.search('tea')
    connection = sqlite3.connect(str(journal._index_db))
    try:
        assert connection.execute('SELECT value FROM facts').fetchone()[0] == 'keep this fact'
    finally:
        connection.close()
    assert journal.keyword_index_status()['state'] == 'available'


def test_readonly_creation_error_preserves_shared_database(tmp_path, monkeypatch):
    journal = MemoryJournal(workspace=tmp_path)
    connection = _shared_database(journal)
    connection.close()
    before = journal._index_db.read_bytes()
    real_connect = sqlite3.connect

    def readonly_connection():
        connection = real_connect(f'{journal._index_db.resolve().as_uri()}?mode=ro', uri=True)
        try:
            connection.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(path, content)")
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    monkeypatch.setattr(journal, '_try_create_db', readonly_connection)
    with pytest.raises(sqlite3.Error):
        journal._get_db()
    assert 'read-only' in journal.search_degraded()
    assert journal._index_db.read_bytes() == before


def test_remediation_reports_failure_instead_of_false_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    (tmp_path / 'memory_index.db').write_bytes(b'keep damaged original')
    with pytest.raises(RuntimeError, match='database was preserved'):
        _job_rebuild_memory_fts()
    assert (tmp_path / 'memory_index.db').read_bytes() == b'keep damaged original'


@pytest.mark.asyncio
async def test_observability_reports_actual_degraded_keyword_index(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    (tmp_path / 'memory_index.db').write_bytes(b'keep damaged original')
    app = web.Application()
    app['state'] = ConsoleState(None, 0)
    app.router.add_get('/api/memory/observability', api_memory_observability)
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/api/memory/observability')
        assert response.status == 200
        data = await response.json()
        assert data['search_index']['authority'] == 'journal'
        assert data['search_index']['state'] == 'degraded'
        assert data['search_index']['detail']
        assert data['search_index']['repair_id'] == 'memory.rebuild-fts'
    assert (tmp_path / 'memory_index.db').read_bytes() == b'keep damaged original'
