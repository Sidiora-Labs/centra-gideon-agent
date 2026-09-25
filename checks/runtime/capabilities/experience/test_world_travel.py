import hashlib
import json
import os
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from checks.runtime.capabilities.experience.test_world_engine import (
    DEPS,
    install_engine,
    receive_type,
)
from gideon.extensions.apps.backend_runtime import get_backend_supervisor
from gideon.workspace.capabilities.experience import Conflict, ExperienceStore, NotFound
from gideon.workspace.capabilities.experience.world_engine import APP_ID, WorldEngine
from gideon.workspace.capabilities.experience.world_travel import SCOPE, WorldTravel
from gideon.workspace.capabilities.experience.world_travel_http import (
    register_world_travel,
)
from gideon.workspace.capabilities.experience.worlds import get_worlds
from gideon.workspace.capabilities.platform.peers import PeerError, PeerStore


def peer_record(identity, endpoint, send=None, receive=None, enabled=True, revision=0):
    return {
        "label": "Remote world",
        "endpoint": endpoint,
        "public_key": identity["public_key"],
        "enabled": enabled,
        "send_categories": list([SCOPE] if send is None else send),
        "receive_categories": list([SCOPE] if receive is None else receive),
        "revision": revision,
    }


@pytest.fixture
async def travel_network(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv(
        "PATH", str(DEPS / "bun-linux-x64") + os.pathsep + os.environ["PATH"]
    )
    store = ExperienceStore(tmp_path / "runtime")
    engine = WorldEngine(store)
    install_engine()
    assert (await engine.control("start", {}))["state"] == "running"
    worlds = get_worlds(store)
    await worlds.open("lounge", {})
    sender, receiver = PeerStore(tmp_path / "sender"), PeerStore(tmp_path / "receiver")
    app = web.Application()
    register_world_travel(app, store, receiver)
    server = TestServer(app)
    await server.start_server()
    sender_identity, receiver_identity = (
        sender.snapshot()["self"],
        receiver.snapshot()["self"],
    )
    sender.put(
        receiver_identity["peer_id"],
        peer_record(receiver_identity, str(server.make_url("/"))),
    )
    receiver.put(
        sender_identity["peer_id"],
        peer_record(sender_identity, "https://sender.example"),
    )
    yield store, worlds, sender, receiver, sender_identity, receiver_identity, server
    await server.close()
    await worlds.close()
    get_backend_supervisor().stop(APP_ID)


@pytest.mark.asyncio
async def test_real_signed_travel_admission_guest_presence_leave_and_restart(
    travel_network,
):
    store, worlds, sender, receiver, sender_identity, receiver_identity, server = (
        travel_network
    )
    travel = WorldTravel(store, sender, worlds)
    assert await travel.destinations() == {
        "schema_version": 1,
        "destinations": [
            {
                "id": receiver_identity["peer_id"],
                "label": "Remote world",
                "category": SCOPE,
            }
        ],
    }
    body = {
        "peer_id": receiver_identity["peer_id"],
        "world": "lounge",
        "guest_name": "Example Visitor",
        "request_id": "depart_once",
    }
    departure = await travel.depart(body)
    assert departure["peer_id"] == receiver_identity["peer_id"]
    assert departure["label"] == "Remote world"
    parsed = urlparse(departure["url"])
    assert parsed.path == "/"
    assert parsed.fragment.startswith("/capabilities/experience?guest=")
    ticket = parse_qs(parsed.fragment.split("?", 1)[1])["guest"][0]
    assert len(ticket) >= 32
    async with TestClient(server) as client:
        guest_response = await client.get(
            "/api/capabilities/experience/world-travel/guest/" + ticket
        )
        assert guest_response.status == 200
        guest = await guest_response.json()
        assert guest == {
            "schema_version": 1,
            "visit_id": departure["visit_id"],
            "peer_id": sender_identity["peer_id"],
            "world": "lounge",
            "guest_name": "Example Visitor",
            "state": "active",
            "expires_at": departure["expires_at"],
            "revision": 2,
        }
        refused = await client.get(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/host/gideon/worlds/other?world=other"
        )
        assert refused.status == 409
        canonical = await client.get(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/host/gideon/worlds/lounge?world=lounge"
        )
        assert canonical.status == 200
        assert (await canonical.json())["world"] == "lounge"
        mutation = await client.post(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/host/gideon/worlds/lounge?world=lounge",
            json={},
        )
        assert mutation.status == 405
        assert (await mutation.json())["error"] == "Guest world bridge is read-only"
        renderer = await client.get(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/host/?world=lounge"
        )
        assert renderer.status == 200
        rendered = await renderer.text()
        scoped_bridge = f"/api/capabilities/experience/world-travel/guest/{ticket}/host"
        assert f'<base href="{scoped_bridge}/">' in rendered
        assert "const bridgeBase=" + repr(scoped_bridge) in rendered
        async with client.ws_connect(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/host/ws?world=lounge"
        ) as socket:
            await socket.send_json(
                {
                    "type": "join",
                    "world": "other",
                    "id": "forged",
                    "agent": True,
                    "avatar": "eidoverse/assets/vrms/claude.vrm",
                }
            )
            snapshot = await receive_type(socket, "snapshot")
            guest_id = "guest_" + guest["visit_id"]
            assert snapshot["world"] == "lounge"
            assert snapshot["you"] == guest_id
            identities = {row["id"]: row for row in snapshot["present"]}
            assert guest_id not in identities
            assert "gideon" in identities
            assert "forged" not in identities
            live = await client.get(
                f"/api/capabilities/experience/world-travel/guest/{ticket}/host/gideon/worlds/lounge?world=lounge"
            )
            live_identities = {row["id"]: row for row in (await live.json())["present"]}
            assert live_identities[guest_id]["agent"] is False
            assert "forged" not in live_identities
            await socket.send_json(
                {
                    "type": "verb",
                    "verb": "spawn",
                    "args": {"id": "guest_owned", "lib": "anything", "pos": [0, 0, 0]},
                }
            )
            denied = await receive_type(socket, "error")
            assert (
                denied["error"]
                == "Guest world access is limited to presence and history"
            )
            assert (
                "guest_owned" not in (await worlds.call("lounge"))["state"]["entities"]
            )
        leave_body = {"revision": guest["revision"], "request_id": "leave_once"}
        left = await client.post(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/leave",
            json=leave_body,
        )
        assert left.status == 200
        receipt = await left.json()
        replay = await client.post(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/leave",
            json=leave_body,
        )
        assert replay.status == 200
        assert await replay.json() == receipt
        assert (
            await client.get(
                "/api/capabilities/experience/world-travel/guest/" + ticket
            )
        ).status == 404
    reopened = WorldTravel(ExperienceStore(store.path.parent.parent), receiver, worlds)
    with pytest.raises(NotFound, match="ended"):
        reopened.guest(ticket)
    with sqlite3.connect(store.path) as database:
        digest, state, revision = database.execute(
            "SELECT ticket_hash,state,revision FROM world_guest_visits"
        ).fetchone()
    assert digest == hashlib.sha256(ticket.encode()).hexdigest()
    assert state == "left" and revision == 3


@pytest.mark.asyncio
async def test_signed_admission_is_idempotent_and_rejects_replay_forgery_and_policy(
    travel_network,
):
    store, worlds, sender, receiver, sender_identity, receiver_identity, server = (
        travel_network
    )
    path = "/api/capabilities/experience/world-travel/federation/admit"
    payload = {
        "world": "lounge",
        "guest_name": "Bound Guest",
        "request_id": "same_admission",
    }
    proof = sender.create_proof(receiver_identity["peer_id"], SCOPE)
    async with TestClient(server) as client:
        admitted = await client.post(path, json={"proof": proof, "payload": payload})
        assert admitted.status == 200
        first = await admitted.json()
        replayed_proof = await client.post(
            path, json={"proof": proof, "payload": payload}
        )
        assert replayed_proof.status == 409
        second = await sender.post_signed(
            receiver_identity["peer_id"], SCOPE, path, payload
        )
        assert second == first
        with pytest.raises(PeerError, match="HTTP 409") as conflict:
            await sender.post_signed(
                receiver_identity["peer_id"],
                SCOPE,
                path,
                {**payload, "guest_name": "Changed"},
            )
        assert conflict.value.status == 502
        malformed = await client.post(path, json={"payload": payload})
        assert malformed.status == 400
        forged = sender.create_proof(receiver_identity["peer_id"], SCOPE)
        forged["sender"] = "peer-" + "0" * 32
        denied = await client.post(path, json={"proof": forged, "payload": payload})
        assert denied.status in (401, 404)
    current = receiver.get(sender_identity["peer_id"])
    receiver.put(
        sender_identity["peer_id"],
        peer_record(
            sender_identity,
            current["endpoint"],
            send=current["send_categories"],
            receive=[],
            revision=current["revision"],
        ),
    )
    direct = WorldTravel(store, receiver, worlds)
    with pytest.raises(Conflict, match="not enabled"):
        await direct.admit(
            sender_identity["peer_id"], {**payload, "request_id": "denied_policy"}
        )


@pytest.mark.asyncio
async def test_validation_expiry_capacity_and_home_binding(
    travel_network, monkeypatch, tmp_path
):
    store, worlds, sender, receiver, sender_identity, receiver_identity, _server = (
        travel_network
    )
    now = [1000]
    travel = WorldTravel(store, receiver, worlds, now=lambda: now[0])
    valid = {
        "world": "lounge",
        "guest_name": "Visitor",
        "request_id": "admit_validation",
    }
    result = await travel.admit(sender_identity["peer_id"], valid)
    for body in (
        None,
        [],
        {},
        {**valid, "extra": True},
        {**valid, "world": "../escape"},
        {**valid, "guest_name": "bad\nname"},
        {**valid, "request_id": "x/y"},
    ):
        with pytest.raises((ValueError, TypeError)):
            await travel.admit(sender_identity["peer_id"], body)
    assert (await travel.admit(sender_identity["peer_id"], valid)) == result
    with pytest.raises(Conflict, match="reused"):
        await travel.admit(sender_identity["peer_id"], {**valid, "guest_name": "Other"})
    now[0] = result["expires_at"]
    with pytest.raises(NotFound, match="expired"):
        travel.guest(result["ticket"])
    for ticket in ("", "short", "x" * 129):
        with pytest.raises(NotFound):
            travel.guest(ticket)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "other-home"))
    with pytest.raises(Conflict, match="home changed"):
        await travel.admit(
            sender_identity["peer_id"], {**valid, "request_id": "wrong_home"}
        )
    assert not (tmp_path / "other-home").exists()


