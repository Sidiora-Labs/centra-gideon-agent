import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_remote_media import register
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.jobs import MediaJobs
from gideon.workspace.capabilities.media.sketches import SketchStore
from gideon.workspace.capabilities.platform.peers import (
    CATEGORIES,
    PeerError,
    PeerStore,
)
from gideon.workspace.capabilities.platform.remote_media import (
    REMOTE_PATH,
    SCOPE,
    RemoteMedia,
    RemoteMediaError,
    create_remote_media,
)
from gideon.workspace.capabilities.platform.remote_media_tools import RemoteMediaTools


def jobs(home):
    artifacts = NativeArtifactProvider(home / "artifacts")
    sketches = SketchStore(home / "capabilities/media/sketches.sqlite3", artifacts)
    return MediaJobs(home / "capabilities/media/jobs.sqlite3", sketches)


def peer_record(identity, endpoint, send=(SCOPE,), receive=(SCOPE,)):
    return {
        "label": "Remote renderer",
        "endpoint": endpoint,
        "public_key": identity["public_key"],
        "enabled": True,
        "send_categories": list(send),
        "receive_categories": list(receive),
        "revision": 0,
    }


def connect(
    sender,
    receiver,
    sender_endpoint,
    receiver_endpoint,
    sender_send=(SCOPE,),
    receiver_receive=(SCOPE,),
):
    sender_identity = sender.snapshot()["self"]
    receiver_identity = receiver.snapshot()["self"]
    sender.put(
        receiver_identity["peer_id"],
        peer_record(receiver_identity, receiver_endpoint, send=sender_send),
    )
    receiver.put(
        sender_identity["peer_id"],
        peer_record(sender_identity, sender_endpoint, receive=receiver_receive),
    )
    return sender_identity, receiver_identity


def request(request_id="poster-1", prompt="A solar port"):
    return {
        "request_id": request_id,
        "operation": "image_generate",
        "input": {"prompt": prompt, "size": "1024x1024", "controls": {"seed": 4}},
    }


