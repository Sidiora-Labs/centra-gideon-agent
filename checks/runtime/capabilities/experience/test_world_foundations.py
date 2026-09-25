import asyncio
import json
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.apps.backend_runtime import get_backend_supervisor
from gideon.workspace.capabilities.experience import ExperienceStore, Conflict
from gideon.workspace.capabilities.experience.world_engine import APP_ID, WorldEngine
from gideon.workspace.capabilities.experience.world_foundations import WorldFoundations
from gideon.workspace.capabilities.experience.world_foundations_http import register_world_foundations
from gideon.workspace.capabilities.experience.world_foundations_provider import WorldFoundationTools
from gideon.workspace.capabilities.experience.worlds import get_worlds
from test_world_engine import DEPS, install_engine


@pytest.fixture
async def foundations(tmp_path, monkeypatch):
    home = tmp_path / "origin"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("PATH", str(DEPS / "bun-linux-x64") + os.pathsep + os.environ["PATH"])
    store = ExperienceStore(home)
    install_engine()
    engine = WorldEngine(store)
    state = await engine.control("start", {})
    for _ in range(50):
        if state["state"] == "running":
            break
        await asyncio.sleep(.1)
        state = await engine.status()
    assert state["state"] == "running"
    worlds = get_worlds(store)
    service = WorldFoundations(store, worlds)
    yield service, engine, worlds, tmp_path, monkeypatch
    await worlds.close()
    get_backend_supervisor().stop(APP_ID)


def draft(service):
    return service.record({"title":"Welcome beacon", "controller":{"kind":"ambient_beacon", "position":[2, 0, -3]},
                           "style":{"color":"violet", "private_notes":"owner only"}})


def promote(service):
    record = draft(service)
    record = service.package(record["id"], record["revision"])
    return service.promote(record["id"], record["revision"])


@pytest.mark.asyncio
async def test_real_controller_lifecycle_survives_engine_stop_and_restart(foundations):
    service, engine, worlds, _tmp_path, _monkeypatch = foundations
    foundation = promote(service)
    controller = await service.install_controller({"foundation_id":foundation["id"], "world":"controller_lounge"})
    assert controller["state"] == "installed" and controller["desired_state"] == "stopped"
    assert (await worlds.call("controller_lounge"))["state"]["entities"] == {}
    controller = await service.control(controller["id"], "arm", controller["revision"])
    assert controller["state"] == "armed" and controller["last_receipt"]["complete"]
    entity = (await worlds.call("controller_lounge"))["state"]["entities"][controller["entity_id"]]
    assert entity["pos"] == [2, 0, -3]
    assert entity["comp"]["gideon_controller"] == {"controller_id":controller["id"], "kind":"ambient_beacon", "foundation_id":foundation["id"]}

    assert (await engine.control("stop", {}))["state"] == "stopped"
    assert (await engine.control("start", {}))["state"] == "running"
    restarted = WorldFoundations(ExperienceStore(service.store.path.parent.parent), worlds)
    receipt = await restarted.reconcile()
    assert receipt["reconciled"][0]["desired_state"] == "armed"
    canonical = await worlds.call("controller_lounge")
    assert canonical["state"]["entities"][controller["entity_id"]]["comp"]["gideon_controller"]["controller_id"] == controller["id"]

    current = restarted.list()["controllers"][0]
    current = await restarted.control(current["id"], "restart", current["revision"])
    assert current["last_receipt"]["complete"]
    current = await restarted.control(current["id"], "stop", current["revision"])
    assert current["state"] == "stopped" and current["desired_state"] == "stopped"
    assert current["entity_id"] is None
    assert (await worlds.call("controller_lounge"))["state"]["entities"] == {}
    persisted = WorldFoundations(ExperienceStore(service.store.path.parent.parent), worlds).list()["controllers"][0]
    assert persisted["state"] == "stopped" and persisted["last_receipt"]["complete"]


