import base64
import hashlib
import io

import pytest
from PIL import Image
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.platform.media_share_tools import MediaShareTools
from gideon.workspace.capabilities.platform.media_shares import MAX_BYTES, SCOPE, MediaShareError, MediaShares
from gideon.workspace.capabilities.platform.media_shares_http import register_media_shares
from gideon.workspace.capabilities.platform.peers import PeerStore


def png(color=(20, 80, 140)):
    target = io.BytesIO(); Image.new("RGB", (2, 2), color).save(target, "PNG"); return target.getvalue()


def identity(store): return store.snapshot()["self"]


def add_peer(store, remote, endpoint, *, send=(), receive=(), enabled=True):
    return store.put(remote["peer_id"], {"label": "Remote", "endpoint": endpoint, "public_key": remote["public_key"], "enabled": enabled, "send_categories": list(send), "receive_categories": list(receive), "revision": 0})


@pytest.fixture
def pair(tmp_path):
    sender_home, receiver_home = tmp_path / "sender", tmp_path / "receiver"
    sender_peers, receiver_peers = PeerStore(sender_home), PeerStore(receiver_home)
    sender_artifacts = NativeArtifactProvider(sender_home / "artifacts")
    receiver_artifacts = NativeArtifactProvider(receiver_home / "artifacts")
    sender = MediaShares(sender_home, sender_peers, sender_artifacts)
    receiver = MediaShares(receiver_home, receiver_peers, receiver_artifacts)
    sender_identity, receiver_identity = identity(sender_peers), identity(receiver_peers)
    add_peer(receiver_peers, sender_identity, "http://127.0.0.1:1", receive=[SCOPE])
    return {"sender_home": sender_home, "receiver_home": receiver_home, "sender_peers": sender_peers, "receiver_peers": receiver_peers, "sender_artifacts": sender_artifacts, "receiver_artifacts": receiver_artifacts, "sender": sender, "receiver": receiver, "sender_identity": sender_identity, "receiver_identity": receiver_identity}


def offer(data=None, **patch):
    data = data or png()
    artifact = {"source_id": "image-one", "source_version": 1, "name": "Shared image", "kind": "image", "mime": "image/png", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "data": base64.b64encode(data).decode()}
    artifact.update(patch)
    return {"operation": "offer", "share_id": "a" * 32, "artifact": artifact}


def test_receive_imports_exact_canonical_bytes_and_minimal_receipt(pair):
    data = png()
    result = pair["receiver"].receive(pair["sender_identity"]["peer_id"], offer(data))
    assert result["share_id"] == "a" * 32
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["status"] == "active"
    artifact = pair["receiver_artifacts"].get(result["artifact_id"], version=1)
    assert artifact.kind == "image"
    assert artifact.source == "import"
    assert artifact.tags == ["peer-share", "image"]
    assert pair["receiver_artifacts"].raw_bytes(result["artifact_id"], version=1) == (data, "image/png")
    snapshot = pair["receiver"].list()
    assert snapshot["scope"] == "media.assets"
    assert snapshot["max_bytes"] == MAX_BYTES
    assert snapshot["outbound"] == []
    assert set(snapshot["inbound"][0]) == {"sender", "share_id", "fingerprint", "artifact_id", "sha256", "status", "received_at", "updated_at"}


def test_receive_replays_same_offer_and_rejects_changed_content(pair):
    sender = pair["sender_identity"]["peer_id"]
    first = pair["receiver"].receive(sender, offer())
    assert pair["receiver"].receive(sender, offer()) == first
    assert len(pair["receiver_artifacts"].list()) == 1
    with pytest.raises(MediaShareError, match="replay changed content"):
        pair["receiver"].receive(sender, offer(png((1, 2, 3))))
    assert len(pair["receiver_artifacts"].list()) == 1


@pytest.mark.parametrize("patch,message", [
    ({"mime": "application/octet-stream"}, "metadata"),
    ({"kind": "audio"}, "metadata"),
    ({"bytes": 0}, "size or hash"),
    ({"bytes": MAX_BYTES + 1}, "size or hash"),
    ({"sha256": "x" * 64}, "size or hash"),
    ({"source_version": 0}, "source identity"),
    ({"source_id": "../escape"}, "source identity"),
    ({"name": ""}, "metadata"),
])
def test_receive_rejects_invalid_metadata_before_artifact_write(pair, patch, message):
    with pytest.raises(MediaShareError, match=message): pair["receiver"].receive(pair["sender_identity"]["peer_id"], offer(**patch))
    assert pair["receiver_artifacts"].list() == []
    assert pair["receiver"].list()["inbound"] == []


def test_receive_rejects_encoding_size_and_hash_mismatch(pair):
    sender = pair["sender_identity"]["peer_id"]
    bad = offer(); bad["artifact"]["data"] = "***"
    with pytest.raises(MediaShareError, match="encoding"): pair["receiver"].receive(sender, bad)
    bad = offer(); bad["artifact"]["bytes"] += 1
    with pytest.raises(MediaShareError, match="bytes do not match"): pair["receiver"].receive(sender, bad)
    bad = offer(); bad["artifact"]["sha256"] = "0" * 64
    with pytest.raises(MediaShareError, match="bytes do not match"): pair["receiver"].receive(sender, bad)


