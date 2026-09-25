import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_peers import register as register_peers
from gideon.interfaces.dashboard.handlers.capabilities_remote_media import register as register_remote_media
from gideon.interfaces.dashboard.handlers.capabilities_replication import register as register_replication
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, use_ephemeral_secret
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.world_travel_http import register_world_travel
from gideon.workspace.capabilities.media.jobs import MediaJobs
from gideon.workspace.capabilities.media.sketches import SketchStore
from gideon.workspace.capabilities.platform.media_shares import MediaShares
from gideon.workspace.capabilities.platform.media_shares_http import register_media_shares
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.remote_media import RemoteMedia


SCOPES = [
    "experience.world_guest",
    "media.assets",
    "media.remote_execution",
    "workspace.records",
]


def _peer_record(identity):
    return {
        "label": identity["peer_id"][-8:],
        "endpoint": "https://peer.invalid",
        "public_key": identity["public_key"],
        "enabled": True,
        "send_categories": SCOPES,
        "receive_categories": SCOPES,
        "revision": 0,
    }


def _pair(local_home, remote_home):
    local, remote = PeerStore(local_home), PeerStore(remote_home)
    local_identity = local.snapshot()["self"]
    remote_identity = remote.snapshot()["self"]
    local.put(remote_identity["peer_id"], _peer_record(remote_identity))
    remote.put(local_identity["peer_id"], _peer_record(local_identity))
    return local, remote, local_identity


def _media_jobs(home):
    artifacts = NativeArtifactProvider(home / "artifacts")
    sketches = SketchStore(home / "capabilities/media/sketches.sqlite3", artifacts)
    return MediaJobs(home / "capabilities/media/jobs.sqlite3", sketches)


@pytest.mark.asyncio
async def test_exact_federation_routes_use_real_handler_auth_and_owner_routes_keep_token_auth(
    tmp_path, monkeypatch
):
    local_home, remote_home = tmp_path / "local", tmp_path / "remote"
    monkeypatch.setenv("GIDEON_HOME", str(local_home))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    local, remote, local_identity = _pair(local_home, remote_home)

    artifacts = NativeArtifactProvider(local_home / "artifacts")
    app = web.Application(middlewares=[token_auth_middleware(port=8000)])
    register_peers(app)
    register_replication(app)
    register_media_shares(app, MediaShares(local_home, local, artifacts), local)
    register_remote_media(app, RemoteMedia(_media_jobs(local_home), local))
    register_world_travel(app, ExperienceStore(local_home), local)

    async with TestClient(TestServer(app)) as client:
        owner_routes = [
            "/api/capabilities/platform/peers",
            "/api/capabilities/platform/replication",
            "/api/capabilities/platform/media-shares",
            "/api/capabilities/platform/remote-media",
            "/api/capabilities/experience/world-travel/destinations",
        ]
        for path in owner_routes:
            assert (await client.get(path)).status == 403

        proof = remote.create_proof(local_identity["peer_id"], "experience.world_guest")
        verified = await client.post(
            "/api/capabilities/platform/peers/proofs/verify",
            json={"proof": proof, "payload": {"probe": True}},
        )
        assert verified.status == 200
        assert (await verified.json())["peer"]["id"] == remote.snapshot()["self"]["peer_id"]
        replay = await client.post(
            "/api/capabilities/platform/peers/proofs/verify",
            json={"proof": proof, "payload": {"probe": True}},
        )
        assert replay.status == 409

        forged = remote.create_proof(local_identity["peer_id"], "experience.world_guest")
        forged["signature"] = ("A" if forged["signature"][0] != "A" else "B") + forged["signature"][1:]
        rejected = await client.post(
            "/api/capabilities/platform/peers/proofs/verify",
            json={"proof": forged, "payload": {}},
        )
        assert rejected.status == 401

        handler_routes = [
            ("/api/capabilities/platform/replication/receive", "workspace.records"),
            ("/api/capabilities/platform/media-shares/receive", "media.assets"),
            ("/api/capabilities/platform/remote-media/federation/jobs", "media.remote_execution"),
            ("/api/capabilities/experience/world-travel/federation/admit", "experience.world_guest"),
        ]
        for path, scope in handler_routes:
            signed = remote.create_proof(local_identity["peer_id"], scope)
            response = await client.post(path, json={"proof": signed, "payload": {}})
            assert response.status == 400, (path, response.status, await response.text())
            assert "Token required" not in await response.text()
            missing = await client.post(path, json={"payload": {}})
            assert missing.status == 400

        ticket = "x" * 43
        guest = "/api/capabilities/experience/world-travel/guest/" + ticket
        assert (await client.get(guest)).status == 404
        assert (await client.post(guest + "/leave", json={"revision": 1, "request_id": "leave-1"})).status == 404
        assert (await client.get(guest + "/host/gideon/worlds/lounge")).status == 404

        assert (await client.get("/api/capabilities/platform/peers/proofs/verify")).status == 403
        assert (await client.get("/api/capabilities/platform/remote-media/federation/jobs")).status == 403

    assert json.loads((local.key_path).read_text())["private_key"]
    assert json.loads((remote.key_path).read_text())["private_key"]
