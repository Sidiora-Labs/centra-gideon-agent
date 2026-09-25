import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.moltworld import API_BASE, PROTOCOL, Moltworld, RemoteError
from gideon.workspace.capabilities.experience.moltworld_http import register_moltworld
from gideon.workspace.capabilities.experience.moltworld_provider import MoltworldTools
from gideon.workspace.capabilities.experience.store import Conflict


@pytest.fixture
async def moltworld(tmp_path):
    seen = {"status":0, "observe":0, "actions":[]}

    async def status(request):
        seen["status"] += 1
        assert request.headers["Authorization"] == "Bearer mw_test_secret"
        return web.json_response({"id":"agent-1", "name":"Gideon", "x":50, "y":51, "hp":99, "energy":80, "inventory":[{"type":"berry","quantity":2}], "walletAddress":"private-remote-field"})

    async def observe(request):
        seen["observe"] += 1
        assert "Authorization" not in request.headers
        assert dict(request.query) == {"x1":"40", "y1":"40", "x2":"45", "y2":"45"}
        return web.json_response({"agents":[{"id":"other"}], "blocks":[], "items":[], "time":{"tick":120,"isDay":True}})

    async def action(request):
        assert request.headers["Authorization"] == "Bearer mw_test_secret"
        body = await request.json()
        seen["actions"].append(body)
        if body["action"] == "speak": return web.json_response({"error":"one action per tick"}, status=429, headers={"Retry-After":"5"})
        if body["action"] == "respawn":
            request.transport.close()
            return web.Response()
        return web.json_response({"success":True, "message":"Action queued for tick 121", "queuedForTick":121, "currentTick":120}, status=202)

    remote = web.Application()
    remote.router.add_get("/api/v1/agent", status)
    remote.router.add_get("/api/v1/observe", observe)
    remote.router.add_post("/api/v1/action", action)
    server = TestServer(remote)
    await server.start_server()
    store = ExperienceStore(tmp_path)
    service = Moltworld(store, credential_resolver=lambda name: "mw_test_secret" if name == "moltworld-key" else None, base_url=str(server.make_url("")))
    service.configure({"enabled":True, "credential_name":"moltworld-key", "revision":0})
    yield service, seen, server, tmp_path
    await server.close()


def approved(request_id, action, params):
    return {"request_id":request_id, "action":action, "params":params, "approval":{"approved":True,"source":"user"}}


@pytest.mark.asyncio
async def test_current_v1_status_and_public_observation_use_actual_http_contract(moltworld):
    service, seen, _server, _tmp_path = moltworld
    readiness = service.readiness()
    assert readiness == {"protocol":PROTOCOL, "base_url":API_BASE, "config":{"enabled":True,"credential_name":"moltworld-key","revision":1},
                         "credential_available":True, "remote_status":"unverified", "ready":True}
    state = await service.status()
    assert state["verification"] == "provider_verified"
    assert state["agent"] == {"id":"agent-1", "name":"Gideon", "x":50, "y":51, "hp":99, "energy":80}
    assert state["inventory"] == [{"type":"berry","quantity":2}]
    assert "walletAddress" in state["raw_fields"]
    assert "private-remote-field" not in json.dumps(state)
    observation = await service.observe({"x1":40,"y1":40,"x2":45,"y2":45})
    assert observation["verification"] == "provider_verified"
    assert observation["area"] == {"x1":40,"y1":40,"x2":45,"y2":45}
    assert observation["observation"]["time"]["tick"] == 120
    assert seen["status"] == 1
    assert seen["observe"] == 1


@pytest.mark.asyncio
async def test_explicitly_approved_action_is_queued_not_completed_and_idempotent(moltworld):
    service, seen, _server, _tmp_path = moltworld
    request = approved("move_once", "move", {"direction":"n"})
    receipt = await service.action(request)
    assert receipt["request_id"] == "move_once"
    assert receipt["approval"] == "user_attested"
    assert receipt["state"] == "queued_remote"
    assert receipt["queued_for_tick"] == 121
    assert receipt["current_tick"] == 120
    assert len(receipt["provider_response_sha256"]) == 64
    assert seen["actions"] == [{"action":"move", "params":{"direction":"n"}}]
    assert await service.action(request) == receipt
    assert len(seen["actions"]) == 1
    with pytest.raises(Conflict, match="different input"):
        await service.action(approved("move_once", "move", {"direction":"s"}))
    assert len(seen["actions"]) == 1
    reopened = Moltworld(ExperienceStore(_tmp_path), credential_resolver=lambda _name:"mw_test_secret", base_url=service.base_url)
    assert reopened.history()[0] == receipt


@pytest.mark.asyncio
async def test_external_refusal_and_transport_uncertainty_are_durable_and_not_retried(moltworld):
    service, seen, _server, tmp_path = moltworld
    refused_request = approved("speak_limited", "speak", {"message":"hello"})
    refused = await service.action(refused_request)
    assert refused["state"] == "refused_remote"
    assert refused["error"] == "one action per tick"
    assert refused["retry_after"] == "5"
    uncertain_request = approved("respawn_unknown", "respawn", {})
    uncertain = await service.action(uncertain_request)
    assert uncertain["state"] == "outcome_unknown"
    assert "unavailable" in uncertain["error"].lower()
    assert len(seen["actions"]) == 2
    reopened = Moltworld(ExperienceStore(tmp_path), credential_resolver=lambda _name:"mw_test_secret", base_url=service.base_url)
    assert await reopened.action(uncertain_request) == uncertain
    assert len(seen["actions"]) == 2
    assert [row["state"] for row in reopened.history()] == ["outcome_unknown", "refused_remote"]


