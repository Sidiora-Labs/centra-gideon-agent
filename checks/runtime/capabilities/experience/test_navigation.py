import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.navigation import (
    NavigationReceipts,
    route,
)
from gideon.workspace.capabilities.experience.store import Conflict, NotFound
from gideon.workspace.capabilities.experience.tools import ExperienceTools


def command(**overrides):
    return dict(
        request_id="open",
        command="Open Projects",
        origin="chat",
        target="projects",
        input_origin="typed",
        **overrides,
    )


@pytest.fixture
def receipts(tmp_path):
    return NavigationReceipts(ExperienceStore(tmp_path))


def test_requested_receipt_is_not_browser_success_and_reloads(receipts, tmp_path):
    requested = receipts.request(command())
    assert requested["status"] == "requested"
    assert requested["revision"] == 1
    assert requested["observed_route"] is None
    assert requested["applied_at"] is None
    assert datetime.fromisoformat(requested["created_at"]).tzinfo is not None
    restarted = NavigationReceipts(ExperienceStore(tmp_path))
    assert restarted.get(requested["id"]) == requested
    assert restarted.list() == [requested]
    assert restarted.request(command()) == requested
    applied = restarted.acknowledge(
        requested["id"], {"revision": 1, "observed_route": "projects"}
    )
    assert applied["status"] == "applied"
    assert applied["observed_route"] == "projects"
    assert applied["revision"] == 2
    assert datetime.fromisoformat(applied["applied_at"]) >= datetime.fromisoformat(
        applied["created_at"]
    )
    assert receipts.get(requested["id"]) == applied
    assert receipts.request(command()) == applied
    assert (
        receipts.acknowledge(
            requested["id"], {"revision": 1, "observed_route": "projects"}
        )
        == applied
    )


def test_wrong_acknowledgement_and_request_reuse_preserve_requested(receipts):
    requested = receipts.request(command())
    for body in [
        {"revision": 1, "observed_route": "chat"},
        {"revision": 2, "observed_route": "projects"},
    ]:
        with pytest.raises(Conflict):
            receipts.acknowledge(requested["id"], body)
        assert receipts.get(requested["id"]) == requested
    with pytest.raises(Conflict):
        receipts.request({**command(), "target": "settings"})
    assert receipts.list() == [requested]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/projects",
        "https://example.com",
        "../settings",
        "app//one",
        "app/",
        "#projects",
        "a" * 201,
        None,
        12,
    ],
)
def test_route_refuses_external_or_malformed_destinations(value):
    with pytest.raises(ValueError):
        route(value)


@pytest.mark.parametrize(
    "patch",
    [
        {"input_origin": "provider"},
        {"command": ""},
        {"command": "x" * 401},
        {"request_id": "../oops"},
        {"provider": "managed"},
        {"home": "/tmp/elsewhere"},
    ],
)
def test_untrusted_payload_rejected_without_receipt(receipts, patch):
    with pytest.raises(ValueError):
        receipts.request({**command(), **patch})
    assert receipts.list() == []


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"revision": True, "observed_route": "projects"},
        {"revision": 1, "observed_route": "projects", "status": "applied"},
    ],
)
def test_invalid_ack_is_not_success(receipts, body):
    requested = receipts.request(command())
    with pytest.raises(ValueError):
        receipts.acknowledge(requested["id"], body)
    assert receipts.get(requested["id"])["status"] == "requested"


def test_parallel_retries_create_one_receipt(receipts):
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: receipts.request(command()), range(8)))
    assert all(item == result[0] for item in result)
    assert len(receipts.list()) == 1


def test_distinct_homes_and_missing_receipts(receipts, tmp_path):
    original = receipts.request(command())
    other = NavigationReceipts(ExperienceStore(tmp_path / "other"))
    assert other.list() == []
    with pytest.raises(NotFound):
        other.get(original["id"])
    with pytest.raises(NotFound):
        other.acknowledge(original["id"], {"revision": 1, "observed_route": "projects"})
    assert other.request(command())["id"] != original["id"]


@pytest.mark.asyncio
async def test_native_tools_only_read_browser_receipts(receipts):
    provider = ExperienceTools(receipts.store)
    definitions = await provider.list_tools()
    names = {item.name for item in definitions if "navigation" in item.name}
    assert names == {"experience_navigation_list", "experience_navigation_get"}
    requested = receipts.request(command())
    listed = await provider.invoke("experience_navigation_list", {})
    assert listed.success
    assert json.loads(listed.output) == {"receipts": [requested]}
    result = await provider.invoke("experience_navigation_get", {"id": requested["id"]})
    assert result.success
    assert json.loads(result.output)["receipt"]["status"] == "requested"
    rejected = await provider.invoke(
        "experience_navigation_acknowledge", {"id": requested["id"]}
    )
    assert not rejected.success
    assert receipts.get(requested["id"]) == requested


@pytest.mark.asyncio
async def test_real_http_request_acknowledgement_and_errors(receipts):
    app = web.Application()
    app[STORE] = receipts.store
    register(app)
    async with TestClient(TestServer(app)) as client:
        prefix = "/api/capabilities/experience/navigation"
        created = await client.post(prefix, json=command())
        assert created.status == 201
        requested = (await created.json())["receipt"]
        assert requested["status"] == "requested"
        detail = prefix + "/" + requested["id"]
        assert (await (await client.get(detail)).json()) == {"receipt": requested}
        wrong = await client.post(
            detail + "/acknowledge", json={"revision": 1, "observed_route": "chat"}
        )
        assert wrong.status == 409
        assert "acknowledged" in (await wrong.json())["error"]
        ack = await client.post(
            detail + "/acknowledge", json={"revision": 1, "observed_route": "projects"}
        )
        assert ack.status == 200
        applied = (await ack.json())["receipt"]
        assert applied["status"] == "applied"
        assert (await (await client.get(prefix)).json()) == {"receipts": [applied]}
        assert (await client.get(prefix + "/missing")).status == 404
        assert (await client.post(prefix, json=[])).status == 400
        assert (
            await client.post(detail + "/acknowledge", json={"revision": 1})
        ).status == 400
        assert (
            await client.post(prefix, json={**command(), "target": "settings"})
        ).status == 409