@pytest.mark.asyncio
async def test_bounded_capacity_ignores_expired_visits_and_refuses_overflow(
    travel_network,
):
    store, worlds, _sender, receiver, sender_identity, _receiver_identity, _server = (
        travel_network
    )
    now = [5000]
    travel = WorldTravel(store, receiver, worlds, now=lambda: now[0])
    tickets = []
    for index in range(32):
        admitted = await travel.admit(
            sender_identity["peer_id"],
            {
                "world": "lounge",
                "guest_name": f"Visitor {index}",
                "request_id": f"capacity_{index}",
            },
        )
        tickets.append(admitted["ticket"])
        assert admitted["expires_at"] == 6800
    with pytest.raises(Conflict, match="limit"):
        await travel.admit(
            sender_identity["peer_id"],
            {
                "world": "lounge",
                "guest_name": "Overflow",
                "request_id": "capacity_overflow",
            },
        )
    assert travel.guest(tickets[0])["state"] == "active"
    now[0] = 6800
    replacement = await travel.admit(
        sender_identity["peer_id"],
        {
            "world": "lounge",
            "guest_name": "After expiry",
            "request_id": "capacity_after_expiry",
        },
    )
    assert replacement["expires_at"] == 8600
    with sqlite3.connect(store.path) as database:
        assert (
            database.execute("SELECT count(*) FROM world_guest_visits").fetchone()[0]
            == 33
        )


