"""New media state survives backups without exporting peer authority."""
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest
from gideon.operations.durability import inventory
from gideon.workspace.snapshot import snapshot_main, _extra_restore_paths_for_test_paths


@pytest.mark.parametrize("path,kind", [
    ("capabilities/platform/identity.key", inventory.KIND_TREE),
    ("capabilities/platform/peers.sqlite3", inventory.KIND_SQLITE),
    ("capabilities/platform/replication.sqlite3", inventory.KIND_SQLITE),
    ("capabilities/platform/media_shares.sqlite3", inventory.KIND_SQLITE),
])
def test_peer_authority_cannot_be_exported_or_restored_as_domain_data(path, kind):
    entry = inventory.claim_for(path)
    assert entry is not None
    assert entry.kind == kind
    assert entry.domain == inventory.DOMAIN_SECURITY
    assert entry.secret and not entry.derived
    assert entry in inventory.backup_entries()
    assert entry not in inventory.export_entries()
    assert path in inventory.secret_paths()
    assert path not in _extra_restore_paths_for_test_paths()
    if kind == inventory.KIND_SQLITE:
        assert entry in inventory.sqlite_entries()
        assert inventory.is_ignored(path + "-wal")
        assert inventory.is_ignored(path + "-shm")


def test_new_media_database_and_listener_settings_survive_snapshot(tmp_path, monkeypatch):
    home = tmp_path / "allocation"
    media = home / "capabilities/media/sprites.sqlite3"
    settings = home / "capabilities/platform/inference_host.json"
    media.parent.mkdir(parents=True)
    settings.parent.mkdir(parents=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    settings.write_text(json.dumps({"version": 1, "enabled": False, "revision": 7}))
    with sqlite3.connect(media) as connection:
        connection.execute("CREATE TABLE retained (id TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO retained VALUES (?, ?)", ("approved", "sha-bound-atlas"))
    assert inventory.claim_for(str(media.relative_to(home))).kind == inventory.KIND_SQLITE
    assert not inventory.audit_home(home).undeclared_dbs
    video = home / "capabilities/knowledge/videos/source/captions.txt"
    video.parent.mkdir(parents=True)
    video.write_text("Retained original source captions")
    output = tmp_path / "backups"
    assert snapshot_main([str(output)]) == 0
    archives = list(output.glob("gideon-snapshot-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as archive:
        names = archive.getnames()
        video_member = next(name for name in names if name.endswith("/capabilities/knowledge/videos/source/captions.txt"))
        assert archive.extractfile(video_member).read() == video.read_bytes()
        database_member = next(name for name in names if name.endswith("/capabilities/media/sprites.sqlite3"))
        config_member = next(name for name in names if name.endswith("/capabilities/platform/inference_host.json"))
        assert archive.extractfile(config_member).read() == settings.read_bytes()
        restored = tmp_path / "restored.sqlite3"
        restored.write_bytes(archive.extractfile(database_member).read())
        assert not any(name.endswith(("sprites.sqlite3-wal", "sprites.sqlite3-shm")) for name in names)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT id,value FROM retained").fetchall() == [("approved", "sha-bound-atlas")]
    restorable = _extra_restore_paths_for_test_paths()
    assert "capabilities/media/sprites.sqlite3" in restorable
    assert "capabilities/platform/inference_host.json" in restorable


@pytest.mark.parametrize("relative", [
    "capabilities/platform/integration_apps.sqlite3",
    "capabilities/platform/remote-agent-sessions.sqlite3",
    "capabilities/platform_quotas.sqlite3",
    "capabilities/platform/replication.sqlite3",
    "capabilities/platform/media_shares.sqlite3",
])
def test_new_platform_state_preserves_committed_wal_in_backup(tmp_path, monkeypatch, relative):
    home = tmp_path / "allocation"
    database = home / relative
    database.parent.mkdir(parents=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    entry = inventory.claim_for(relative)
    assert entry is not None and entry.kind == inventory.KIND_SQLITE
    assert entry in inventory.backup_entries()
    with sqlite3.connect(database) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE receipts (id TEXT PRIMARY KEY, revision INTEGER)")
        writer.execute("INSERT INTO receipts VALUES (?, ?)", ("retained-operation", 9))
        writer.commit()
        assert Path(str(database) + "-wal").stat().st_size > 0
        assert not inventory.audit_home(home).undeclared_dbs
        output = tmp_path / "snapshots"
        assert snapshot_main([str(output)]) == 0
        archives = list(output.glob("gideon-snapshot-*.tar.gz"))
        assert len(archives) == 1
        with tarfile.open(archives[0]) as archive:
            names = archive.getnames()
            member = next(name for name in names if name.endswith("/" + relative))
            restored = tmp_path / "restored.sqlite3"
            restored.write_bytes(archive.extractfile(member).read())
            assert not any(name.endswith((relative + "-wal", relative + "-shm")) for name in names)
        with sqlite3.connect(restored) as reader:
            assert reader.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert reader.execute("SELECT id,revision FROM receipts").fetchall() == [("retained-operation", 9)]
        assert writer.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