@pytest.mark.asyncio
async def test_candidate_inherit_adopt_withdraw_and_private_style_boundary(foundations):
    service, _engine, worlds, tmp_path, monkeypatch = foundations
    foundation = promote(service)
    envelope = service.envelope(foundation["id"])
    encoded = json.dumps(envelope)
    assert "private_notes" not in encoded and "violet" not in encoded and "style" not in encoded
    assert envelope["candidate"]["controller"]["kind"] == "ambient_beacon"

    receiver_home = tmp_path / "receiver"
    receiver = WorldFoundations(ExperienceStore(receiver_home), worlds)
    inherited = receiver.inherit(envelope)
    assert inherited["state"] == "inherited" and inherited["style"] is None
    assert inherited["provenance"] == {"kind":"inherited", "origin_instance":service.instance_id, "foundation_id":foundation["id"]}
    adopted = receiver.adopt(inherited["id"], inherited["revision"])
    assert adopted["state"] == "adopted"
    with pytest.raises(Conflict, match="fingerprint"):
        receiver.inherit({**envelope, "fingerprint":"0" * 64})
    withdrawal = service.withdraw(foundation["id"], foundation["revision"])
    received = receiver.apply_withdrawal(withdrawal)
    assert received["state"] == "withdrawn"
    with pytest.raises(Conflict, match="promoted or adopted"):
        await receiver.install_controller({"foundation_id":received["id"], "world":"controller_lounge"})


@pytest.mark.asyncio
async def test_http_and_native_permissions_operate_real_controller(foundations):
    service, _engine, worlds, _tmp_path, _monkeypatch = foundations
    foundation = promote(service)
    app = web.Application()
    register_world_foundations(app, service.store)
    async with TestClient(TestServer(app)) as client:
        snapshot = await client.get("/api/capabilities/experience/world-foundations")
        assert snapshot.status == 200
        assert (await snapshot.json())["foundations"][0]["state"] == "promoted"
        installed = await client.post("/api/capabilities/experience/world-foundations/controllers/install", json={"foundation_id":foundation["id"], "world":"http_world"})
        assert installed.status == 200
        controller = (await installed.json())["controller"]
        armed = await client.post(f"/api/capabilities/experience/world-foundations/controllers/{controller['id']}/arm", json={"revision":controller["revision"]})
        assert armed.status == 200
        controller = (await armed.json())["controller"]
        assert controller["last_receipt"]["complete"]
        stale = await client.post(f"/api/capabilities/experience/world-foundations/controllers/{controller['id']}/stop", json={"revision":1})
        assert stale.status == 409
        invalid = await client.post("/api/capabilities/experience/world-foundations/record", json={"title":"Unsafe", "controller":{"kind":"arbitrary_script", "position":[0,0,0]}, "style":{}})
        assert invalid.status == 400

    provider = WorldFoundationTools(service.store)
    definitions = {item.name:item for item in await provider.list_tools()}
    assert not definitions["experience_foundations_get"].requires_approval
    assert definitions["experience_controller_install"].requires_approval
    assert definitions["experience_controller_control"].requires_approval
    installed = await provider.invoke("experience_controller_install", {"foundation_id":foundation["id"], "world":"native_world"})
    assert installed.success
    controller = json.loads(installed.output)["controller"]
    armed = await provider.invoke("experience_controller_control", {"id":controller["id"], "operation":"arm", "revision":controller["revision"]})
    assert armed.success
    assert (await worlds.call("native_world"))["state"]["entities"]
    refused = await provider.invoke("experience_controller_control", {"id":controller["id"], "operation":"exec", "revision":2})
    assert not refused.success


@pytest.mark.asyncio
async def test_validation_revision_and_lifecycle_fail_closed(foundations):
    service, _engine, worlds, _tmp_path, _monkeypatch = foundations
    for body in (None, [], {}, {"title":"x", "controller":{"kind":"ambient_beacon", "position":[0,0,0]}, "style":{}, "extra":True},
                 {"title":"x", "controller":{"kind":"other", "position":[0,0,0]}, "style":{}},
                 {"title":"x", "controller":{"kind":"ambient_beacon", "position":[0,0]}, "style":{}},
                 {"title":"x", "controller":{"kind":"ambient_beacon", "position":[True,0,0]}, "style":{}}):
        with pytest.raises((ValueError, TypeError)):
            service.record(body)
    record = draft(service)
    with pytest.raises(Conflict, match="revision"):
        service.package(record["id"], record["revision"] + 1)
    with pytest.raises(Conflict, match="promoted or adopted"):
        await service.install_controller({"foundation_id":record["id"], "world":"closed"})
    record = service.package(record["id"], record["revision"])
    with pytest.raises(Conflict, match="packaged"):
        service.package(record["id"], record["revision"])
    record = service.promote(record["id"], record["revision"])
    controller = await service.install_controller({"foundation_id":record["id"], "world":"closed"})
    with pytest.raises(Conflict, match="revision"):
        await service.control(controller["id"], "arm", 99)
    assert (await worlds.call("closed"))["state"]["entities"] == {}


