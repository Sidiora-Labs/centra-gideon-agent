import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import config_dir
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.integrations.mcp_core import (
    reset_current_session_key,
    set_current_session_key,
)
from gideon.interfaces.dashboard.handlers.capabilities_ownership import register
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.workspace.capabilities.platform.ownership import mutate, view
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = "/api/capabilities/platform/ownership"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    return HierarchyStore().create_project("Ownership project")


def application():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    return app


async def authenticate(client, actor="alice"):
    response = await client.get(PREFIX, params={"token": generate_token(actor)})
    assert response.status == 200
    data = await response.json()
    assert data["actor"] == "user:" + actor
    return data


def command(
    project, request="request-one", revision=0, action="claim", feature="editor"
):
    return {
        "project_id": project.id,
        "feature": feature,
        "action": action,
        "revision": revision,
        "request_id": request,
    }


@pytest.mark.asyncio
async def test_actual_project_ownership_persists_replays_and_owner_only_release(
    project,
):
    async with (
        TestClient(TestServer(application())) as alice,
        TestClient(TestServer(application())) as bob,
    ):
        empty = await authenticate(alice)
        await authenticate(bob, "bob")
        assert project.id in {item["id"] for item in empty["projects"]}
        assert empty["ownership"] == []
        request = command(project)
        claimed = await alice.post(PREFIX, json=request)
        assert claimed.status == 200
        row = (await claimed.json())["ownership"][0]
        assert row == {
            "project_id": project.id,
            "feature": "editor",
            "owner": "user:alice",
            "revision": 1,
        }
        replayed = await alice.post(PREFIX, json=request)
        assert replayed.status == 200
        assert (await replayed.json())["ownership"][0] == row
        other_actor_replay = await bob.post(PREFIX, json=request)
        assert other_actor_replay.status == 409
        occupied = await bob.post(PREFIX, json=command(project, "bob-claim", 1))
        assert occupied.status == 409
        forbidden_release = await bob.post(
            PREFIX, json=command(project, "bob-release", 1, "release")
        )
        assert forbidden_release.status == 403
        stale_release = await alice.post(
            PREFIX, json=command(project, "stale-release", 0, "release")
        )
        assert stale_release.status == 409
        released = await alice.post(
            PREFIX, json=command(project, "alice-release", 1, "release")
        )
        assert released.status == 200
        assert (await released.json())["ownership"][0]["owner"] is None
        handover = await bob.post(PREFIX, json=command(project, "bob-handover", 2))
        assert handover.status == 200
        assert (await handover.json())["ownership"][0]["owner"] == "user:bob"
    async with TestClient(TestServer(application())) as restarted:
        persisted = await authenticate(restarted, "bob")
        assert persisted["ownership"][0]["revision"] == 3
        assert persisted["ownership"][0]["owner"] == "user:bob"
        history = await restarted.get(
            PREFIX, params={"project_id": project.id, "feature": "editor"}
        )
        events = (await history.json())["history"]
        assert [event["action"] for event in events] == ["claim", "release", "claim"]
        assert [event["actor"] for event in events] == [
            "user:bob",
            "user:alice",
            "user:alice",
        ]
        assert [event["revision"] for event in events] == [3, 2, 1]
        assert all(event["at"] for event in events)
        assert "sqlite" not in json.dumps(persisted)
        assert HierarchyStore().get_project(project.id).name == "Ownership project"


@pytest.mark.asyncio
async def test_concurrent_real_http_claims_have_one_winner(project):
    async with (
        TestClient(TestServer(application())) as alice,
        TestClient(TestServer(application())) as bob,
    ):
        await authenticate(alice)
        await authenticate(bob, "bob")
        results = await asyncio.gather(
            alice.post(PREFIX, json=command(project, "alice-request")),
            bob.post(PREFIX, json=command(project, "bob-request")),
        )
        assert sorted(response.status for response in results) == [200, 409]
        current = view(project.id, "editor")
        assert len(current["ownership"]) == 1
        assert current["ownership"][0]["owner"] in {"user:alice", "user:bob"}
        assert current["ownership"][0]["revision"] == 1
        assert len(current["history"]) == 1
        assert current["history"][0]["actor"] == current["ownership"][0]["owner"]


@pytest.mark.asyncio
async def test_native_session_context_claim_release_and_manifest(project):
    provider = create_provider()
    definitions = await provider.list_tools()
    claim = next(tool for tool in definitions if tool.name == "platform_feature_claim")
    assert claim.requires_approval is True
    assert claim.risk_level.value == "caution"
    manifest = json.loads(
        Path(
            "runtime/gideon/extensions/apps/native/gideon-platform/app.json"
        ).read_text()
    )
    assert claim.name in manifest["provider"]["capabilities"]
    missing = set_current_session_key("")
    try:
        denied = await provider.invoke(claim.name, command(project))
        assert not denied.success
    finally:
        reset_current_session_key(missing)
    token = set_current_session_key("agent:feature-worker")
    try:
        result = await provider.invoke(claim.name, command(project, "native-request"))
        assert result.success
        assert json.loads(result.output)["owner"] == "session:agent:feature-worker"
        exact_replay = await provider.invoke(
            claim.name, command(project, "native-request")
        )
        assert exact_replay.output == result.output
        inventory = await provider.invoke("platform_feature_ownership", {})
        assert inventory.success
        assert json.loads(inventory.output)["ownership"][0]["revision"] == 1
        released = await provider.invoke(
            claim.name, command(project, "native-release", 1, "release")
        )
        assert released.success
        assert json.loads(released.output)["owner"] is None
        spoofed = await provider.invoke(
            claim.name,
            {**command(project, "spoof-request", 2), "actor": "administrator"},
        )
        assert not spoofed.success
        assert view()["ownership"][0]["revision"] == 2
    finally:
        reset_current_session_key(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"feature": ""},
        {"feature": "bad feature"},
        {"feature": "x" * 101},
        {"revision": True},
        {"revision": -1},
        {"revision": "0"},
        {"action": "transfer"},
        {"request_id": "x"},
        {"request_id": "../outside"},
        {"owner": "other-user"},
    ],
)
async def test_invalid_commands_cannot_create_claims(project, changes):
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        response = await client.post(PREFIX, json={**command(project), **changes})
        assert response.status == 400
        assert "error" in await response.json()
        assert (await authenticate(client))["ownership"] == []


@pytest.mark.asyncio
async def test_missing_project_replay_mismatch_scope_and_immutable_history(project):
    async with TestClient(TestServer(application())) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        app = await client.post(
            PREFIX,
            params={"token": generate_token("owner", app="third-party")},
            json=command(project),
        )
        assert app.status in {401, 403}
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        missing = await client.post(
            PREFIX, json={**command(project), "project_id": "p-does-not-exist"}
        )
        assert missing.status == 404
        accepted = await client.post(PREFIX, json=command(project))
        assert accepted.status == 200
        altered = await client.post(
            PREFIX, json=command(project, feature="other-feature")
        )
        assert altered.status == 409
        assert len(view()["ownership"]) == 1
        connection = sqlite3.connect(
            config_dir() / "capabilities/platform/feature_ownership.sqlite3"
        )
        try:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("DELETE FROM events")
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("UPDATE events SET actor='attacker'")
        finally:
            connection.close()
        assert len(view(project.id, "editor")["history"]) == 1
