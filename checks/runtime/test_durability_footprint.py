from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.operations.durability import footprint, inventory, service


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    return tmp_path


def database(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, body BLOB)")
        connection.executemany(
            "INSERT INTO documents(body) VALUES(?)", [(b"x" * 8192,)] * 100
        )
        connection.execute("DELETE FROM documents WHERE id > 1")
    return path.stat().st_size


def test_growth_and_mixed_results_keep_signs_and_magnitudes():
    result = footprint.ReclaimResult(100, 160, 2, {"growing": 90, "shrinking": -30})
    data = result.to_dict()
    assert data["net_change_bytes"] == 60
    assert data["freed_bytes"] == 0
    assert data["growth_bytes"] == 60
    assert data["per_store_freed"] == {"shrinking": 30}
    assert data["per_store_growth"] == {"growing": 90}
    assert "grew 60 bytes" in result.describe()
    assert "freed -" not in result.describe()
    equal = footprint.ReclaimResult(100, 100, 2, {"a": 30, "b": -30})
    assert "No net footprint change" in equal.describe()


def test_real_locked_store_does_not_stop_other_compaction(home):
    locked_path = home / "memory.db"
    compact_path = home / "memory_index.db"
    database(locked_path)
    before = database(compact_path)
    unowned = home / "unregistered.db"
    unowned_before = database(unowned)
    locked = sqlite3.connect(locked_path, isolation_level=None)
    try:
        locked.execute("BEGIN EXCLUSIVE")
        result = footprint.reclaim(home)
    finally:
        locked.rollback()
        locked.close()
    memory_id = next(
        entry.id for entry in inventory.sqlite_entries() if entry.path == "memory.db"
    )
    assert result.skipped[memory_id] == "database is busy"
    assert compact_path.stat().st_size < before
    assert result.freed_bytes > 0
    assert result.growth_bytes == 0
    assert unowned.stat().st_size == unowned_before
    with sqlite3.connect(compact_path) as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_external_link_is_skipped_without_path_disclosure(home, tmp_path_factory):
    outside = tmp_path_factory.mktemp("external-store") / "private.db"
    before = database(outside)
    (home / "memory.db").symlink_to(outside)
    result = footprint.reclaim(home)
    assert result.stores == 0
    assert len(result.skipped) == 1
    assert str(outside) not in json.dumps(result.to_dict())
    assert outside.stat().st_size == before


def test_service_and_cli_use_actual_reclaim_result(home, capsys, caplog):
    database(home / "memory.db")
    with caplog.at_level("INFO", logger=service.__name__):
        service._tick_footprint_maintenance()
    assert "Reclaimed" in caplog.text
    assert "freed -" not in caplog.text
    assert time.time() - service.load_state()["last_reclaim"] < 10
    assert footprint.footprint_cmd(argparse.Namespace(reclaim=True, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job"] == "reclaim"
    assert payload["extra"]["freed_bytes"] >= 0
    assert payload["extra"]["growth_bytes"] >= 0


@pytest.mark.asyncio
async def test_existing_api_returns_structured_reclaim_result(home):
    from gideon.interfaces.dashboard.handlers.durability import api_durability_run

    database(home / "memory.db")
    app = web.Application()
    app.router.add_post("/api/durability/run", api_durability_run)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/durability/run", json={"job": "reclaim"})
        assert response.status == 200
        payload = await response.json()
    assert payload["extra"]["net_change_bytes"] < 0
    assert payload["extra"]["freed_bytes"] > 0
    assert payload["extra"]["growth_bytes"] == 0
    assert str(home) not in json.dumps(payload)