@pytest.mark.asyncio
async def test_http_exposes_complete_foundation_candidate_lifecycle(foundations):
    service, _engine, _worlds, _tmp_path, _monkeypatch = foundations
    app = web.Application()
    register_world_foundations(app, service.store)
    async with TestClient(TestServer(app)) as client:
        created_response = await client.post("/api/capabilities/experience/world-foundations/record", json={
            "title":"Shared beacon",
            "controller":{"kind":"ambient_beacon", "position":[1, 2, 3]},
            "style":{"theme":"private"},
        })
        assert created_response.status == 200
        created = (await created_response.json())["foundation"]
        assert created["state"] == "draft"
        assert created["revision"] == 1
        assert created["candidate"] is None
        assert created["fingerprint"] is None
        packaged_response = await client.post(
            f"/api/capabilities/experience/world-foundations/{created['id']}/package",
            json={"revision":created["revision"]},
        )
        assert packaged_response.status == 200
        packaged = (await packaged_response.json())["foundation"]
        assert packaged["state"] == "packaged"
        assert packaged["revision"] == 2
        assert len(packaged["fingerprint"]) == 64
        assert packaged["candidate"]["title"] == "Shared beacon"
        assert "style" not in packaged["candidate"]
        stale_response = await client.post(
            f"/api/capabilities/experience/world-foundations/{created['id']}/promote",
            json={"revision":1},
        )
        assert stale_response.status == 409
        promoted_response = await client.post(
            f"/api/capabilities/experience/world-foundations/{created['id']}/promote",
            json={"revision":packaged["revision"]},
        )
        assert promoted_response.status == 200
        promoted = (await promoted_response.json())["foundation"]
        assert promoted["state"] == "promoted"
        envelope_response = await client.get(
            f"/api/capabilities/experience/world-foundations/{created['id']}/envelope"
        )
        assert envelope_response.status == 200
        envelope = await envelope_response.json()
        assert envelope["fingerprint"] == promoted["fingerprint"]
        assert envelope["candidate"] == promoted["candidate"]
        assert "private" not in json.dumps(envelope)
        withdrawn_response = await client.post(
            f"/api/capabilities/experience/world-foundations/{created['id']}/withdraw",
            json={"revision":promoted["revision"]},
        )
        assert withdrawn_response.status == 200
        withdrawal = (await withdrawn_response.json())["withdrawal"]
        assert withdrawal["foundation_id"] == created["id"]
        assert withdrawal["origin_instance"] == service.instance_id
        assert withdrawal["fingerprint"] == promoted["fingerprint"]


@pytest.mark.asyncio
async def test_retired_controller_is_removed_and_not_reconciled(foundations):
    service, _engine, worlds, _tmp_path, _monkeypatch = foundations
    foundation = promote(service)
    controller = await service.install_controller({
        "foundation_id":foundation["id"],
        "world":"retirement_world",
    })
    controller = await service.control(
        controller["id"],
        "arm",
        controller["revision"],
    )
    entity_id = controller["entity_id"]
    before = await worlds.call("retirement_world")
    assert entity_id in before["state"]["entities"]
    retired = await service.control(
        controller["id"],
        "retire",
        controller["revision"],
    )
    assert retired["state"] == "retired"
    assert retired["desired_state"] == "retired"
    assert retired["entity_id"] is None
    assert retired["last_receipt"]["complete"] is True
    after = await worlds.call("retirement_world")
    assert entity_id not in after["state"]["entities"]
    reconciliation = await service.reconcile()
    assert reconciliation == {"reconciled":[]}
    final = service.list()["controllers"][0]
    assert final["state"] == "retired"
    assert final["revision"] == retired["revision"]
    with pytest.raises(ValueError, match="Unknown"):
        await service.control(final["id"], "execute", final["revision"])


