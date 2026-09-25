import base64
import json
import os
import sqlite3
import stat
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_peers import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.workspace.capabilities.platform.peer_tools import create_provider
from gideon.workspace.capabilities.platform.peers import CATEGORIES, PeerError, PeerStore

PREFIX = "/api/capabilities/platform/peers"
SCOPE = "experience.world_guest"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    return tmp_path


def record(identity, endpoint="https://peer.example", revision=0, enabled=True, send=None, receive=None, label="Remote peer"):
    return {
        "label": label,
        "endpoint": endpoint,
        "public_key": identity["public_key"],
        "enabled": enabled,
        "send_categories": list(send if send is not None else [SCOPE]),
        "receive_categories": list(receive if receive is not None else [SCOPE]),
        "revision": revision,
    }


def pair(sender, receiver, sender_endpoint="https://sender.example", receiver_endpoint="https://receiver.example"):
    sender_identity = sender.snapshot()["self"]
    receiver_identity = receiver.snapshot()["self"]
    sender.put(receiver_identity["peer_id"], record(receiver_identity, receiver_endpoint))
    receiver.put(sender_identity["peer_id"], record(sender_identity, sender_endpoint))
    return sender_identity, receiver_identity


def test_identity_is_stable_private_and_never_projected(home):
    store = PeerStore(home)
    first = store.snapshot()
    second = PeerStore(home).snapshot()
    assert first == second
    assert first["version"] == 1
    assert first["categories"] == list(CATEGORIES)
    assert first["peers"] == []
    assert first["self"]["peer_id"].startswith("peer-")
    assert len(first["self"]["peer_id"]) == 37
    assert len(base64.urlsafe_b64decode(first["self"]["public_key"] + "==")) == 32
    key = home / "capabilities/platform/identity.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    raw = json.loads(key.read_text())
    assert raw["private_key"] not in json.dumps(first)
    assert "private_key" not in json.dumps(first)
    assert (home / "capabilities/platform/peers.sqlite3").exists()


def test_refuses_identity_key_with_broad_permissions(home):
    store = PeerStore(home)
    store.snapshot()
    os.chmod(store.key_path, 0o644)
    with pytest.raises(PeerError, match="permissions") as error:
        store.snapshot()
    assert error.value.status == 503


def test_peer_policy_crud_restart_and_optimistic_conflicts(home, tmp_path):
    store = PeerStore(home)
    remote = PeerStore(tmp_path / "remote").snapshot()["self"]
    created = store.put(remote["peer_id"], record(remote))
    assert created["revision"] == 1
    assert created["send_categories"] == [SCOPE]
    assert created["receive_categories"] == [SCOPE]
    assert created["enabled"] is True
    assert created["created_at"] == created["updated_at"]
    assert PeerStore(home).get(remote["peer_id"]) == created
    revised = store.put(remote["peer_id"], record(remote, revision=1, enabled=False, send=["media.assets", SCOPE], receive=[], label="Paused"))
    assert revised["revision"] == 2
    assert revised["label"] == "Paused"
    assert revised["send_categories"] == [SCOPE, "media.assets"]
    assert revised["receive_categories"] == []
    assert revised["created_at"] == created["created_at"]
    with pytest.raises(PeerError, match="changed") as stale:
        store.put(remote["peer_id"], record(remote, revision=1))
    assert stale.value.status == 409
    with pytest.raises(PeerError, match="changed"):
        store.delete(remote["peer_id"], 1)
    store.delete(remote["peer_id"], 2)
    with pytest.raises(PeerError, match="not found") as missing:
        store.get(remote["peer_id"])
    assert missing.value.status == 404


@pytest.mark.parametrize("change,message", [
    ({"label": ""}, "label"),
    ({"endpoint": "file:///tmp/peer"}, "endpoint"),
    ({"endpoint": "https://user:pw@example.com"}, "endpoint"),
    ({"enabled": 1}, "enabled"),
    ({"revision": -1}, "revision"),
    ({"send_categories": [SCOPE, SCOPE]}, "categories"),
    ({"receive_categories": ["INVALID"]}, "categories"),
    ({"public_key": "not-a-key"}, "public key"),
])
def test_invalid_peer_records_do_not_write(home, tmp_path, change, message):
    store = PeerStore(home)
    identity = PeerStore(tmp_path / "remote").snapshot()["self"]
    body = record(identity)
    body.update(change)
    with pytest.raises(PeerError, match=message):
        store.put(identity["peer_id"], body)
    assert store.snapshot()["peers"] == []


def test_peer_id_is_cryptographically_bound_to_public_key(home, tmp_path):
    store = PeerStore(home)
    first = PeerStore(tmp_path / "first").snapshot()["self"]
    second = PeerStore(tmp_path / "second").snapshot()["self"]
    with pytest.raises(PeerError, match="does not match"):
        store.put(first["peer_id"], record(second))
    assert store.snapshot()["peers"] == []