def test_receive_requires_exact_opt_in_policy(pair):
    stranger = PeerStore(pair["receiver_home"] / "stranger").snapshot()["self"]
    with pytest.raises(MediaShareError, match="denies inbound") as denied:
        pair["receiver"].receive(stranger["peer_id"], offer())
    assert denied.value.status == 403
    peer = pair["receiver_peers"].get(pair["sender_identity"]["peer_id"])
    pair["receiver_peers"].put(peer["id"], {**{key: peer[key] for key in ("label", "endpoint", "public_key", "enabled", "send_categories", "receive_categories", "revision")}, "receive_categories": []})
    with pytest.raises(MediaShareError, match="denies inbound"): pair["receiver"].receive(pair["sender_identity"]["peer_id"], offer())


def test_receive_revocation_deletes_only_shared_artifact_and_blocks_replay(pair):
    sender = pair["sender_identity"]["peer_id"]
    original = pair["receiver_artifacts"].create_binary(name="Local", slug="local-image", data=png((5, 5, 5)), mime="image/png", kind="image")
    active = pair["receiver"].receive(sender, offer())
    result = pair["receiver"].receive(sender, {"operation": "revoke", "share_id": "a" * 32, "sha256": active["sha256"]})
    assert result == {"share_id": "a" * 32, "status": "revoked"}
    assert pair["receiver_artifacts"].get(active["artifact_id"]) is None
    assert pair["receiver_artifacts"].get(original.slug) is not None
    assert pair["receiver"].receive(sender, {"operation": "revoke", "share_id": "a" * 32, "sha256": active["sha256"]}) == result
    with pytest.raises(MediaShareError, match="cannot be replayed"): pair["receiver"].receive(sender, offer())


def test_receive_revoke_rejects_unknown_or_wrong_hash(pair):
    sender = pair["sender_identity"]["peer_id"]
    with pytest.raises(MediaShareError, match="not found"): pair["receiver"].receive(sender, {"operation": "revoke", "share_id": "a" * 32, "sha256": "0" * 64})
    active = pair["receiver"].receive(sender, offer())
    with pytest.raises(MediaShareError, match="hash does not match"): pair["receiver"].receive(sender, {"operation": "revoke", "share_id": "a" * 32, "sha256": "0" * 64})
    assert pair["receiver_artifacts"].get(active["artifact_id"]) is not None


@pytest.mark.asyncio
async def test_two_real_runtimes_share_verify_replay_and_revoke_over_guarded_http(pair):
    @web.middleware
    async def authenticated(request, handler): request["user"] = "operator"; return await handler(request)
    app = web.Application(middlewares=[authenticated])
    register_media_shares(app, pair["receiver"], pair["receiver_peers"])
    async with TestServer(app) as server:
        add_peer(pair["sender_peers"], pair["receiver_identity"], str(server.make_url("")), send=[SCOPE])
        data = png((200, 40, 60))
        artifact = pair["sender_artifacts"].create_binary(name="Exact red image", slug="red-image", data=data, mime="image/png", kind="image")
        body = {"request_id": "share-red-v1", "peer_id": pair["receiver_identity"]["peer_id"], "artifact_id": artifact.slug, "artifact_version": 1}
        shared = await pair["sender"].share(body)
        assert shared["status"] == "active"
        assert shared["artifact_id"] == "red-image"
        assert shared["artifact_version"] == 1
        assert shared["sha256"] == hashlib.sha256(data).hexdigest()
        assert shared["bytes"] == len(data)
        remote = pair["receiver_artifacts"].raw_bytes(shared["remote_artifact_id"], version=1)
        assert remote == (data, "image/png")
        assert await pair["sender"].share(body) == shared
        assert len(pair["receiver"].list()["inbound"]) == 1
        revoked = await pair["sender"].revoke(shared["id"], shared["revision"])
        assert revoked["status"] == "revoked"
        assert revoked["revision"] == 2
        assert pair["receiver_artifacts"].get(shared["remote_artifact_id"]) is None
        assert pair["receiver"].list()["inbound"][0]["status"] == "revoked"


def test_outbound_source_requires_exact_version_kind_size_and_send_policy(pair):
    data = png()
    image = pair["sender_artifacts"].create_binary(name="Image", slug="image", data=data, mime="image/png", kind="image")
    body = {"request_id": "one", "peer_id": pair["receiver_identity"]["peer_id"], "artifact_id": image.slug, "artifact_version": 1}
    with pytest.raises(MediaShareError, match="denies media sharing"): pair["sender"].source(body)
    add_peer(pair["sender_peers"], pair["receiver_identity"], "http://127.0.0.1:1", send=[SCOPE])
    artifact, found, mime = pair["sender"].source(body)
    assert artifact.slug == image.slug and found == data and mime == "image/png"
    with pytest.raises(MediaShareError, match="not found"): pair["sender"].source({**body, "artifact_version": 2})
    audio = pair["sender_artifacts"].create_binary(name="Audio", slug="audio", data=b"ID3audio", mime="audio/mpeg", kind="audio")
    with pytest.raises(MediaShareError, match="not found"): pair["sender"].source({**body, "artifact_id": audio.slug})


