from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.interfaces.dashboard.handlers import triggers as handlers


@pytest.fixture
def api(tmp_path, monkeypatch):
    store = TriggerStore(base_dir=tmp_path)
    monkeypatch.setattr(handlers, "_trigger_store", lambda: store)
    monkeypatch.setattr(handlers, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(handlers, "_sel", lambda: MagicMock())
    return store, MagicMock()


def request(method, state, body, trigger_id=""):
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(
        method,
        "/api/triggers" + (f"/{trigger_id}" if trigger_id else ""),
        match_info={"id": trigger_id} if trigger_id else {},
        app=app,
        headers={"Content-Type": "application/json"},
    )
    req["user"] = "tester"
    req._read_bytes = json.dumps(body).encode()
    return req


def body(response):
    return json.loads(response.body.decode())


def test_create_persists_and_returns_failure_controls(api):
    store, state = api
    response = asyncio.run(
        handlers.api_trigger_create(
            request(
                "POST",
                state,
                {
                    "trigger_type": "schedule",
                    "name": "Nightly",
                    "cron": "0 9 * * *",
                    "action": {"provider": "notify", "config": {}},
                    "failure_delivery": "channel:C123",
                    "failure_policy": {"dedupe_hash": True},
                },
            )
        )
    )
    assert response.status == 200
    trigger = store.get("clock:nightly").trigger
    assert trigger.failure_delivery == "channel:C123"
    assert trigger.failure_policy == {"dedupe_hash": True}
    assert body(response)["trigger"]["failure_policy"] == {"dedupe_hash": True}


def test_update_merges_dedupe_without_losing_the_autopause_budget(api):
    store, state = api
    store.upsert(
        Trigger(
            id="file:notes",
            name="Notes",
            kind="file",
            spec={"paths": ["notes/**"]},
            workflow={"provider": "notify", "config": {}},
            failure_policy={"autopause_after": 2, "dedupe_hash": False},
        )
    )
    response = asyncio.run(
        handlers.api_trigger_detail(
            request(
                "PUT",
                state,
                {
                    "delivery": "none",
                    "failure_delivery": "inbox",
                    "failure_policy": {"dedupe_hash": True},
                },
                "store:file:notes",
            )
        )
    )
    assert response.status == 200
    trigger = store.get("file:notes").trigger
    assert trigger.failure_policy == {"autopause_after": 2, "dedupe_hash": True}
    assert trigger.delivery == "none" and trigger.failure_delivery == "inbox"


@pytest.mark.parametrize("route", ["email", "channel:", "channel:bad route"])
def test_invalid_outcome_routes_are_refused_without_mutating(api, route):
    store, state = api
    store.upsert(
        Trigger(
            id="file:notes",
            name="Notes",
            kind="file",
            spec={"paths": ["notes/**"]},
        )
    )
    response = asyncio.run(
        handlers.api_trigger_detail(
            request("PUT", state, {"failure_delivery": route}, "store:file:notes")
        )
    )
    assert response.status == 400
    assert store.get("file:notes").trigger.failure_delivery == "inbox"