def test_directional_policy_is_exact_and_disabled_peer_denies(home, tmp_path):
    store = PeerStore(home)
    peer = PeerStore(tmp_path / "remote").snapshot()["self"]
    store.put(peer["peer_id"], record(peer, send=[SCOPE], receive=["media.assets"]))
    assert store.allows(peer["peer_id"], SCOPE, "send") is True
    assert store.allows(peer["peer_id"], SCOPE, "receive") is False
    assert store.allows(peer["peer_id"], "media.assets", "receive") is True
    with pytest.raises(PeerError, match="direction"):
        store.allows(peer["peer_id"], SCOPE, "sideways")
    current = store.get(peer["peer_id"])
    store.put(peer["peer_id"], record(peer, revision=current["revision"], enabled=False))
    assert store.allows(peer["peer_id"], SCOPE, "send") is False
    assert store.allows(peer["peer_id"], SCOPE, "receive") is False


def test_real_ed25519_proof_exact_recipient_scope_expiry_and_nonce(home, tmp_path):
    sender = PeerStore(home / "sender")
    receiver = PeerStore(home / "receiver")
    sender_identity, receiver_identity = pair(sender, receiver)
    before = int(time.time())
    proof = sender.create_proof(receiver_identity["peer_id"], SCOPE, ttl_seconds=30)
    assert set(proof) == {"version", "sender", "recipient", "scope", "expires_at", "nonce", "signature"}
    assert proof["sender"] == sender_identity["peer_id"]
    assert proof["recipient"] == receiver_identity["peer_id"]
    assert proof["scope"] == SCOPE
    assert before < proof["expires_at"] <= before + 30
    assert len(base64.urlsafe_b64decode(proof["signature"] + "==")) == 64
    verified = receiver.verify_proof(proof, now=before)
    assert verified["id"] == sender_identity["peer_id"]
    assert "private_key" not in json.dumps(verified)
    with pytest.raises(PeerError, match="already used") as replay:
        receiver.verify_proof(proof, now=before)
    assert replay.value.status == 409
    with sqlite3.connect(receiver.db_path) as connection:
        assert connection.execute("SELECT sender,nonce,expires_at FROM nonces").fetchall() == [(sender_identity["peer_id"], proof["nonce"], proof["expires_at"])]


@pytest.mark.parametrize("field,value,message,status", [
    ("recipient", "peer-00000000000000000000000000000000", "recipient", 401),
    ("scope", "media.assets", "policy", 403),
    ("expires_at", 0, "expiry", 401),
    ("nonce", "short", "nonce", 401),
])
def test_proof_rejects_recipient_scope_expiry_and_nonce(home, field, value, message, status):
    sender = PeerStore(home / "sender")
    receiver = PeerStore(home / "receiver")
    _, receiver_identity = pair(sender, receiver)
    proof = sender.create_proof(receiver_identity["peer_id"], SCOPE)
    proof[field] = value
    with pytest.raises(PeerError, match=message) as error:
        receiver.verify_proof(proof)
    assert error.value.status == status


