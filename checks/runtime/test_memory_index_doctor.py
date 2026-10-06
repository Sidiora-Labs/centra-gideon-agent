from __future__ import annotations

import pytest

from gideon.cognition.memory import MemoryJournal
from gideon.operations.resilience.doctor import (
    DoctorContext,
    _probe_memory_keyword_index,
    all_probes,
)


@pytest.mark.asyncio
async def test_keyword_probe_checks_real_projection_without_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    journal = MemoryJournal()
    journal.init()
    journal.write_preferences("# Preferences\n- tea\n")
    journal.rebuild_index()
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = await _probe_memory_keyword_index(DoctorContext(home=tmp_path))
    assert result.ok
    assert result.evidence["indexes"][0]["desync_count"] == 0
    assert {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    } == before


@pytest.mark.asyncio
async def test_keyword_probe_detects_changed_journal_and_offers_real_repair(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    journal = MemoryJournal()
    journal.init()
    journal.rebuild_index()
    journal._pages[0].location.write_text("# Preferences\n- a new fact\n")
    result = await _probe_memory_keyword_index(DoctorContext(home=tmp_path))
    assert not result.ok
    assert result.evidence["indexes"][0]["desync_count"] == 1
    assert result.fix_id == "memory.rebuild-fts"


@pytest.mark.asyncio
async def test_keyword_probe_does_not_open_malformed_database_or_remove_sidecars(
    tmp_path,
):
    paths = [tmp_path / ("memory_index.db" + suffix) for suffix in ("", "-wal", "-shm")]
    for path in paths:
        path.write_bytes(("original " + path.name).encode())
    before = {path: path.read_bytes() for path in paths}
    result = await _probe_memory_keyword_index(DoctorContext(home=tmp_path))
    assert not result.ok
    assert result.fix_id == "memory.rebuild-fts"
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.asyncio
async def test_keyword_probe_absent_index_never_initializes_journal(tmp_path):
    result = await _probe_memory_keyword_index(DoctorContext(home=tmp_path))
    assert result.ok
    assert list(tmp_path.iterdir()) == []
    directory = tmp_path / "workspace" / "memory"
    directory.mkdir(parents=True)
    (directory / "preferences.md").write_text("Existing unindexed fact")
    result = await _probe_memory_keyword_index(DoctorContext(home=tmp_path))
    assert not result.ok
    assert result.fix_id == "memory.rebuild-fts"
    assert not (tmp_path / "memory_index.db").exists()


def test_keyword_probe_registered_separately_from_active_native_storage():
    probes = {probe.id: probe for probe in all_probes()}
    assert "memory.store" in probes
    assert probes["memory.keyword-search"].run is _probe_memory_keyword_index


@pytest.mark.asyncio
async def test_workspace_failure_does_not_offer_default_home_only_repair(tmp_path):
    workspace = tmp_path / "isolated-workspace"
    workspace.mkdir()
    (workspace / "memory_index.db").write_bytes(b"Original malformed workspace DB")
    result = await _probe_memory_keyword_index(DoctorContext(home=tmp_path))
    assert not result.ok
    assert result.fix_id is None
