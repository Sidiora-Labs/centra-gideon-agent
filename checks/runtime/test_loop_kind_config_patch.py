from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.automation.loop import store
from gideon.automation.loop.loop import Loop

_HANDLER_PATH = (
    Path(__file__).parents[2]
    / "runtime/gideon/interfaces/dashboard/handlers/loop_routes.py"
)
_SPEC = importlib.util.spec_from_file_location("task26_loop_routes", _HANDLER_PATH)
assert _SPEC and _SPEC.loader
_HANDLERS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HANDLERS)


@pytest.fixture
def _isolate_trigger_store():
    yield


def test_put_merges_kind_config_over_unredacted_stored_config(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.automation.loop.files.config_dir", lambda: tmp_path)
    created = store.create(
        Loop(
            id="",
            name="Research",
            kind="research",
            task="research practical database migration strategies",
            kind_config={
                "granularity": "balanced",
                "subtopics": ["backfills", "cutover"],
                "output_template": "Use AKIAIOSFODNN7EXAMPLE in the appendix",
            },
        )
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(store.get_redacted(created.id))

    raw = json.dumps({"kind_config": {"granularity": "exhaustive"}}).encode()
    app = web.Application()
    app["state"] = object()
    request = make_mocked_request(
        "PUT",
        f"/api/loops/{created.id}",
        match_info={"id": created.id},
        app=app,
        headers={"Content-Type": "application/json"},
    )
    request._read_bytes = raw
    response = asyncio.run(_HANDLERS.api_loop_update(request))

    assert response.status == 200
    assert store.get(created.id).kind_config == {
        "granularity": "exhaustive",
        "subtopics": ["backfills", "cutover"],
        "output_template": "Use AKIAIOSFODNN7EXAMPLE in the appendix",
    }