@pytest.mark.asyncio
async def test_packaged_candidate_tamper_never_promotes_or_installs(foundations):
    service, _engine, worlds, _tmp_path, _monkeypatch = foundations
    record = draft(service)
    packaged = service.package(record["id"], record["revision"])
    tampered = dict(packaged)
    tampered["candidate"] = dict(packaged["candidate"])
    tampered["candidate"]["title"] = "Tampered title"
    with service.store.connection() as database:
        database.execute(
            "UPDATE world_foundations SET body=? WHERE id=?",
            (json.dumps(tampered), packaged["id"]),
        )
    with pytest.raises(Conflict, match="intact"):
        service.promote(packaged["id"], packaged["revision"])
    with pytest.raises(Conflict, match="promoted"):
        service.envelope(packaged["id"])
    with pytest.raises(Conflict, match="promoted"):
        await service.install_controller({
            "foundation_id":packaged["id"],
            "world":"tamper_world",
        })
    with pytest.raises(Exception):
        await worlds.call("tamper_world")


@pytest.mark.asyncio
async def test_inherited_provenance_and_withdrawal_must_match_exactly(foundations):
    service, _engine, worlds, tmp_path, _monkeypatch = foundations
    promoted = promote(service)
    envelope = service.envelope(promoted["id"])
    receiver = WorldFoundations(ExperienceStore(tmp_path / "isolated_receiver"), worlds)
    inherited = receiver.inherit(envelope)
    assert inherited["candidate"]["foundation_id"] == promoted["id"]
    assert inherited["candidate"]["origin_instance"] == service.instance_id
    assert inherited["fingerprint"] == promoted["fingerprint"]
    with pytest.raises(Exception, match="not found"):
        receiver.adopt(promoted["id"], promoted["revision"])
    adopted = receiver.adopt(inherited["id"], inherited["revision"])
    assert adopted["revision"] == 2
    assert adopted["provenance"] == inherited["provenance"]
    withdrawal = service.withdraw(promoted["id"], promoted["revision"])
    wrong_origin = dict(withdrawal)
    wrong_origin["origin_instance"] = "0" * 24
    with pytest.raises(Exception, match="not found"):
        receiver.apply_withdrawal(wrong_origin)
    wrong_fingerprint = dict(withdrawal)
    wrong_fingerprint["fingerprint"] = "0" * 64
    with pytest.raises(Conflict, match="does not match"):
        receiver.apply_withdrawal(wrong_fingerprint)
    unchanged = receiver.list()["foundations"][0]
    assert unchanged["state"] == "adopted"
    withdrawn = receiver.apply_withdrawal(withdrawal)
    assert withdrawn["state"] == "withdrawn"
    assert withdrawn["revision"] == 3
    assert withdrawn["provenance"] == inherited["provenance"]


@pytest.mark.asyncio
async def test_native_provider_rejects_undeclared_and_malformed_operations(foundations):
    service, _engine, _worlds, _tmp_path, _monkeypatch = foundations
    provider = WorldFoundationTools(service.store)
    listed = await provider.list_tools()
    names = [definition.name for definition in listed]
    assert names == [
        "experience_foundations_get",
        "experience_controller_install",
        "experience_controller_control",
    ]
    snapshot = await provider.invoke("experience_foundations_get", {})
    assert snapshot.success
    parsed = json.loads(snapshot.output)
    assert parsed["foundations"] == []
    assert parsed["controllers"] == []
    assert parsed["instance_id"] == service.instance_id
    malformed_get = await provider.invoke("experience_foundations_get", {"extra":True})
    assert not malformed_get.success
    unknown = await provider.invoke("experience_foundation_exec", {})
    assert not unknown.success
    missing_world = await provider.invoke("experience_controller_install", {"foundation_id":"missing"})
    assert not missing_world.success
    malformed_control = await provider.invoke("experience_controller_control", {"id":"missing", "operation":"arm"})
    assert not malformed_control.success