@pytest.mark.asyncio
async def test_real_two_runtime_admission_replay_status_and_cancel(tmp_path):
    sender_peers = PeerStore(tmp_path / "sender")
    receiver_peers = PeerStore(tmp_path / "receiver")
    receiver_jobs = jobs(tmp_path / "receiver")
    receiver = RemoteMedia(receiver_jobs, receiver_peers)
    app = web.Application()
    register(app, receiver)
    async with TestServer(app) as server:
        sender_identity, receiver_identity = connect(
            sender_peers,
            receiver_peers,
            "https://sender.invalid",
            str(server.make_url("/")),
        )
        sender = RemoteMedia(jobs(tmp_path / "sender"), sender_peers)
        admitted = await sender.dispatch(receiver_identity["peer_id"], request())
        assert admitted["peer_id"] == receiver_identity["peer_id"]
        assert admitted["status"] == "queued"
        assert admitted["remote_state_revision"] == 1
        assert admitted["remote_job_id"]
        assert receiver_jobs.get(admitted["remote_job_id"])["input"] == {
            "prompt": "A solar port",
            "size": "1024x1024",
            "controls": {"seed": 4},
            "selection": "",
        }
        assert receiver_jobs.list()["items"][0]["status"] == "queued"
        assert sender.list()["items"] == [admitted]
        assert sender.list()["peers"][0]["id"] == receiver_identity["peer_id"]

        replay = await sender.dispatch(receiver_identity["peer_id"], request())
        assert replay == admitted
        assert len(receiver_jobs.list()["items"]) == 1

        remote_replay = await sender_peers.post_signed(
            receiver_identity["peer_id"], SCOPE, REMOTE_PATH + "/jobs", request()
        )
        assert remote_replay["accepted"] is True
        assert remote_replay["executor_peer_id"] == receiver_identity["peer_id"]
        assert remote_replay["job"]["id"] == admitted["remote_job_id"]
        assert remote_replay["job"]["status"] == "queued"
        assert set(remote_replay["job"]) == {
            "id",
            "operation",
            "status",
            "attempt",
            "state_revision",
            "result",
            "error",
            "created_at",
            "updated_at",
        }

        refreshed = await sender.refresh(admitted["id"])
        assert refreshed["status"] == "queued"
        assert refreshed["remote_state_revision"] == 1
        cancelled = await sender.cancel(
            admitted["id"], refreshed["remote_state_revision"]
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["remote_state_revision"] == 2
        assert receiver_jobs.get(admitted["remote_job_id"])["status"] == "cancelled"
        assert (
            await sender.cancel(admitted["id"], cancelled["remote_state_revision"])
            == cancelled
        )
        reopened = RemoteMedia(
            jobs(tmp_path / "sender"), PeerStore(tmp_path / "sender")
        )
        assert reopened.get(admitted["id"]) == cancelled
        assert sender_identity["peer_id"]


@pytest.mark.asyncio
async def test_outbound_scope_denial_never_reaches_receiver(tmp_path):
    sender_peers = PeerStore(tmp_path / "sender")
    receiver_peers = PeerStore(tmp_path / "receiver")
    receiver_jobs = jobs(tmp_path / "receiver")
    calls = 0

    async def denied(request):
        nonlocal calls
        calls += 1
        return web.json_response({})

    app = web.Application()
    app.router.add_post(REMOTE_PATH + "/jobs", denied)
    async with TestServer(app) as server:
        _, receiver_identity = connect(
            sender_peers,
            receiver_peers,
            "https://sender.invalid",
            str(server.make_url("/")),
            sender_send=(),
        )
        service = RemoteMedia(jobs(tmp_path / "sender"), sender_peers)
        with pytest.raises(PeerError, match="outbound") as error:
            await service.dispatch(receiver_identity["peer_id"], request())
    assert error.value.status == 403
    assert calls == 0
    assert service.list()["items"] == []
    assert receiver_jobs.list()["items"] == []


@pytest.mark.asyncio
async def test_inbound_scope_denial_returns_forbidden_and_does_not_enqueue(tmp_path):
    sender_peers = PeerStore(tmp_path / "sender")
    receiver_peers = PeerStore(tmp_path / "receiver")
    receiver_jobs = jobs(tmp_path / "receiver")
    app = web.Application()
    register(app, RemoteMedia(receiver_jobs, receiver_peers))
    async with TestClient(TestServer(app)) as client:
        sender_identity, receiver_identity = connect(
            sender_peers,
            receiver_peers,
            "https://sender.invalid",
            "https://receiver.invalid",
            receiver_receive=(),
        )
        current = sender_peers.get(receiver_identity["peer_id"])
        proof = sender_peers.create_proof(receiver_identity["peer_id"], SCOPE)
        response = await client.post(
            REMOTE_PATH + "/jobs", json={"proof": proof, "payload": request()}
        )
        assert response.status == 403
        assert "inbound" in (await response.json())["error"]
    assert sender_identity["peer_id"]
    assert current["send_categories"] == [SCOPE]
    assert receiver_jobs.list()["items"] == []


@pytest.mark.asyncio
async def test_federation_rejects_missing_wrong_and_replayed_proofs(tmp_path):
    sender = PeerStore(tmp_path / "sender")
    receiver = PeerStore(tmp_path / "receiver")
    app = web.Application()
    receiver_jobs = jobs(tmp_path / "receiver")
    register(app, RemoteMedia(receiver_jobs, receiver))
    async with TestClient(TestServer(app)) as client:
        _, receiver_identity = connect(
            sender, receiver, "https://sender.invalid", "https://receiver.invalid"
        )
        missing = await client.post(REMOTE_PATH + "/jobs", json={"payload": request()})
        assert missing.status == 400
        wrong = sender.create_proof(receiver_identity["peer_id"], SCOPE)
        wrong["scope"] = "media.assets"
        wrong_scope = await client.post(
            REMOTE_PATH + "/jobs", json={"proof": wrong, "payload": request()}
        )
        assert wrong_scope.status == 403
        proof = sender.create_proof(receiver_identity["peer_id"], SCOPE)
        first = await client.post(
            REMOTE_PATH + "/jobs", json={"proof": proof, "payload": request()}
        )
        assert first.status == 202
        replay = await client.post(
            REMOTE_PATH + "/jobs", json={"proof": proof, "payload": request()}
        )
        assert replay.status == 409
    assert len(receiver_jobs.list()["items"]) == 1


@pytest.mark.parametrize(
    "body,message,status",
    [
        ({}, "requires", 400),
        (
            {"request_id": "x", "operation": "video_generate", "input": {}},
            "self-contained",
            400,
        ),
        (
            {
                "request_id": "x",
                "operation": "image_generate",
                "input": {"prompt": "ok", "source_artifact_id": "a"},
            },
            "only",
            400,
        ),
        (
            {
                "request_id": "x" * 41,
                "operation": "image_generate",
                "input": {"prompt": "ok"},
            },
            "40",
            400,
        ),
        (
            {
                "request_id": "x",
                "operation": "image_generate",
                "input": {"prompt": "x" * 17000},
            },
            "16 KiB",
            413,
        ),
    ],
)
def test_request_bounds_reject_before_canonical_queue(tmp_path, body, message, status):
    service = RemoteMedia(jobs(tmp_path), PeerStore(tmp_path))
    with pytest.raises(RemoteMediaError, match=message) as error:
        service.accept({"id": "peer-" + "0" * 32}, body)
    assert error.value.status == status
    assert service.jobs.list()["items"] == []


def test_canonical_media_validation_is_retained(tmp_path):
    service = RemoteMedia(jobs(tmp_path), PeerStore(tmp_path))
    peer = {"id": "peer-" + "1" * 32}
    with pytest.raises(RemoteMediaError, match="Prompt"):
        service.accept(peer, request(prompt=""))
    with pytest.raises(RemoteMediaError, match="finite"):
        service.accept(
            peer,
            {
                "request_id": "nan",
                "operation": "image_generate",
                "input": {"prompt": "x", "controls": {"guidance": float("nan")}},
            },
        )
    assert service.jobs.list()["items"] == []


@pytest.mark.asyncio
async def test_request_id_conflict_is_local_and_durable(tmp_path):
    class Transport:
        def snapshot(self):
            return {"peers": []}

        async def post_signed(self, peer_id, scope, path, payload):
            job = {
                "id": "job-1",
                "operation": "image_generate",
                "status": "queued",
                "attempt": 0,
                "state_revision": 1,
                "result": None,
                "error": None,
                "created_at": "now",
                "updated_at": "now",
            }
            return {"accepted": True, "executor_peer_id": peer_id, "job": job}

    service = RemoteMedia(jobs(tmp_path), Transport())
    first = await service.dispatch("peer-a", request())
    assert (await service.dispatch("peer-a", request())) == first
    with pytest.raises(RemoteMediaError, match="conflicts") as error:
        await service.dispatch("peer-a", request(prompt="changed"))
    assert error.value.status == 409


def test_status_and_cancel_require_exact_payload_and_cas(tmp_path):
    store = jobs(tmp_path)
    service = RemoteMedia(store, PeerStore(tmp_path))
    peer = {"id": "peer-" + "2" * 32}
    remote = service.accept(peer, request())["job"]
    assert service.status(peer, {"job_id": remote["id"]})["job"]["status"] == "queued"
    with pytest.raises(RemoteMediaError, match="job ID"):
        service.status(peer, {"job_id": remote["id"], "extra": True})
    with pytest.raises(RemoteMediaError, match="requires"):
        service.cancel_remote(peer, {"job_id": remote["id"]})
    with pytest.raises(RemoteMediaError, match="changed") as stale:
        service.cancel_remote(peer, {"job_id": remote["id"], "state_revision": 99})
    assert stale.value.status == 409
    cancelled = service.cancel_remote(
        peer, {"job_id": remote["id"], "state_revision": 1}
    )["job"]
    assert cancelled["status"] == "cancelled"


def test_peer_cannot_read_or_cancel_another_peers_admission(tmp_path):
    service = RemoteMedia(jobs(tmp_path), PeerStore(tmp_path))
    owner = {"id": "peer-" + "4" * 32}
    stranger = {"id": "peer-" + "5" * 32}
    remote = service.accept(owner, request())["job"]
    with pytest.raises(RemoteMediaError, match="does not own") as read_denied:
        service.status(stranger, {"job_id": remote["id"]})
    assert read_denied.value.status == 403
    with pytest.raises(RemoteMediaError, match="does not own") as cancel_denied:
        service.cancel_remote(stranger, {"job_id": remote["id"], "state_revision": 1})
    assert cancel_denied.value.status == 403
    assert service.jobs.get(remote["id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_signed_federation_routes_bind_job_authority_to_admitting_peer(tmp_path):
    receiver_store = PeerStore(tmp_path / "receiver")
    receiver_identity = receiver_store.snapshot()["self"]
    first_store = PeerStore(tmp_path / "first")
    second_store = PeerStore(tmp_path / "second")
    for number, sender in enumerate((first_store, second_store), 1):
        sender_identity = sender.snapshot()["self"]
        sender.put(
            receiver_identity["peer_id"],
            peer_record(receiver_identity, "https://receiver.invalid"),
        )
        receiver_store.put(
            sender_identity["peer_id"],
            peer_record(sender_identity, f"https://sender-{number}.invalid"),
        )
    receiver_jobs = jobs(tmp_path / "receiver")
    app = web.Application()
    register(app, RemoteMedia(receiver_jobs, receiver_store))
    async with TestClient(TestServer(app)) as client:
        admitted_proof = first_store.create_proof(receiver_identity["peer_id"], SCOPE)
        admitted_response = await client.post(
            REMOTE_PATH + "/jobs", json={"proof": admitted_proof, "payload": request()}
        )
        assert admitted_response.status == 202
        remote_job = (await admitted_response.json())["job"]

        foreign_status_proof = second_store.create_proof(
            receiver_identity["peer_id"], SCOPE
        )
        foreign_status = await client.post(
            REMOTE_PATH + "/status",
            json={
                "proof": foreign_status_proof,
                "payload": {"job_id": remote_job["id"]},
            },
        )
        assert foreign_status.status == 403
        assert "does not own" in (await foreign_status.json())["error"]

        foreign_cancel_proof = second_store.create_proof(
            receiver_identity["peer_id"], SCOPE
        )
        foreign_cancel = await client.post(
            REMOTE_PATH + "/cancel",
            json={
                "proof": foreign_cancel_proof,
                "payload": {"job_id": remote_job["id"], "state_revision": 1},
            },
        )
        assert foreign_cancel.status == 403
        assert "does not own" in (await foreign_cancel.json())["error"]

        owner_status_proof = first_store.create_proof(
            receiver_identity["peer_id"], SCOPE
        )
        owner_status = await client.post(
            REMOTE_PATH + "/status",
            json={"proof": owner_status_proof, "payload": {"job_id": remote_job["id"]}},
        )
        assert owner_status.status == 200
        assert (await owner_status.json())["job"]["status"] == "queued"
    assert receiver_jobs.get(remote_job["id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_owner_routes_require_dashboard_auth(tmp_path):
    app = web.Application()
    register(app, RemoteMedia(jobs(tmp_path), PeerStore(tmp_path)))
    async with TestClient(TestServer(app)) as client:
        assert (
            await client.get("/api/capabilities/platform/remote-media")
        ).status == 403
        assert (
            await client.post("/api/capabilities/platform/remote-media", json={})
        ).status == 403


@pytest.mark.asyncio
async def test_native_tools_share_service_and_require_approval_for_mutations(tmp_path):
    service = RemoteMedia(jobs(tmp_path), PeerStore(tmp_path))
    tools = RemoteMediaTools(service)
    definitions = {tool.name: tool for tool in await tools.list_tools()}
    assert definitions["remote_media_list"].requires_approval is False
    assert definitions["remote_media_dispatch"].requires_approval is True
    assert definitions["remote_media_cancel"].requires_approval is True
    listed = await tools.invoke("remote_media_list", {})
    assert listed.success is True
    assert json.loads(listed.output) == {"items": [], "peers": []}
    invalid = await tools.invoke("remote_media_dispatch", {"peer_id": "x"})
    assert invalid.success is False
    unknown = await tools.invoke("missing", {})
    assert unknown.success is False


def test_scope_is_discoverable_in_peer_projection(tmp_path):
    projection = PeerStore(tmp_path).snapshot()
    assert SCOPE in CATEGORIES
    assert SCOPE in projection["categories"]
    assert "private_key" not in json.dumps(projection)


def test_completed_result_is_bounded_to_artifact_reference(tmp_path):
    store = jobs(tmp_path)
    service = RemoteMedia(store, PeerStore(tmp_path))
    peer = {"id": "peer-" + "3" * 32}
    admitted = service.accept(peer, request())["job"]
    claimed = store.claim()
    assert claimed["id"] == admitted["id"]
    store.finish(
        claimed["id"],
        result={
            "artifact_id": "artifact-1",
            "version": 7,
            "mime": "image/png",
            "width": 1024,
            "height": 1024,
            "provider_secret": "must-not-cross",
            "local_path": "/private/output.png",
        },
    )
    projected = service.status(peer, {"job_id": admitted["id"]})["job"]
    assert projected["status"] == "succeeded"
    assert projected["result"] == {
        "artifact_id": "artifact-1",
        "version": 7,
        "mime": "image/png",
        "width": 1024,
        "height": 1024,
    }
    assert "provider_secret" not in json.dumps(projected)
    assert "local_path" not in json.dumps(projected)
    assert "events" not in projected
    assert "input" not in projected


def test_only_enabled_send_authorized_peers_are_projected(tmp_path):
    local = PeerStore(tmp_path / "local")
    allowed = PeerStore(tmp_path / "allowed").snapshot()["self"]
    wrong_scope = PeerStore(tmp_path / "wrong").snapshot()["self"]
    disabled = PeerStore(tmp_path / "disabled").snapshot()["self"]
    local.put(allowed["peer_id"], peer_record(allowed, "https://allowed.example"))
    local.put(
        wrong_scope["peer_id"],
        peer_record(wrong_scope, "https://wrong.example", send=("media.assets",)),
    )
    row = peer_record(disabled, "https://disabled.example")
    row["enabled"] = False
    local.put(disabled["peer_id"], row)
    projected = RemoteMedia(jobs(tmp_path / "local"), local).list()
    assert [peer["id"] for peer in projected["peers"]] == [allowed["peer_id"]]
    assert projected["items"] == []
    assert "private_key" not in json.dumps(projected)


def test_receiver_request_namespace_separates_peer_identities(tmp_path):
    service = RemoteMedia(jobs(tmp_path), PeerStore(tmp_path))
    first = service.accept({"id": "peer-" + "a" * 32}, request())["job"]
    second = service.accept({"id": "peer-" + "b" * 32}, request())["job"]
    assert first["id"] != second["id"]
    assert len(service.jobs.list()["items"]) == 2
    assert service.accept({"id": "peer-" + "a" * 32}, request())["job"] == first
    assert service.accept({"id": "peer-" + "b" * 32}, request())["job"] == second


def test_factory_reopens_same_canonical_jobs_database(tmp_path):
    first = create_remote_media(tmp_path)
    admitted = first.accept({"id": "peer-" + "c" * 32}, request())["job"]
    second = create_remote_media(tmp_path)
    assert second.jobs.path == tmp_path / "capabilities/media/jobs.sqlite3"
    assert second.jobs.get(admitted["id"])["status"] == "queued"
    assert second.accept({"id": "peer-" + "c" * 32}, request())["job"] == admitted
    assert len(second.jobs.list()["items"]) == 1


@pytest.mark.asyncio
async def test_federation_envelope_size_is_bounded_before_proof_work(tmp_path):
    receiver = PeerStore(tmp_path / "receiver")
    service = RemoteMedia(jobs(tmp_path / "receiver"), receiver)
    app = web.Application(client_max_size=128 * 1024)
    register(app, service)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            REMOTE_PATH + "/jobs",
            json={"proof": {}, "payload": {"padding": "x" * (33 * 1024)}},
        )
        assert response.status == 413
        assert "32 KiB" in (await response.json())["error"]
    assert service.jobs.list()["items"] == []
    assert receiver.snapshot()["peers"] == []
