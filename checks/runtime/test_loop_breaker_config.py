import ast
import inspect
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.core.config.schema import build_json_schema
from gideon.interfaces.dashboard.handlers.core import (
    api_gideon_config,
    api_gideon_config_patch,
)
from gideon.security.guardrails.loop_breaker import LoopBreaker


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    path.write_text("{}")
    return path


def write_threshold(path, value):
    path.write_text(
        json.dumps({"guardrails": {"loop_breaker": {"circuit_threshold": value}}})
    )


@pytest.mark.parametrize(
    "value, expected", [(0, 1), (-7, 1), (1, 1), (42, 42), (None, 30), ("invalid", 30)]
)
def test_config_floor_and_round_trip(config_file, value, expected):
    write_threshold(config_file, value)
    config = AppConfig.load()
    assert config.guardrails.loop_breaker.circuit_threshold == expected
    config_file.write_text(json.dumps(config.to_dict()))
    assert AppConfig.load().guardrails.loop_breaker.circuit_threshold == expected


def test_default_and_schema(config_file):
    assert AppConfig.load().guardrails.loop_breaker.circuit_threshold == 30
    schema = build_json_schema(AppConfig)
    field = schema["properties"]["guardrails"]["properties"]["loop_breaker"][
        "properties"
    ]["circuit_threshold"]
    assert field["type"] == "integer"
    assert field["default"] == 30
    assert field["x-meta"]["label"]
    assert "Minimum 1" in field["x-meta"]["help"]


def test_lazy_per_run_cache_buckets_and_reset(config_file):
    write_threshold(config_file, 20)
    breaker = LoopBreaker()
    write_threshold(config_file, 2)
    breaker.record("a", True)
    breaker.record("b", True)
    assert not breaker.circuit_tripped()
    write_threshold(config_file, 50)
    breaker.record("a", False)
    assert breaker.count("a") == 0
    assert breaker.count("b") == 1
    assert breaker.total_failures == 2
    breaker.record("c", True)
    assert breaker.circuit_tripped()
    breaker.reset_structural()
    assert breaker.circuit_tripped()
    breaker.reset()
    assert breaker.total_failures == 0
    assert breaker.count("b") == 0
    write_threshold(config_file, 1)
    breaker.record("a", True)
    assert not breaker.circuit_tripped()
    breaker.record("b", True)
    assert breaker.circuit_tripped()
    assert not LoopBreaker().circuit_tripped()


@pytest.mark.asyncio
async def test_real_http_patch_get_persistence_and_rejected_floor(config_file):
    app = web.Application()
    app.router.add_get("/api/config/gideon", api_gideon_config)
    app.router.add_patch("/api/config/gideon", api_gideon_config_patch)
    async with TestClient(TestServer(app)) as client:
        for value in (1, 47):
            response = await client.patch(
                "/api/config/gideon",
                json={
                    "path": "guardrails.loop_breaker.circuit_threshold",
                    "value": value,
                },
            )
            assert response.status == 200, await response.text()
            response = await client.get("/api/config/gideon")
            assert response.status == 200
            assert (await response.json())["guardrails"]["loop_breaker"][
                "circuit_threshold"
            ] == value
            assert AppConfig.load().guardrails.loop_breaker.circuit_threshold == value
        saved = config_file.read_text()
        for value in (0, -1, "invalid"):
            response = await client.patch(
                "/api/config/gideon",
                json={
                    "path": "guardrails.loop_breaker.circuit_threshold",
                    "value": value,
                },
            )
            assert response.status == 400
            assert config_file.read_text() == saved


def test_acp_resets_only_at_outer_turn_boundary():
    from gideon.interfaces.dashboard.chat_runner import run_chat

    tree = ast.parse(inspect.getsource(run_chat))
    boundary = next(
        node
        for node in tree.body[0].body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "_prompt_depth == 0"
    )
    assert ast.unparse(boundary.body[0]) == "session._acp_breaker.reset()"
