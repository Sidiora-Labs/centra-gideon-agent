import json
import sqlite3

import pytest

from checks.runtime.capabilities.music.test_listening import api_track, store_at
from gideon.workspace.capabilities.music.listening import digest
from gideon.workspace.capabilities.music.spotify import SpotifyBridge
from gideon.workspace.capabilities.music.store import DomainError


def test_config_revision_readiness_and_reopen(tmp_path):
    store = store_at(tmp_path)
    bridge = SpotifyBridge(tmp_path, store)
    assert bridge.config() == {
        "enabled": False,
        "credential_name": "",
        "account_label": "",
        "revision": 0,
    }
    assert not bridge.readiness()["ready_to_sync"]
    value = bridge.configure(
        {
            "enabled": True,
            "credential_name": "spotify",
            "account_label": "Personal",
            "revision": 0,
        }
    )
    assert value["revision"] == 1
    assert not bridge.readiness()["credential_available"]
    again = SpotifyBridge(tmp_path, store)
    assert again.config() == value
    with pytest.raises(DomainError) as error:
        again.configure({**value, "revision": 0})
    assert error.value.status == 409
    assert again.config() == value
    with sqlite3.connect(bridge.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM cursors").fetchone()[0] == 0


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"enabled": "yes", "credential_name": "x", "account_label": "x", "revision": 0},
        {"enabled": True, "credential_name": "x", "account_label": "x", "revision": -1},
        {
            "enabled": True,
            "credential_name": "x",
            "account_label": "x",
            "revision": 0,
            "endpoint": "https://example.com",
        },
    ],
)
def test_configuration_rejects_unknown_or_invalid(tmp_path, data):
    bridge = SpotifyBridge(tmp_path, store_at(tmp_path))
    with pytest.raises(DomainError):
        bridge.configure(data)
    assert bridge.config()["revision"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {},
        {"request_id": "x", "playlist_ids": ["a", "a"]},
        {"request_id": "x", "playlist_ids": ["../"]},
        {"request_id": "x", "playlist_ids": ["a"] * 11},
        {"request_id": "x", "playlist_ids": "abc"},
        {"request_id": "x", "playlist_ids": [], "home": "x"},
    ],
)
async def test_sync_validation_without_remote_calls(tmp_path, data):
    bridge = SpotifyBridge(tmp_path, store_at(tmp_path))
    with pytest.raises(DomainError) as error:
        await bridge.sync(data)
    assert error.value.status == 400
    with sqlite3.connect(bridge.path) as db:
        assert db.execute("SELECT count(*) FROM syncs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_missing_connection_refusal_leaves_no_remote_claim(tmp_path):
    store = store_at(tmp_path)
    bridge = SpotifyBridge(tmp_path, store)
    bridge.configure(
        {
            "enabled": True,
            "credential_name": "missing",
            "account_label": "Personal",
            "revision": 0,
        }
    )
    with pytest.raises(DomainError) as error:
        await bridge.sync({"request_id": "one", "playlist_ids": []})
    assert error.value.status == 503
    assert error.value.code == "spotify_unavailable"
    assert store.imports() == []
    assert bridge.readiness()["remote_status"] == "unverified"
    with sqlite3.connect(bridge.path) as db:
        assert db.execute("SELECT count(*) FROM syncs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM cursors").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_persisted_pending_import_resume_commits_cursor_once(tmp_path):
    store = store_at(tmp_path)
    bridge = SpotifyBridge(tmp_path, store)
    document = {
        "recent": [{"played_at": "2026-01-02T03:04:05Z", "track": api_track()}],
        "playlists": [],
    }
    art = store.artifacts.create(
        name="Imported API snapshot",
        content=json.dumps(document),
        kind="json",
        source="import",
    )
    data = {"request_id": "r" * 200, "playlist_ids": []}
    pending = {
        "account_id": "owner",
        "artifact_ref": {"slug": art.slug, "version": art.version},
        "cursor": 1767323045000,
        "receipt": None,
    }
    with sqlite3.connect(bridge.path) as db:
        db.execute(
            "INSERT INTO syncs VALUES (?,?,?)",
            (data["request_id"], digest(data), json.dumps(pending)),
        )
    receipt = await bridge.sync(data)
    assert receipt["account_id"] == "owner"
    assert receipt["import_receipt"]["added"] == 1
    assert receipt["cursor"] == 1767323045000
    assert not receipt["replayed"]
    assert store.history()["items"][0]["account_label"] == "spotify:owner"
    assert store.history()["items"][0]["ms_played"] is None
    reopened = SpotifyBridge(tmp_path, store_at(tmp_path))
    assert await reopened.sync(data) == {**receipt, "replayed": True}
    with sqlite3.connect(bridge.path) as db:
        assert (
            db.execute(
                "SELECT value FROM cursors WHERE account_id=?", ("owner",)
            ).fetchone()[0]
            == receipt["cursor"]
        )
    with pytest.raises(DomainError) as error:
        await bridge.sync({**data, "playlist_ids": ["abc"]})
    assert error.value.status == 409
    assert store.stats()["event_count"] == 1


@pytest.mark.asyncio
async def test_pending_missing_artifact_never_advances_cursor(tmp_path):
    store = store_at(tmp_path)
    bridge = SpotifyBridge(tmp_path, store)
    data = {"request_id": "pending", "playlist_ids": []}
    pending = {
        "account_id": "owner",
        "artifact_ref": {"slug": "missing", "version": 1},
        "cursor": 123,
        "receipt": None,
    }
    with sqlite3.connect(bridge.path) as db:
        db.execute(
            "INSERT INTO syncs VALUES (?,?,?)",
            (data["request_id"], digest(data), json.dumps(pending)),
        )
    with pytest.raises(DomainError) as error:
        await bridge.sync(data)
    assert error.value.status == 404
    with sqlite3.connect(bridge.path) as db:
        assert db.execute("SELECT count(*) FROM cursors").fetchone()[0] == 0
        assert (
            json.loads(db.execute("SELECT payload FROM syncs").fetchone()[0])["receipt"]
            is None
        )
    assert store.stats()["event_count"] == 0