@pytest.mark.asyncio
async def test_http_dashboard_requires_auth_but_signed_receive_does_not(pair):
    app = web.Application()
    register_media_shares(app, pair["receiver"], pair["receiver_peers"])
    async with TestClient(TestServer(app)) as client:
        denied = await client.get("/api/capabilities/platform/media-shares")
        assert denied.status == 403
        malformed = await client.post("/api/capabilities/platform/media-shares/receive", json={"payload": {}})
        assert malformed.status == 400


@pytest.mark.asyncio
async def test_native_tools_expose_exact_approved_operations(pair):
    tools = MediaShareTools(pair["sender"])
    definitions = {item.name: item for item in await tools.list_tools()}
    assert set(definitions) == {"platform_media_shares_list", "platform_media_share", "platform_media_share_revoke"}
    assert definitions["platform_media_shares_list"].requires_approval is False
    assert definitions["platform_media_share"].requires_approval is True
    assert definitions["platform_media_share_revoke"].requires_approval is True
    listed = await tools.invoke("platform_media_shares_list", {})
    assert listed.success
    assert "outbound" in listed.output and "inbound" in listed.output
    unknown = await tools.invoke("other", {})
    assert unknown.success is False


def test_projection_contains_receipts_only_not_media_or_peer_secrets(pair):
    pair["receiver"].receive(pair["sender_identity"]["peer_id"], offer())
    encoded = str(pair["receiver"].list())
    assert "private_key" not in encoded
    assert "public_key" not in encoded
    assert "signature" not in encoded
    assert "nonce" not in encoded
    assert base64.b64encode(png()).decode() not in encoded
    assert "data" not in pair["receiver"].list()["inbound"][0]


def test_receiver_receipt_and_replay_survive_service_restart(pair):
    sender = pair["sender_identity"]["peer_id"]
    original = pair["receiver"].receive(sender, offer())
    reopened = MediaShares(pair["receiver_home"], pair["receiver_peers"], pair["receiver_artifacts"])
    assert reopened.receive(sender, offer()) == original
    assert len(reopened.list()["inbound"]) == 1
    changed = offer(png((90, 80, 70)))
    with pytest.raises(MediaShareError, match="replay changed content") as rejected:
        reopened.receive(sender, changed)
    assert rejected.value.status == 409


def test_receiver_accepts_supported_video_without_decoding_or_parallel_store(pair):
    data = b"\x00\x00\x00\x18ftypmp42" + b"media-payload"
    payload = offer(
        data,
        name="Exact clip",
        kind="video",
        mime="video/mp4",
    )
    received = pair["receiver"].receive(pair["sender_identity"]["peer_id"], payload)
    artifact = pair["receiver_artifacts"].get(received["artifact_id"], version=1)
    assert artifact.kind == "video"
    assert artifact.mime == "video/mp4"
    assert pair["receiver_artifacts"].raw_bytes(received["artifact_id"], version=1) == (data, "video/mp4")
    assert not (pair["receiver_home"] / "capabilities/platform/media").exists()


@pytest.mark.parametrize("body,message", [
    ({}, "Expected request_id"),
    ({"request_id": "bad space", "peer_id": "peer-one", "artifact_id": "image", "artifact_version": 1}, "request identifier"),
    ({"request_id": "one", "peer_id": "stranger", "artifact_id": "image", "artifact_version": 1}, "peer identifier"),
    ({"request_id": "one", "peer_id": "peer-one", "artifact_id": "../image", "artifact_version": 1}, "artifact identifier"),
    ({"request_id": "one", "peer_id": "peer-one", "artifact_id": "image", "artifact_version": True}, "artifact version"),
])
def test_outbound_selection_schema_is_exact(pair, body, message):
    with pytest.raises(MediaShareError, match=message):
        pair["sender"].source(body)


@pytest.mark.asyncio
async def test_sender_rejects_stale_revision_without_transport(pair):
    with pair["sender"].db() as db:
        stamp = 1.0
        db.execute(
            "INSERT INTO outbound VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("b" * 32, "stable-request", pair["receiver_identity"]["peer_id"], "image", 1, "0" * 64, 10, "image/png", "image", "active", 3, "remote", "", stamp, stamp),
        )
    with pytest.raises(MediaShareError, match="revision changed") as rejected:
        await pair["sender"].revoke("b" * 32, 2)
    assert rejected.value.status == 409
    assert pair["sender"].get("b" * 32)["status"] == "active"