@pytest.mark.asyncio
async def test_approval_configuration_secret_and_validation_boundaries(moltworld):
    service, seen, _server, tmp_path = moltworld
    with pytest.raises(Conflict, match="approval"):
        await service.action({"request_id":"not_approved", "action":"move", "params":{"direction":"n"}, "approval":{"approved":False,"source":"user"}})
    assert seen["actions"] == []
    for action, params in (("move",{"direction":"up"}), ("take",{"item":"stone"}), ("withdraw",{"amount":0}),
                           ("speak",{"message":"x"*501}), ("unknown",{}), ("look",{"extra":True})):
        with pytest.raises(ValueError):
            await service.action(approved("invalid_" + action, action, params))
    for area in ({"x1":0,"y1":0,"x2":20,"y2":19}, {"x1":5,"y1":5,"x2":4,"y2":6}, {"x1":0,"y1":0,"x2":1}, {"x1":0,"y1":0,"x2":1,"y2":100}):
        with pytest.raises(ValueError): await service.observe(area)
    with pytest.raises(Conflict, match="revision"):
        service.configure({"enabled":False,"credential_name":"moltworld-key","revision":0})
    config_bytes = service.store.path.read_bytes()
    assert b"mw_test_secret" not in config_bytes
    disabled = service.configure({"enabled":False,"credential_name":"moltworld-key","revision":1})
    assert disabled["revision"] == 2
    assert service.readiness()["ready"] is False
    with pytest.raises(Conflict, match="disabled"):
        await service.status()
    assert not (tmp_path / "credentials.json").exists()


@pytest.mark.asyncio
async def test_http_and_native_provider_preserve_verified_vs_attested_states(moltworld):
    service, _seen, _server, _tmp_path = moltworld
    app = web.Application()
    register_moltworld(app, service.store, service)
    async with TestClient(TestServer(app)) as client:
        local = await client.get("/api/capabilities/experience/moltworld")
        assert local.status == 200
        assert (await local.json())["readiness"]["remote_status"] == "unverified"
        remote = await client.get("/api/capabilities/experience/moltworld/status")
        assert remote.status == 200
        assert (await remote.json())["verification"] == "provider_verified"
        queued = await client.post("/api/capabilities/experience/moltworld/actions", json=approved("http_move", "move", {"direction":"e"}))
        assert queued.status == 200
        assert (await queued.json())["receipt"]["state"] == "queued_remote"
        history = await client.get("/api/capabilities/experience/moltworld/history")
        assert (await history.json())["history"][0]["approval"] == "user_attested"
    provider = MoltworldTools(service=service)
    definitions = {definition.name:definition for definition in await provider.list_tools()}
    assert not definitions["experience_moltworld_get"].requires_approval
    assert not definitions["experience_moltworld_status"].requires_approval
    assert not definitions["experience_moltworld_observe"].requires_approval
    assert definitions["experience_moltworld_configure"].requires_approval
    assert definitions["experience_moltworld_action"].requires_approval
    assert definitions["experience_moltworld_action"].risk_level.value == "destructive"
    snapshot = await provider.invoke("experience_moltworld_get", {})
    assert snapshot.success
    assert json.loads(snapshot.output)["history"][0]["state"] == "queued_remote"
    malformed = await provider.invoke("experience_moltworld_action", {"action":"move"})
    assert not malformed.success


@pytest.mark.asyncio
async def test_incompatible_current_protocol_responses_fail_closed(tmp_path):
    async def bad_status(_request): return web.json_response({"id":"agent"})
    async def bad_action(_request): return web.json_response({"success":True,"queuedForTick":3}, status=200)
    app = web.Application()
    app.router.add_get("/api/v1/agent", bad_status)
    app.router.add_post("/api/v1/action", bad_action)
    server = TestServer(app)
    await server.start_server()
    service = Moltworld(ExperienceStore(tmp_path), credential_resolver=lambda _name:"secret", base_url=str(server.make_url("")))
    service.configure({"enabled":True,"credential_name":"key","revision":0})
    try:
        with pytest.raises(RemoteError, match="missing required"):
            await service.status()
        receipt = await service.action(approved("bad_queue", "look", {}))
        assert receipt["state"] == "refused_remote"
        assert receipt["error"] == "Moltworld queue response is incompatible"
    finally:
        await server.close()


def test_current_primary_action_shapes_are_explicit_and_bounded(tmp_path):
    service = Moltworld(ExperienceStore(tmp_path), credential_resolver=lambda _name:"secret")
    valid = {
        "look": {},
        "move": {"direction":"w"},
        "break": {"direction":"e"},
        "loot": {},
        "take": {"item":"gold", "quantity":2},
        "eat": {"item":"berry"},
        "attack": {"direction":"n"},
        "speak": {"message":"Hello nearby agents"},
        "whisper": {"target":"agent-2", "message":"private hello"},
        "drop": {"item":"berry"},
        "withdraw": {"amount":3},
        "respawn": {},
    }
    for action, params in valid.items():
        assert service._params(action, params) == params
    assert service.history() == []
    assert service.readiness()["remote_status"] == "unverified"
    assert service.readiness()["ready"] is False
    assert service.readiness()["base_url"] == API_BASE