def test_proof_signature_covers_every_authority_field(home):
    sender = PeerStore(home / "sender")
    receiver = PeerStore(home / "receiver")
    _, receiver_identity = pair(sender, receiver)
    proof = sender.create_proof(receiver_identity["peer_id"], SCOPE)
    signature = proof["signature"]
    proof["nonce"] = "A" * 24
    proof["signature"] = signature
    with pytest.raises(PeerError, match="signature") as error:
        receiver.verify_proof(proof)
    assert error.value.status == 401
    with sqlite3.connect(receiver.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM nonces").fetchone()[0] == 0


def test_proof_creation_requires_outbound_policy_and_bounded_ttl(home, tmp_path):
    sender = PeerStore(home)
    receiver = PeerStore(tmp_path / "receiver").snapshot()["self"]
    sender.put(receiver["peer_id"], record(receiver, send=[], receive=[SCOPE]))
    with pytest.raises(PeerError, match="outbound") as denied:
        sender.create_proof(receiver["peer_id"], SCOPE)
    assert denied.value.status == 403
    current = sender.get(receiver["peer_id"])
    sender.put(receiver["peer_id"], record(receiver, revision=current["revision"]))
    for ttl in (0, 301, 1.5):
        with pytest.raises(PeerError, match="lifetime"):
            sender.create_proof(receiver["peer_id"], SCOPE, ttl)


@pytest.mark.asyncio
async def test_signed_transport_uses_real_guarded_http_and_receiver_store(home):
    sender = PeerStore(home / "sender")
    receiver = PeerStore(home / "receiver")
    seen = {}

    async def inbound(request):
        seen["content_type"] = request.headers.get("Content-Type")
        seen["body"] = await request.json()
        peer = receiver.verify_proof(seen["body"]["proof"])
        return web.json_response({"accepted": seen["body"]["payload"], "peer_id": peer["id"]})

    app = web.Application()
    app.router.add_post("/world/guest", inbound)
    async with TestServer(app) as server:
        sender_identity, receiver_identity = pair(sender, receiver, receiver_endpoint=str(server.make_url("/")))
        result = await sender.post_signed(receiver_identity["peer_id"], SCOPE, "/world/guest", {"guest_id": "guest-1"})
    assert result == {"accepted": {"guest_id": "guest-1"}, "peer_id": sender_identity["peer_id"]}
    assert seen["content_type"] == "application/json"
    assert set(seen["body"]) == {"proof", "payload"}
    assert seen["body"]["proof"]["scope"] == SCOPE
    assert seen["body"]["payload"] == {"guest_id": "guest-1"}
    assert sender.get(receiver_identity["peer_id"])["last_probe"] is not None


@pytest.mark.asyncio
async def test_inbound_verify_handler_requires_peer_proof_without_dashboard_session(home, tmp_path):
    remote_store = PeerStore(tmp_path / "remote")
    remote = remote_store.snapshot()["self"]
    local_store = PeerStore(home)
    local = local_store.snapshot()["self"]
    local_store.put(remote["peer_id"], record(remote))
    remote_store.put(local["peer_id"], record(local))
    app = web.Application()
    register(app)
    async with TestClient(TestServer(app)) as client:
        proof = remote_store.create_proof(local["peer_id"], SCOPE)
        verified = await client.post(f"{PREFIX}/proofs/verify", json={"proof": proof, "payload": {"guest_id": "guest-1"}})
        assert verified.status == 200
        result = await verified.json()
        assert result["verified"] is True
        assert result["peer"]["id"] == remote["peer_id"]
        assert result["payload"] == {"guest_id": "guest-1"}
        assert (await client.get(PREFIX)).status == 403
        assert (await client.post(f"{PREFIX}/proofs/verify", json={"proof": {}, "payload": {}})).status == 401


@pytest.mark.asyncio
async def test_http_auth_crud_inbound_verify_and_native_projection(home, tmp_path):
    remote_store = PeerStore(tmp_path / "remote")
    remote = remote_store.snapshot()["self"]
    local = PeerStore(home).snapshot()["self"]
    remote_store.put(local["peer_id"], record(local))
    app = web.Application(middlewares=[token_auth_middleware(port=8000)])
    register(app)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status == 403
        headers = {"Cookie": "gideon_token_8000=" + generate_token("dashboard:peers")}
        initial = await client.get(PREFIX, headers=headers)
        assert initial.status == 200
        assert initial.headers["Cache-Control"] == "no-store"
        created = await client.put(f"{PREFIX}/{remote['peer_id']}", headers=headers, json=record(remote))
        assert created.status == 200
        assert (await created.json())["revision"] == 1
        native = await create_provider().invoke("platform_peer_projection", {})
        assert native.success
        projected = json.loads(native.output)
        assert projected["peers"][0]["id"] == remote["peer_id"]
        assert "private_key" not in native.output
        proof = remote_store.create_proof(local["peer_id"], SCOPE)
        verified = await client.post(f"{PREFIX}/proofs/verify", headers=headers, json={"proof": proof, "payload": {"guest_id": "guest-1"}})
        assert verified.status == 200
        verified_body = await verified.json()
        assert verified_body["verified"] is True
        assert verified_body["peer"]["id"] == remote["peer_id"]
        assert verified_body["payload"] == {"guest_id": "guest-1"}
        replay = await client.post(f"{PREFIX}/proofs/verify", headers=headers, json={"proof": proof, "payload": {"guest_id": "guest-1"}})
        assert replay.status == 409
        invalid_envelope = await client.post(f"{PREFIX}/proofs/verify", headers=headers, json=proof)
        assert invalid_envelope.status == 400
        deleted = await client.delete(f"{PREFIX}/{remote['peer_id']}?revision=1", headers=headers)
        assert deleted.status == 200
        assert (await deleted.json())["peers"] == []
    invalid_native = await create_provider().invoke("platform_peer_projection", {"secret": True})
    assert not invalid_native.success


@pytest.mark.asyncio
async def test_http_rejects_app_sessions_bad_revisions_and_bad_records(home, tmp_path):
    remote = PeerStore(tmp_path / "remote").snapshot()["self"]
    app = web.Application()
    register(app)
    async with TestClient(TestServer(app)) as client:
        app_request = await client.get(PREFIX)
        assert app_request.status == 403
    store = PeerStore(home)
    store.put(remote["peer_id"], record(remote))
    with pytest.raises(PeerError, match="changed"):
        store.delete(remote["peer_id"], 0)
    assert store.get(remote["peer_id"])["revision"] == 1
