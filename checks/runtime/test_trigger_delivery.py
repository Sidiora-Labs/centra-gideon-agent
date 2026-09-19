from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.automation.triggers.delivery import route_for
from gideon.automation.triggers.models import Trigger, parse_trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers import triggers as handlers
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(handlers, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        handlers, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path)
    )
    return tmp_path


@pytest.fixture
def state(home):
    config = AppConfig()
    return ConsoleState(ConversationDirectory(config), time.time())


def _request(method: str, state, body: dict, *, trigger_id: str = ""):
    app = web.Application()
    app["state"] = state
    request = make_mocked_request(
        method,
        "/api/triggers",
        match_info={"id": trigger_id} if trigger_id else {},
        app=app,
    )

    async def read_json():
        return body

    request.json = read_json  # type: ignore[assignment]
    return request


def _body(response) -> dict:
    return json.loads(response.body.decode())


def _create(state, **settings):
    body = {
        "trigger_type": "schedule",
        "name": "Nightly",
        "cron": "0 9 * * *",
        "action": {"provider": "bash", "config": {"command": "echo ok"}},
        **settings,
    }
    return asyncio.run(handlers.api_trigger_create(_request("POST", state, body)))


def test_explicit_empty_failure_delivery_survives_decode_and_inherits_delivery():
    raw = Trigger(
        id="clock:nightly",
        name="Nightly",
        kind="clock",
        delivery="channel:ops",
        failure_delivery="",
    ).to_dict()

    trigger, issues = parse_trigger(raw)

    assert not [issue for issue in issues if issue.severity == "error"]
    assert trigger.failure_delivery == ""
    assert route_for(trigger, ok=False) == "channel:ops"


def test_missing_failure_delivery_keeps_the_inbox_default():
    trigger, _issues = parse_trigger(
        {"id": "clock:nightly", "name": "Nightly", "kind": "clock"}
    )
    assert trigger.failure_delivery == "inbox"


def test_create_writes_empty_inheritance_and_failure_dedupe(home, state):
    response = _create(state, failure_delivery="", dedupe_hash=True)

    assert response.status == 200
    stored = TriggerStore(base_dir=home).get("clock:nightly").trigger
    assert stored.failure_delivery == ""
    assert stored.failure_policy["dedupe_hash"] is True
    assert _body(response)["trigger"]["failure_delivery"] == ""
    assert _body(response)["trigger"]["dedupe_hash"] is True


def test_update_merges_dedupe_hash_without_losing_other_failure_policy(home, state):
    _create(state, dedupe_hash=True)
    store = TriggerStore(base_dir=home)
    trigger = store.get("clock:nightly").trigger
    trigger.failure_policy["autopause_after"] = 4
    store.upsert(trigger)

    response = asyncio.run(
        handlers.api_trigger_detail(
            _request(
                "PUT",
                state,
                {"failure_delivery": "", "dedupe_hash": False},
                trigger_id="schedule:clock:nightly",
            )
        )
    )

    assert response.status == 200
    stored = store.get("clock:nightly").trigger
    assert stored.failure_delivery == ""
    assert stored.failure_policy == {"dedupe_hash": False, "autopause_after": 4}