@pytest.mark.asyncio
async def test_departure_and_guest_mutation_validation_fail_closed(travel_network):
    store, worlds, sender, receiver, sender_identity, receiver_identity, server = (
        travel_network
    )
    travel = WorldTravel(store, sender, worlds)
    valid = {
        "peer_id": receiver_identity["peer_id"],
        "world": "lounge",
        "guest_name": "Visitor",
        "request_id": "departure_valid",
    }
    for body in (
        None,
        [],
        {},
        {**valid, "extra": 1},
        {**valid, "peer_id": "missing"},
        {**valid, "world": "../escape"},
        {**valid, "guest_name": ""},
        {**valid, "request_id": "bad/request"},
    ):
        with pytest.raises((ValueError, Conflict)):
            await travel.depart(body)
    departure = await travel.depart(valid)
    ticket = parse_qs(urlparse(departure["url"]).fragment.split("?", 1)[1])["guest"][0]
    async with TestClient(server) as client:
        guest = await (
            await client.get(
                "/api/capabilities/experience/world-travel/guest/" + ticket
            )
        ).json()
        for body in (
            None,
            [],
            {},
            {"revision": guest["revision"]},
            {"revision": True, "request_id": "bad"},
            {"revision": guest["revision"], "request_id": "bad/id"},
        ):
            response = await client.post(
                f"/api/capabilities/experience/world-travel/guest/{ticket}/leave",
                json=body,
            )
            assert response.status == 400
        stale = await client.post(
            f"/api/capabilities/experience/world-travel/guest/{ticket}/leave",
            json={"revision": guest["revision"] - 1, "request_id": "stale_leave"},
        )
        assert stale.status == 409
        assert (
            await (
                await client.get(
                    "/api/capabilities/experience/world-travel/guest/" + ticket
                )
            ).json()
        )["state"] == "active"
        assert (
            await client.get(
                "/api/capabilities/experience/world-travel/guest/" + "x" * 64
            )
        ).status == 404
    current = sender.get(receiver_identity["peer_id"])
    sender.put(
        receiver_identity["peer_id"],
        peer_record(
            receiver_identity,
            current["endpoint"],
            send=[],
            receive=current["receive_categories"],
            revision=current["revision"],
        ),
    )
    assert (await travel.destinations())["destinations"] == []
    with pytest.raises(Conflict, match="not enabled"):
        await travel.depart({**valid, "request_id": "policy_denied"})
