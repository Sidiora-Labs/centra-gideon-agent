"""E2-P4.5: native default agent seed + default-name resolution."""

from __future__ import annotations

import json as _json

import pytest
from aiohttp.test_utils import make_mocked_request

import gideon.core.config.loader as _loader
import gideon.interfaces.dashboard.handlers as _handlers
import gideon.interfaces.dashboard.handlers.agents as _agents_h
from gideon.engine.agents.defaults import (
    DEFAULT_NATIVE_AGENT_NAME,
    default_agent_name,
    make_default_native_profile,
    normalize_agent_name,
)


def test_default_native_profile_is_native():
    from gideon.core.config.loader import AgentProfile

    prof = make_default_native_profile(AgentProfile)
    assert prof.provider == "native"
    assert prof.source == "builtin"
    assert prof.system_prompt
    assert prof.model == ""


def test_global_agent_provider_defaults_native():
    from gideon.core.config.loader import AgentConfig

    assert AgentConfig().provider == "native"


def test_agent_profile_has_provider_field():
    from gideon.core.config.loader import AgentProfile

    assert AgentProfile().provider == ""
    assert AgentProfile(provider="acp:claude").provider == "acp:claude"


class _Cfg:
    def __init__(self, default_agent=""):
        self.default_agent = default_agent


def test_default_agent_name_falls_back_to_native():
    assert default_agent_name(_Cfg()) == DEFAULT_NATIVE_AGENT_NAME
    assert default_agent_name(_Cfg(default_agent="MyAgent")) == "MyAgent"


def test_normalize_agent_name_canonicalizes_default():
    assert normalize_agent_name(None) == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("") == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("gideon") == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("Gideon") == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("  gideon  ") == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("MyCustomAgent") == "MyCustomAgent"


def _acfg(monkeypatch, tmp_path, agents: dict):
    """A config.json with the given agents dict; point both config_path seams at it."""
    cfg = tmp_path / "config.json"
    cfg.write_text(
        _json.dumps({"agents": {k: {} for k in agents}, "default_agent": "default"})
    )
    monkeypatch.setattr(_loader, "config_path", lambda: cfg)
    monkeypatch.setattr(_loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(_handlers, "config_path", lambda: cfg, raising=False)
    return cfg


def _put(body):
    req = make_mocked_request("PUT", "/api/config/default-agent")

    async def _j():
        return body

    req.json = _j
    return req


@pytest.mark.asyncio
async def test_default_agent_rejects_unknown(monkeypatch, tmp_path):
    cfg = _acfg(monkeypatch, tmp_path, {"default": {}, "my-writer": {}})
    resp = await _agents_h.api_default_agent(_put({"agent": "no-such-agent-xyz"}))
    assert resp.status == 400
    assert "Unknown agent" in _json.loads(resp.body.decode())["error"]
    assert _json.loads(cfg.read_text())["default_agent"] == "default"


@pytest.mark.asyncio
async def test_default_agent_accepts_known(monkeypatch, tmp_path):
    cfg = _acfg(monkeypatch, tmp_path, {"default": {}, "my-writer": {}})
    resp = await _agents_h.api_default_agent(_put({"agent": "my-writer"}))
    assert resp.status == 200
    assert _json.loads(resp.body.decode())["default_agent"] == "my-writer"
    assert _json.loads(cfg.read_text())["default_agent"] == "my-writer"


@pytest.mark.asyncio
async def test_default_agent_empty_allowed(monkeypatch, tmp_path):
    _acfg(monkeypatch, tmp_path, {"default": {}})
    resp = await _agents_h.api_default_agent(_put({"agent": ""}))
    assert resp.status == 200


@pytest.mark.asyncio
async def test_default_agent_missing_key_is_400_not_silent_clear(monkeypatch, tmp_path):
    """S05 C11 regression: a body WITHOUT 'agent' (typo'd key, wrong contract —
    e.g. {"name": "..."}) used to coerce to "" and silently CLEAR the default
    agent while returning ok:true. Missing key must be a 400; reset stays the
    explicit {"agent": ""}."""
    cfg = _acfg(monkeypatch, tmp_path, {"default": {}, "my-writer": {}})
    resp = await _agents_h.api_default_agent(_put({"name": "my-writer"}))
    assert resp.status == 400
    assert _json.loads(cfg.read_text())["default_agent"] == "default"


def test_is_reserved_agent_case_insensitive():
    from gideon.engine.agents.defaults import LOOP_WORKER_AGENT_NAME, is_reserved_agent

    reserved = LOOP_WORKER_AGENT_NAME
    assert is_reserved_agent(reserved)
    assert is_reserved_agent(reserved.upper())
    assert is_reserved_agent(reserved.lower())
    assert not is_reserved_agent("my-custom-agent")


@pytest.mark.asyncio
async def test_api_agents_create_rejects_non_alphanumeric(monkeypatch, tmp_path):
    _acfg(monkeypatch, tmp_path, {"default": {}})
    req = make_mocked_request("POST", "/api/agents")

    async def _j():
        return {"name": "---"}

    req.json = _j
    resp = await _agents_h.api_gideon_agents_create(req)
    assert resp.status == 400
    assert "Agent name must match" in _json.loads(resp.body.decode())["error"]


@pytest.mark.asyncio
async def test_api_agents_create_case_insensitive_conflict(monkeypatch, tmp_path):
    from gideon.engine.agents.defaults import LOOP_WORKER_AGENT_NAME

    _acfg(monkeypatch, tmp_path, {LOOP_WORKER_AGENT_NAME: {}})
    req = make_mocked_request("POST", "/api/agents")

    async def _j():
        return {"name": LOOP_WORKER_AGENT_NAME.upper()}

    req.json = _j
    resp = await _agents_h.api_gideon_agents_create(req)
    assert resp.status == 409
    assert "already exists" in _json.loads(resp.body.decode())["error"]


@pytest.mark.asyncio
async def test_api_agents_create_lowercases_name(monkeypatch, tmp_path):
    cfg = _acfg(monkeypatch, tmp_path, {})
    req = make_mocked_request("POST", "/api/agents")

    async def _j():
        return {"name": "MyNewAgent"}

    req.json = _j
    resp = await _agents_h.api_gideon_agents_create(req)
    assert resp.status == 200
    assert "mynewagent" in _json.loads(cfg.read_text())["agents"]
