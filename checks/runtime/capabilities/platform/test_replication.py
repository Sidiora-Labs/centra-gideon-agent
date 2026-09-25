import json
import os
import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.interfaces.dashboard.handlers.capabilities_replication import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.operations.durability.conflicts import ConflictQueue, STATUS_NEEDS_REVIEW
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import DOMAINS, ReplicationError, ReplicationService
from gideon.workspace.capabilities.platform.replication_tools import create_provider

SCOPE = "workspace.records"
PREFIX = "/api/capabilities/platform/replication"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    use_ephemeral_secret()
    return tmp_path


def peer_record(identity, endpoint, revision=0, enabled=True, send=None, receive=None):
    return {"label": identity["peer_id"][-8:], "endpoint": endpoint, "public_key": identity["public_key"], "enabled": enabled, "send_categories": list([SCOPE] if send is None else send), "receive_categories": list([SCOPE] if receive is None else receive), "revision": revision}


def pair(a_home, b_home, a_endpoint="https://a.example", b_endpoint="https://b.example"):
    a, b = PeerStore(a_home), PeerStore(b_home)
    aid, bid = a.snapshot()["self"], b.snapshot()["self"]
    a.put(bid["peer_id"], peer_record(bid, b_endpoint))
    b.put(aid["peer_id"], peer_record(aid, a_endpoint))
    return aid, bid


def in_home(home, action):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(home)
    try:
        return action(HierarchyStore())
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous


def create_project(home, name="Alpha", brief="base"):
    return in_home(home, lambda store: store.create_project(name, brief=brief))


def project(home, identity):
    return in_home(home, lambda store: store.get_project(identity))


def update_project(home, identity, brief):
    return in_home(home, lambda store: store.update_project(identity, brief=brief))


def delete_project(home, identity):
    return in_home(home, lambda store: store.delete_project(identity))


def test_export_is_fixed_coverage_idempotent_versioned_and_secret_free(home, tmp_path):
    remote_home = tmp_path / "remote"
    _, remote = pair(home, remote_home)
    created = create_project(home)
    service = ReplicationService(home)
    first = service.export_batch(remote["peer_id"], SCOPE)
    second = ReplicationService(home).export_batch(remote["peer_id"], SCOPE)
    assert first == second
    assert first["schema_version"] == 1
    assert first["domain"] == SCOPE
    assert first["sequence"] == 1
    assert len(first["batch_id"]) == 32
    assert [item["entry_id"] for item in first["entries"]] == ["projects", "tasks"]
    rows = first["entries"][0]["rows"]
    assert rows[0]["id"] == created.id + "/project"
    wire = json.dumps(first)
    for forbidden in ("private_key", "identity.key", "peers.sqlite3", "replication.sqlite3", "nonce", "signature"):
        assert forbidden not in wire
    update_project(home, created.id, "changed")
    changed = service.export_batch(remote["peer_id"], SCOPE)
    assert changed["sequence"] == 2
    assert changed["batch_id"] != first["batch_id"]
    assert changed["entries"][0]["rows"][0]["data"]["brief"] == "changed"


def test_two_home_create_edit_delete_and_offline_restart(home, tmp_path):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair(a_home, b_home)
    created = create_project(a_home, brief="created on A")
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    first = a.export_batch(bid["peer_id"], SCOPE)
    response = b.apply_batch(aid["peer_id"], first)
    assert response["accepted"] is True
    assert response["entries"][0]["added"] == 1
    assert project(b_home, created.id).brief == "created on A"
    assert ReplicationService(b_home).apply_batch(aid["peer_id"], first) == response
    update_project(a_home, created.id, "edited after offline interval")
    second = ReplicationService(a_home).export_batch(bid["peer_id"], SCOPE)
    assert second["sequence"] == 2
    assert ReplicationService(b_home).apply_batch(aid["peer_id"], second)["sequence"] == 2
    assert project(b_home, created.id).brief == "edited after offline interval"
    assert delete_project(a_home, created.id)
    third = ReplicationService(a_home).export_batch(bid["peer_id"], SCOPE)
    marker = next(row for row in third["entries"][0]["rows"] if row["id"] == created.id + "/project")
    assert "deleted_at" in marker
    removed = ReplicationService(b_home).apply_batch(aid["peer_id"], third)
    assert removed["entries"][0]["removed"] == 1
    assert project(b_home, created.id) is None
    with sqlite3.connect(b.path) as connection:
        assert connection.execute("SELECT sequence FROM cursors WHERE peer_id=? AND domain=?", (aid["peer_id"], SCOPE)).fetchone()[0] == 3


