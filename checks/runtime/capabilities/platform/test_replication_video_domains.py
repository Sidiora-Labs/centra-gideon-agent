import copy
import json
import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.operations.durability.conflicts import ConflictQueue, STATUS_NEEDS_REVIEW
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.platform import replication_music_video, replication_video
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import DOMAINS, RECEIVE_PATH, ReplicationError, ReplicationService


CASES = (
    (replication_video.SCOPE, replication_video, {
        "id": "timeline-project", "revision": 1, "title": "Editorial cut",
        "width": 32, "height": 24, "fps": 4,
        "segments": [{"artifact_id": "source-frame", "version": 1, "kind": "image", "start": 0, "duration": 1}],
        "overlays": [], "audio": [], "duration": 1.0, "updated_at": "2026-09-25T12:00:00+00:00",
    }),
    (replication_music_video.SCOPE, replication_music_video, {
        "id": "music-video-project", "revision": 1, "title": "Beat cut",
        "track_id": "track-one", "render_id": "render-one", "tempo_bpm": 120, "offset_seconds": 0.25,
        "scenes": [{"id": "opening", "image_ref": {"slug": "scene-image", "version": 1}, "beats": 2, "start_seconds": 0.0, "duration_seconds": 1.0}],
        "duration_seconds": 1.0, "audio_ref": {"slug": "source-audio", "version": 1},
    }),
)


def peer_record(identity, endpoint, scope, revision=0, send=True, receive=True):
    return {
        "label": identity["peer_id"][-8:], "endpoint": endpoint, "public_key": identity["public_key"],
        "enabled": True, "send_categories": [scope] if send else [],
        "receive_categories": [scope] if receive else [], "revision": revision,
    }


def pair(first_home, second_home, scope, second_endpoint="https://second.example"):
    first, second = PeerStore(first_home), PeerStore(second_home)
    first_id, second_id = first.snapshot()["self"], second.snapshot()["self"]
    first.put(second_id["peer_id"], peer_record(second_id, second_endpoint, scope))
    second.put(first_id["peer_id"], peer_record(first_id, "https://first.example", scope))
    return first_id, second_id


def write(adapter, home, data):
    row = {"id": data["id"], "data": copy.deepcopy(data)}
    adapter.write_row(home, adapter.ENTRY_ID, row, data["id"])
    return row


@pytest.mark.parametrize("scope,adapter,data", CASES)
def test_video_domains_are_declared_and_default_denied(tmp_path, scope, adapter, data):
    first, second = tmp_path / "first", tmp_path / "second"
    first_store, second_store = PeerStore(first), PeerStore(second)
    second_id = second_store.snapshot()["self"]
    first_store.put(second_id["peer_id"], peer_record(second_id, "https://second.example", scope, send=False))
    write(adapter, first, data)
    assert DOMAINS[scope].entries == adapter.ENTRIES
    assert scope in first_store.snapshot()["categories"]
    with pytest.raises(ReplicationError, match="policy denies"):
        ReplicationService(first).export_batch(second_id["peer_id"], scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,adapter,data", CASES)
async def test_signed_video_domain_route_dispatches_canonical_current_rows(tmp_path, scope, adapter, data):
    first, second = tmp_path / "first", tmp_path / "second"
    write(adapter, first, data)
    receiver = ReplicationService(second)

    async def receive(request):
        envelope = await request.json()
        assert envelope["proof"]["scope"] == scope
        peer = PeerStore(second).verify_proof(envelope["proof"])
        return web.json_response(receiver.apply_batch(peer["id"], envelope["payload"]))

    app = web.Application()
    app.router.add_post(RECEIVE_PATH, receive)
    async with TestServer(app) as server:
        first_id, second_id = pair(first, second, scope, str(server.make_url("/")))
        malformed = ReplicationService(first).export_batch(second_id["peer_id"], scope)
        malformed = copy.deepcopy(malformed)
        malformed["entries"][0]["rows"][0]["data"]["request_id"] = "private-request"
        with pytest.raises(ReplicationError) as rejected:
            receiver.apply_batch(first_id["peer_id"], malformed)
        assert rejected.value.status == 422
        result = await ReplicationService(first).push(second_id["peer_id"], scope)

    assert result["accepted"] is True
    assert result["entries"] == [{"entry_id": adapter.ENTRY_ID, "added": 1, "updated": 0, "removed": 0, "conflicts": 0}]
    assert adapter.read_rows(second, adapter.ENTRY_ID) == [{"id": data["id"], "data": data}]
    assert NativeArtifactProvider(second / "artifacts").list() == []
    database_path = second / ("capabilities/media/timelines.sqlite3" if scope == replication_video.SCOPE else "capabilities/music/music_video.sqlite3")
    authority_table = "requests" if scope == replication_video.SCOPE else "jobs"
    with sqlite3.connect(database_path) as database:
        assert database.execute(f"SELECT count(*) FROM {authority_table}").fetchone()[0] == 0


@pytest.mark.parametrize("scope,adapter,data", CASES)
def test_central_video_conflicts_restore_selected_fields(tmp_path, scope, adapter, data):
    first, second = tmp_path / "first", tmp_path / "second"
    first_id, second_id = pair(first, second, scope)
    write(adapter, first, data)
    first_service, second_service = ReplicationService(first), ReplicationService(second)
    baseline = first_service.export_batch(second_id["peer_id"], scope)
    second_service.apply_batch(first_id["peer_id"], baseline)
    first_service._record_sent(second_id["peer_id"], baseline)

    local, remote = copy.deepcopy(data), copy.deepcopy(data)
    local.update(title="Local title", revision=2)
    remote.update(title="Peer title", revision=2)
    if scope == replication_video.SCOPE:
        local["updated_at"] = "2026-09-25T12:05:00+00:00"
        remote["updated_at"] = "2026-09-25T12:06:00+00:00"
    write(adapter, first, local)
    write(adapter, second, remote)
    incoming = second_service.export_batch(first_id["peer_id"], scope)
    result = first_service.apply_batch(second_id["peer_id"], incoming)
    assert result["entries"][0]["conflicts"] == 1
    pending = ConflictQueue(first).items(status=STATUS_NEEDS_REVIEW)
    assert len(pending) == 1 and pending[0].entry_id == adapter.ENTRY_ID
    restored = first_service.restore_fields(pending[0].id, ["title"])
    assert restored["entry_id"] == adapter.ENTRY_ID and restored["fields"] == ["title"]
    current = adapter.read_rows(first, adapter.ENTRY_ID)[0]["data"]
    assert current["title"] == "Peer title" and current["revision"] == 3
    assert json.dumps(current, sort_keys=True).find("private-request") == -1
