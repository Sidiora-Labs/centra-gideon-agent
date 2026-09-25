import copy
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.triggers.store import TriggerStore
from gideon.interfaces.dashboard.handlers.capabilities_creative_peer_feedback import (
    register as register_peer_feedback,
)
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.workspace.capabilities.creative.commissions import CommissionStore
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.peer_feedback import (
    RECEIVE_PATH,
    SCOPE,
    PeerFeedbackStore,
    _hash,
)
from gideon.workspace.capabilities.creative.store import CatalogError
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform.peers import PeerError, PeerStore


async def owner_home(home):
    works = WorkStore(home)
    work = works.create(
        {
            "request_id": "peer-work",
            "title": "Peer feedback source",
            "kind": "work",
            "prompt": "A quiet station.",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    drafted = works.draft(
        work["id"],
        {
            "request_id": "peer-draft",
            "revision": 1,
            "text": "A lantern moves across the empty station.",
            "note": "Pinned source",
        },
    )
    direction = DirectionStore(home)
    store = CommissionStore(
        home, direction=direction, triggers=TriggerStore(base_dir=home)
    )
    commission = store.create(
        {
            "request_id": "peer-commission",
            "name": "Shared taste",
            "target_ability": "image",
            "brief": {
                "intent": "Develop a quiet visual treatment.",
                "genre": "mystery",
                "category": "treatment",
                "style": "restrained",
                "constraints": {"rating": "PG"},
                "seed_refs": [],
            },
            "cadence": {"kind": "interval", "seconds": 900},
            "sources": [
                {
                    "kind": "work",
                    "id": work["id"],
                    "revision": drafted["work"]["revision"],
                }
            ],
            "steps": [
                {
                    "id": "verify",
                    "title": "Verify source",
                    "operation": "source.verify",
                    "depends_on": [],
                },
                {
                    "id": "snapshot",
                    "title": "Save treatment",
                    "operation": "treatment.snapshot",
                    "depends_on": ["verify"],
                },
            ],
            "enabled": True,
            "max_attempts": 2,
        }
    )
    run = await store.execute(commission["id"], "manual:peer", trigger="manual")
    output = {
        key: run["outputs"][0][key]
        for key in ("artifact_id", "artifact_version", "content_hash")
    }
    return store, direction, commission, run, output


def connect_peer(store, remote, endpoint, send=(), receive=()):
    identity = remote.snapshot()["self"]
    return store.put(
        identity["peer_id"],
        {
            "label": "isolated peer",
            "endpoint": endpoint,
            "public_key": identity["public_key"],
            "enabled": True,
            "send_categories": list(send),
            "receive_categories": list(receive),
            "revision": 0,
        },
    )


def target_approval(peer_id, target, decision="approved"):
    return {"decision": decision, "peer_id": peer_id, "target_hash": _hash(target)}


def reaction_body(
    revision=None, author="remote-owner", rating="liked", note="Keep the stillness."
):
    body = {"author": author, "rating": rating, "note": note, "tags": ["pace", "tone"]}
    if revision is not None:
        body["revision"] = revision
    return body


def read_feedback(store, reaction_id):
    with store.db() as db:
        row = db.execute(
            "SELECT record FROM creative_commission_feedback WHERE id=?", (reaction_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def receive_app(adapter, calls):
    app = web.Application()

    async def receive(request):
        calls.append(1)
        try:
            return web.json_response(adapter.receive_signed(await request.json()))
        except CatalogError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)

    app.router.add_post(RECEIVE_PATH, receive)
    return app


async def connected_pair(tmp_path):
    owner, remote = tmp_path / "owner", tmp_path / "remote"
    store, direction, commission, run, output = await owner_home(owner)
    owner_peers, remote_peers = PeerStore(owner), PeerStore(remote)
    owner_adapter = PeerFeedbackStore(
        owner, commissions=store, direction=direction, peers=owner_peers
    )
    target = owner_adapter.target(commission["id"], run["id"], output)
    return (
        owner,
        remote,
        store,
        direction,
        commission,
        run,
        output,
        target,
        owner_peers,
        remote_peers,
        owner_adapter,
    )


async def test_real_two_home_remote_author_signed_transport_rerating_tombstone_restart(
    tmp_path,
):
    (
        owner,
        remote,
        store,
        _,
        _,
        _,
        _,
        target,
        owner_peers,
        remote_peers,
        owner_adapter,
    ) = await connected_pair(tmp_path)
    calls = []
    async with TestClient(TestServer(receive_app(owner_adapter, calls))) as client:
        connect_peer(remote_peers, owner_peers, str(client.make_url("/")), send=[SCOPE])
        connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
        remote_adapter = PeerFeedbackStore(remote, peers=remote_peers)
        owner_id = owner_peers.snapshot()["self"]["peer_id"]
        first_payload = remote_adapter.prepare(
            owner_id, target, reaction_body(), target_approval(owner_id, target)
        )
        first = await remote_adapter.push_prepared(owner_id, first_payload)
        assert first["state"] == "applied"
        reaction_id = first["reaction_id"]
        assert read_feedback(store, reaction_id)["author"] == "remote-owner"
        assert read_feedback(store, reaction_id)["output"] == target["output"]
        assert await remote_adapter.push_prepared(owner_id, first_payload) == first
        assert len(calls) == 1

        second_payload = remote_adapter.prepare(
            owner_id,
            target,
            reaction_body(revision=1, rating="disliked", note="Increase movement."),
            target_approval(owner_id, target),
        )
        second = await remote_adapter.push_prepared(owner_id, second_payload)
        assert second["revision"] == 2
        assert read_feedback(store, reaction_id)["note"] == "Increase movement."

        tombstone = remote_adapter.revoke_prepared(
            owner_id, reaction_id, 2, target_approval(owner_id, target)
        )
        third = await remote_adapter.push_prepared(owner_id, tombstone)
        assert third["revision"] == 3
        assert read_feedback(store, reaction_id)["deleted"] is True

        restarted = PeerFeedbackStore(
            owner,
            commissions=CommissionStore(owner),
            direction=DirectionStore(owner),
            peers=PeerStore(owner),
        )
        new_proof_result = await remote_peers.post_signed(
            owner_id, SCOPE, RECEIVE_PATH, tombstone
        )
        assert new_proof_result == third
        assert restarted.path.exists()


async def test_target_is_resolved_from_owner_records_and_wire_excludes_private_state(
    tmp_path,
):
    *_, target, owner_peers, remote_peers, owner_adapter = await connected_pair(
        tmp_path
    )
    remote = tmp_path / "remote"
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    payload = PeerFeedbackStore(remote, peers=remote_peers).prepare(
        owner_id, target, reaction_body(), target_approval(owner_id, target)
    )
    assert set(payload) == {
        "schema_version",
        "delivery_id",
        "reaction",
        "lineage",
        "binding",
    }
    assert payload["lineage"] == target
    assert payload["binding"]["payload_hash"] == _hash(
        {k: v for k, v in payload.items() if k != "binding"}
    )
    assert payload["binding"]["recipient"] == owner_id
    assert payload["binding"]["scope"] == SCOPE
    serialized = json.dumps(payload, sort_keys=True)
    for private in (
        "cadence",
        "brief",
        "schedule_revision",
        "dispatch",
        "attempts",
        "treatment",
    ):
        assert f'"{private}"' not in serialized
    assert (
        owner_adapter.target(
            target["commission_id"], target["run_id"], target["output"]
        )
        == target
    )


async def test_approval_and_directional_policy_fail_before_outbox_write(tmp_path):
    *_, target, owner_peers, remote_peers, _ = await connected_pair(tmp_path)
    remote = tmp_path / "remote"
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9")
    adapter = PeerFeedbackStore(remote, peers=remote_peers)
    with pytest.raises(CatalogError, match="approval") as denied:
        adapter.prepare(
            owner_id,
            target,
            reaction_body(),
            target_approval(owner_id, target, "denied"),
        )
    assert denied.value.status == 403
    with pytest.raises(CatalogError, match="policy") as policy:
        adapter.prepare(
            owner_id, target, reaction_body(), target_approval(owner_id, target)
        )
    assert policy.value.status == 403
    with adapter.db() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM creative_feedback_outbox").fetchone()[0]
            == 0
        )


async def test_payload_signature_binds_exact_feedback_and_all_lineage_pins(tmp_path):
    (
        owner,
        remote,
        store,
        direction,
        _,
        _,
        _,
        target,
        owner_peers,
        remote_peers,
        owner_adapter,
    ) = await connected_pair(tmp_path)
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
    payload = PeerFeedbackStore(remote, peers=remote_peers).prepare(
        owner_id, target, reaction_body(), target_approval(owner_id, target)
    )
    for field, mutate in (
        ("reaction", lambda p: p["reaction"].__setitem__("note", "tampered")),
        (
            "output",
            lambda p: p["lineage"]["output"].__setitem__("content_hash", "0" * 64),
        ),
        ("project", lambda p: p["lineage"]["project"].__setitem__("revision", 999)),
        ("source", lambda p: p["lineage"]["sources"][0].__setitem__("revision", 999)),
    ):
        bad = copy.deepcopy(payload)
        mutate(bad)
        with pytest.raises(CatalogError, match="binding") as rejected:
            owner_adapter.receive(remote_id, bad)
        assert rejected.value.status == 401, field
    assert read_feedback(store, payload["reaction"]["id"]) is None
    assert (
        direction.get(target["project"]["id"])["revision"]
        == target["project"]["revision"]
    )


async def test_signed_scope_nonce_and_signature_guards_precede_merge(tmp_path):
    _, remote, store, _, _, _, _, target, owner_peers, remote_peers, owner_adapter = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
    payload = PeerFeedbackStore(remote, peers=remote_peers).prepare(
        owner_id, target, reaction_body(), target_approval(owner_id, target)
    )
    proof = remote_peers.create_proof(owner_id, SCOPE)
    with pytest.raises(CatalogError, match="scope"):
        owner_adapter.receive_signed(
            {"proof": {"scope": "creative.catalog"}, "payload": payload}
        )
    bad_signature = copy.deepcopy(payload)
    bad_signature["binding"]["signature"] = "A" * 86
    with pytest.raises(CatalogError, match="signature"):
        owner_adapter.receive(remote_id, bad_signature)
    receipt = owner_adapter.receive_signed({"proof": proof, "payload": payload})
    assert receipt["state"] == "applied"
    with pytest.raises(CatalogError, match="already used") as replay:
        owner_adapter.receive_signed({"proof": proof, "payload": payload})
    assert replay.value.status == 409
    assert read_feedback(store, payload["reaction"]["id"]) is not None


@pytest.mark.parametrize(
    "mutation", ["commission", "run", "project", "revision", "sources", "output"]
)
async def test_receiver_rejects_orphan_or_mismatched_owner_lineage_before_write(
    tmp_path, mutation
):
    _, remote, store, _, _, _, _, target, owner_peers, remote_peers, owner_adapter = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
    remote_adapter = PeerFeedbackStore(remote, peers=remote_peers)
    altered = copy.deepcopy(target)
    if mutation == "commission":
        altered["commission_id"] = "foreign-commission"
    elif mutation == "run":
        altered["run_id"] = "foreign-run"
    elif mutation == "project":
        altered["project"]["id"] = "foreign-project"
    elif mutation == "revision":
        altered["project"]["revision"] += 1
    elif mutation == "sources":
        altered["sources"][0]["revision"] += 1
    else:
        altered["output"]["content_hash"] = "0" * 64
    payload = remote_adapter.prepare(
        owner_id, altered, reaction_body(), target_approval(owner_id, altered)
    )
    with pytest.raises(CatalogError, match="lineage") as rejected:
        owner_adapter.receive(remote_id, payload)
    assert rejected.value.status == 409
    assert read_feedback(store, payload["reaction"]["id"]) is None


async def test_author_isolation_revision_conflict_and_tombstone_are_durable(tmp_path):
    (
        owner,
        remote,
        store,
        _,
        _,
        _,
        _,
        target,
        owner_peers,
        remote_peers,
        owner_adapter,
    ) = await connected_pair(tmp_path)
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
    adapter = PeerFeedbackStore(remote, peers=remote_peers)
    alice = adapter.prepare(
        owner_id,
        target,
        reaction_body(author="alice"),
        target_approval(owner_id, target),
    )
    bob = adapter.prepare(
        owner_id, target, reaction_body(author="bob"), target_approval(owner_id, target)
    )
    assert alice["reaction"]["id"] != bob["reaction"]["id"]
    owner_adapter.receive(remote_id, alice)
    owner_adapter.receive(remote_id, bob)
    conflicting = copy.deepcopy(alice)
    conflicting["reaction"]["note"] = "different"
    unsigned = {k: v for k, v in conflicting.items() if k != "binding"}
    conflicting["binding"]["payload_hash"] = _hash(unsigned)
    private, _ = remote_peers._identity()
    signed = {k: v for k, v in conflicting["binding"].items() if k != "signature"}
    import base64

    conflicting["binding"]["signature"] = (
        base64.urlsafe_b64encode(
            private.sign(
                json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
            )
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(CatalogError, match="different content"):
        owner_adapter.receive(remote_id, conflicting)
    assert read_feedback(store, bob["reaction"]["id"])["deleted"] is False
    reopened = PeerFeedbackStore(
        owner,
        commissions=CommissionStore(owner),
        direction=DirectionStore(owner),
        peers=PeerStore(owner),
    )
    assert read_feedback(reopened.commissions, alice["reaction"]["id"])["revision"] == 1


def test_feedback_scope_is_independent_and_default_denied(tmp_path):
    peers, remote = PeerStore(tmp_path / "a"), PeerStore(tmp_path / "b")
    row = connect_peer(peers, remote, "http://127.0.0.1:9", send=["creative.catalog"])
    assert SCOPE == "creative.commission_feedback"
    assert peers.allows(row["id"], "creative.catalog", "send") is True
    assert peers.allows(row["id"], SCOPE, "send") is False
    with pytest.raises(PeerError):
        peers.create_proof(row["id"], SCOPE)


async def test_real_dashboard_middleware_delegates_only_exact_signed_receive_post(
    tmp_path,
):
    owner, remote, store, direction, _, _, _, target, owner_peers, remote_peers, _ = (
        await connected_pair(tmp_path)
    )
    app = web.Application(
        middlewares=[token_auth_middleware(port=47991, local_only=True)]
    )
    register_peer_feedback(
        app, owner, commissions=store, direction=direction, peers=owner_peers
    )

    async def nearby_handler(request):
        return web.json_response({"unsafe": True})

    app.router.add_post(
        "/api/capabilities/creative/commission-feedback/receive-nearby", nearby_handler
    )
    async with TestClient(TestServer(app)) as client:
        connect_peer(remote_peers, owner_peers, str(client.make_url("/")), send=[SCOPE])
        connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
        owner_id = owner_peers.snapshot()["self"]["peer_id"]
        remote_adapter = PeerFeedbackStore(remote, peers=remote_peers)
        payload = remote_adapter.prepare(
            owner_id, target, reaction_body(), target_approval(owner_id, target)
        )
        accepted = await remote_adapter.push_prepared(owner_id, payload)
        assert accepted["state"] == "applied"
        assert read_feedback(store, accepted["reaction_id"]) == payload["reaction"]

        malformed = await client.post(RECEIVE_PATH, json={"not": "an envelope"})
        assert malformed.status == 400
        assert "envelope" in (await malformed.json())["error"]
        nearby = await client.post(
            "/api/capabilities/creative/commission-feedback/receive-nearby", json={}
        )
        assert nearby.status == 403
        assert (await nearby.json())["error"] == "Forbidden"
        wrong_method = await client.get(RECEIVE_PATH)
        assert wrong_method.status == 403
        assert (await wrong_method.json())["error"] == "Forbidden"


def test_creative_registrar_mounts_the_exact_peer_feedback_route(tmp_path):
    from gideon.interfaces.dashboard.handlers import capabilities_creative
    from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE
    from gideon.workspace.capabilities.creative.store import IngredientStore

    app = web.Application()
    app[STORE] = IngredientStore(tmp_path)
    capabilities_creative.register(app)
    matching = [
        route
        for route in app.router.routes()
        if route.method == "POST" and route.resource.canonical == RECEIVE_PATH
    ]
    assert len(matching) == 1


async def test_prepared_outbox_survives_restart_and_requires_optimistic_revision(
    tmp_path,
):
    _, remote, _, _, _, _, _, target, owner_peers, remote_peers, _ = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    adapter = PeerFeedbackStore(remote, peers=remote_peers)
    first = adapter.prepare(
        owner_id, target, reaction_body(), target_approval(owner_id, target)
    )
    reopened = PeerFeedbackStore(remote, peers=PeerStore(remote))
    with reopened.db() as db:
        stored = db.execute(
            "SELECT peer_id,record,lineage FROM creative_feedback_outbox"
        ).fetchone()
    assert stored[0] == owner_id
    assert json.loads(stored[1]) == first["reaction"]
    assert json.loads(stored[2]) == target
    with pytest.raises(CatalogError, match="reload") as stale:
        reopened.prepare(
            owner_id,
            target,
            reaction_body(revision=9),
            target_approval(owner_id, target),
        )
    assert stale.value.status == 409
    second = reopened.prepare(
        owner_id,
        target,
        reaction_body(revision=1, rating="disliked"),
        target_approval(owner_id, target),
    )
    assert second["reaction"]["id"] == first["reaction"]["id"]
    assert second["reaction"]["revision"] == 2
    assert second["reaction"]["created_at"] == first["reaction"]["created_at"]
    assert second["reaction"]["updated_at"] >= first["reaction"]["updated_at"]


@pytest.mark.parametrize(
    "body,error",
    [
        ({"author": "", "rating": "liked", "note": "", "tags": []}, "identifier"),
        ({"author": "alice", "rating": "up", "note": "", "tags": []}, "rating"),
        (
            {"author": "alice", "rating": "liked", "note": "", "tags": ["x", "x"]},
            "tags",
        ),
        (
            {"author": "alice", "rating": "liked", "note": "", "tags": ["x"] * 21},
            "tags",
        ),
        (
            {"author": "alice", "rating": "liked", "note": "x" * 2001, "tags": []},
            "length",
        ),
        (
            {
                "author": "alice",
                "rating": "liked",
                "note": "",
                "tags": [],
                "extra": True,
            },
            "fields",
        ),
    ],
)
async def test_prepare_validates_author_rating_note_tags_and_exact_fields(
    tmp_path, body, error
):
    _, remote, _, _, _, _, _, target, owner_peers, remote_peers, _ = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    adapter = PeerFeedbackStore(remote, peers=remote_peers)
    with pytest.raises((CatalogError, ValueError)) as rejected:
        adapter.prepare(owner_id, target, body, target_approval(owner_id, target))
    assert error.lower() in str(rejected.value).lower()
    with adapter.db() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM creative_feedback_outbox").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "approval",
    [
        None,
        {},
        {
            "decision": "approved",
            "peer_id": "peer-" + "0" * 32,
            "target_hash": "0" * 64,
        },
        {"decision": "denied", "peer_id": "placeholder", "target_hash": "0" * 64},
        {
            "decision": "approved",
            "peer_id": "placeholder",
            "target_hash": "0" * 64,
            "extra": True,
        },
    ],
)
async def test_approval_is_exact_target_and_peer_attestation(tmp_path, approval):
    _, remote, _, _, _, _, _, target, owner_peers, remote_peers, _ = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    if isinstance(approval, dict) and approval.get("peer_id") == "placeholder":
        approval["peer_id"] = owner_id
    adapter = PeerFeedbackStore(remote, peers=remote_peers)
    with pytest.raises(CatalogError) as rejected:
        adapter.prepare(owner_id, target, reaction_body(), approval)
    assert rejected.value.status in {400, 403}


async def test_receiver_policy_denial_happens_before_payload_binding_or_store_access(
    tmp_path,
):
    _, remote, store, _, _, _, _, target, owner_peers, remote_peers, owner_adapter = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9")
    payload = PeerFeedbackStore(remote, peers=remote_peers).prepare(
        owner_id, target, reaction_body(), target_approval(owner_id, target)
    )
    malformed = copy.deepcopy(payload)
    malformed["binding"]["signature"] = "not-a-signature"
    with pytest.raises(CatalogError, match="policy") as denied:
        owner_adapter.receive(remote_id, malformed)
    assert denied.value.status == 403
    assert read_feedback(store, payload["reaction"]["id"]) is None


async def test_prepared_delivery_refuses_another_recipient_and_invalid_receipt(
    tmp_path,
):
    owner, remote, _, _, _, _, _, target, owner_peers, remote_peers, _ = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    calls = []
    app = web.Application()

    async def invalid_receipt(request):
        calls.append(await request.json())
        return web.json_response({"delivery_id": "wrong", "payload_hash": "wrong"})

    app.router.add_post(RECEIVE_PATH, invalid_receipt)
    async with TestClient(TestServer(app)) as client:
        connect_peer(remote_peers, owner_peers, str(client.make_url("/")), send=[SCOPE])
        adapter = PeerFeedbackStore(remote, peers=remote_peers)
        payload = adapter.prepare(
            owner_id, target, reaction_body(), target_approval(owner_id, target)
        )
        with pytest.raises(CatalogError, match="different peer") as wrong:
            await adapter.push_prepared(
                remote_peers.snapshot()["self"]["peer_id"], payload
            )
        assert wrong.value.status == 409
        with pytest.raises(CatalogError, match="invalid feedback receipt") as invalid:
            await adapter.push_prepared(owner_id, payload)
        assert invalid.value.status == 502
        assert len(calls) == 1
        with adapter.db() as db:
            assert (
                db.execute("SELECT COUNT(*) FROM creative_feedback_sends").fetchone()[0]
                == 0
            )
    assert owner != remote


async def test_lower_unseen_revision_is_receipted_stale_without_overwriting_newer_owner_record(
    tmp_path,
):
    _, remote, store, _, _, _, _, target, owner_peers, remote_peers, owner_adapter = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
    adapter = PeerFeedbackStore(remote, peers=remote_peers)
    first = adapter.prepare(
        owner_id, target, reaction_body(), target_approval(owner_id, target)
    )
    second = adapter.prepare(
        owner_id,
        target,
        reaction_body(revision=1, rating="disliked"),
        target_approval(owner_id, target),
    )
    assert owner_adapter.receive(remote_id, second)["state"] == "applied"
    assert read_feedback(store, first["reaction"]["id"])["revision"] == 2
    stale = owner_adapter.receive(remote_id, first)
    assert stale["state"] == "stale"
    assert stale["revision"] == 1
    current = read_feedback(store, first["reaction"]["id"])
    assert current["revision"] == 2
    assert current["rating"] == "disliked"


async def test_exact_replay_returns_original_receipt_timestamp_across_receiver_restart(
    tmp_path,
):
    (
        owner,
        remote,
        store,
        direction,
        _,
        _,
        _,
        target,
        owner_peers,
        remote_peers,
        owner_adapter,
    ) = await connected_pair(tmp_path)
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
    payload = PeerFeedbackStore(remote, peers=remote_peers).prepare(
        owner_id, target, reaction_body(), target_approval(owner_id, target)
    )
    first = owner_adapter.receive(remote_id, payload)
    reopened = PeerFeedbackStore(
        owner,
        commissions=CommissionStore(owner),
        direction=DirectionStore(owner),
        peers=PeerStore(owner),
    )
    replay = reopened.receive(remote_id, payload)
    assert replay == first
    assert replay["received_at"] == first["received_at"]
    assert replay["payload_hash"] == payload["binding"]["payload_hash"]
    assert read_feedback(store, payload["reaction"]["id"]) == payload["reaction"]
    assert direction.get(target["project"]["id"])["sources"]


async def test_tombstoning_one_remote_author_preserves_another_author(tmp_path):
    _, remote, store, _, _, _, _, target, owner_peers, remote_peers, owner_adapter = (
        await connected_pair(tmp_path)
    )
    owner_id = owner_peers.snapshot()["self"]["peer_id"]
    remote_id = remote_peers.snapshot()["self"]["peer_id"]
    connect_peer(remote_peers, owner_peers, "http://127.0.0.1:9", send=[SCOPE])
    connect_peer(owner_peers, remote_peers, "http://127.0.0.1:9", receive=[SCOPE])
    adapter = PeerFeedbackStore(remote, peers=remote_peers)
    alice = adapter.prepare(
        owner_id,
        target,
        reaction_body(author="alice"),
        target_approval(owner_id, target),
    )
    bob = adapter.prepare(
        owner_id, target, reaction_body(author="bob"), target_approval(owner_id, target)
    )
    owner_adapter.receive(remote_id, alice)
    owner_adapter.receive(remote_id, bob)
    tombstone = adapter.revoke_prepared(
        owner_id, alice["reaction"]["id"], 1, target_approval(owner_id, target)
    )
    receipt = owner_adapter.receive(remote_id, tombstone)
    assert receipt["state"] == "applied"
    assert read_feedback(store, alice["reaction"]["id"])["deleted"] is True
    bob_record = read_feedback(store, bob["reaction"]["id"])
    assert bob_record["deleted"] is False
    assert bob_record["author"] == "bob"
    assert bob_record["revision"] == 1


async def test_owner_target_accepts_canonical_output_with_extra_local_fields(tmp_path):
    *_, target, _, _, owner_adapter = await connected_pair(tmp_path)
    with owner_adapter.commissions.db() as db:
        row = db.execute(
            "SELECT record FROM creative_commission_runs WHERE id=?",
            (target["run_id"],),
        ).fetchone()
        run = json.loads(row[0])
        run["outputs"][0]["path"] = "/machine-local/not-shared.png"
        db.execute(
            "UPDATE creative_commission_runs SET record=? WHERE id=?",
            (json.dumps(run, sort_keys=True), run["id"]),
        )
    resolved = owner_adapter.target(
        target["commission_id"], target["run_id"], target["output"]
    )
    assert resolved == target
    assert "path" not in resolved["output"]