def test_independent_two_home_edits_queue_exact_conflict_and_hold_local(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair(a_home, b_home)
    created = create_project(a_home, brief="shared base")
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], SCOPE)
    b.apply_batch(aid["peer_id"], baseline)
    a._record_sent(bid["peer_id"], baseline)
    update_project(a_home, created.id, "local A edit")
    update_project(b_home, created.id, "remote B edit")
    incoming = b.export_batch(aid["peer_id"], SCOPE)
    result = a.apply_batch(bid["peer_id"], incoming)
    assert result["entries"][0]["conflicts"] == 1
    assert project(a_home, created.id).brief == "local A edit"
    queued = ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW)
    assert len(queued) == 1
    conflict = queued[0]
    assert conflict.entry_id == "projects"
    assert conflict.entity_id == created.id + "/project"
    assert conflict.local_row["data"]["brief"] == "local A edit"
    assert conflict.remote_row["data"]["brief"] == "remote B edit"
    assert conflict.surface == "durability"
    assert a.apply_batch(bid["peer_id"], incoming) == result
    assert len(ConflictQueue(a_home).items()) == 1


def test_replay_sequence_gap_mixed_version_coverage_and_sender_denials(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair(a_home, b_home)
    create_project(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    batch = a.export_batch(bid["peer_id"], SCOPE)
    b.apply_batch(aid["peer_id"], batch)
    changed = json.loads(json.dumps(batch)); changed["entries"][0]["rows"][0]["data"]["brief"] = "tampered"
    with pytest.raises(ReplicationError, match="reused") as replay:
        b.apply_batch(aid["peer_id"], changed)
    assert replay.value.status == 409
    gap = json.loads(json.dumps(batch)); gap["sequence"] = 3; gap["batch_id"] = "3" * 32
    with pytest.raises(ReplicationError, match="expects sequence 2"):
        b.apply_batch(aid["peer_id"], gap)
    newer = json.loads(json.dumps(batch)); newer["schema_version"] = 2; newer["sequence"] = 2; newer["batch_id"] = "2" * 32
    with pytest.raises(ReplicationError, match="schema"):
        b.apply_batch(aid["peer_id"], newer)
    secret = json.loads(json.dumps(batch)); secret["sequence"] = 2; secret["batch_id"] = "4" * 32; secret["entries"][0]["entry_id"] = "auth"
    with pytest.raises(ReplicationError, match="coverage"):
        b.apply_batch(aid["peer_id"], secret)
    wrong = json.loads(json.dumps(batch)); wrong["sequence"] = 2; wrong["batch_id"] = "5" * 32; wrong["sender"] = bid["peer_id"]
    with pytest.raises(ReplicationError, match="sender") as sender:
        b.apply_batch(aid["peer_id"], wrong)
    assert sender.value.status == 403


def test_disabled_and_directional_policy_deny_before_export_or_apply(home, tmp_path):
    remote_home = tmp_path / "remote"
    local_id, remote_id = pair(home, remote_home)
    local_peer = PeerStore(home).get(remote_id["peer_id"])
    PeerStore(home).put(remote_id["peer_id"], peer_record(remote_id, local_peer["endpoint"], revision=1, send=[]))
    with pytest.raises(ReplicationError, match="export") as denied:
        ReplicationService(home).export_batch(remote_id["peer_id"], SCOPE)
    assert denied.value.status == 403
    remote_service = ReplicationService(remote_home)
    remote_peer = PeerStore(remote_home).get(local_id["peer_id"])
    PeerStore(remote_home).put(local_id["peer_id"], peer_record(local_id, remote_peer["endpoint"], revision=1, receive=[]))
    batch = {"schema_version": 1, "domain": SCOPE, "sender": local_id["peer_id"], "sequence": 1, "batch_id": "a" * 32, "entries": [{"entry_id": "projects", "rows": []}, {"entry_id": "tasks", "rows": []}]}
    with pytest.raises(ReplicationError, match="import") as inbound:
        remote_service.apply_batch(local_id["peer_id"], batch)
    assert inbound.value.status == 403
    assert not remote_service.path.exists()


@pytest.mark.asyncio
async def test_real_signed_guarded_push_between_two_homes(home):
    a_home, b_home = home / "a", home / "b"
    receiver = ReplicationService(b_home)

    async def receive(request):
        envelope = await request.json()
        peer = PeerStore(b_home).verify_proof(envelope["proof"])
        return web.json_response(receiver.apply_batch(peer["id"], envelope["payload"]))

    app = web.Application(); app.router.add_post("/api/capabilities/platform/replication/receive", receive)
    async with TestServer(app) as server:
        aid, bid = pair(a_home, b_home, b_endpoint=str(server.make_url("/")))
        created = create_project(a_home, brief="signed transport")
        result = await ReplicationService(a_home).push(bid["peer_id"], SCOPE)
    assert result["accepted"] is True
    assert result["sequence"] == 1
    assert project(b_home, created.id).brief == "signed transport"
    with sqlite3.connect(ReplicationService(a_home).path) as connection:
        assert connection.execute("SELECT count(*) FROM ancestors WHERE peer_id=?", (bid["peer_id"],)).fetchone()[0] >= 1
    assert aid["peer_id"] == PeerStore(b_home).get(aid["peer_id"])["id"]


@pytest.mark.asyncio
async def test_http_boundaries_status_native_and_signed_receive(home, tmp_path):
    remote_home = tmp_path / "remote"
    local_id, remote_id = pair(home, remote_home)
    create_project(remote_home)
    remote_batch = ReplicationService(remote_home).export_batch(local_id["peer_id"], SCOPE)
    proof = PeerStore(remote_home).create_proof(local_id["peer_id"], SCOPE)
    app = web.Application(middlewares=[token_auth_middleware(port=8000)]); register(app)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status == 403
        headers = {"Cookie": "gideon_token_8000=" + generate_token("dashboard:replication")}
        status = await client.get(PREFIX, headers=headers)
        assert status.status == 200
        assert status.headers["Cache-Control"] == "no-store"
        denied = await client.post(f"{PREFIX}/receive", json={"proof": proof, "payload": remote_batch})
        assert denied.status == 403
        accepted = await client.post(f"{PREFIX}/receive", headers=headers, json={"proof": proof, "payload": remote_batch})
        assert accepted.status == 200
        assert (await accepted.json())["sequence"] == 1
        native = await create_provider().invoke("platform_replication_status", {})
        assert native.success
        projection = json.loads(native.output)
        assert projection["cursors"][0]["sequence"] == 1
        assert "private_key" not in native.output
        assert "rows" not in native.output
    assert not (home / "capabilities/platform/replication.sqlite3-wal").exists() or (home / "capabilities/platform/replication.sqlite3").exists()


def test_status_reports_declared_coverage_cursor_and_conflict_only(home, tmp_path):
    remote_home = tmp_path / "remote"
    local_id, remote_id = pair(home, remote_home)
    service = ReplicationService(home)
    value = service.status()
    assert value["domains"] == [{"scope": SCOPE, "entries": ["projects", "tasks"]}]
    assert value["cursors"] == []
    assert value["conflicts"] == []
    assert value["peers"][0]["id"] == remote_id["peer_id"]
    assert set(DOMAINS) == {SCOPE}
    encoded = json.dumps(value)
    assert "private_key" not in encoded
    assert "identity.key" not in encoded
    invalid = pytest.raises(ReplicationError, match="Unsupported")
    with invalid:
        service.export_batch(remote_id["peer_id"], "identity.secrets")
