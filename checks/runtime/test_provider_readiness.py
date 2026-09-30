"""Provider listings stay read-only; explicit tests require output to be ready."""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import sys

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.integrations.llm.registry import ProviderEntry


@pytest.fixture
def acp_entry():
    import gideon.integrations.llm as llm_package
    from gideon.engine.agents import registry as agent_registry
    from gideon.integrations.llm import acp_agent, registry as llm_registry
    from gideon.interfaces.dashboard.handlers import providers

    previous_registry = llm_registry._default_registry
    previous_module = sys.modules.get("gideon.integrations.llm.acp_agent")
    previous_package_attribute = getattr(llm_package, "acp_agent", None)
    previous_agents = dict(agent_registry._providers)
    original_pool = None
    try:
        from gideon.integrations.acp import connection_pool

        original_pool = connection_pool.get_acp_pool()
        connection_pool.set_acp_pool(None)
        llm_registry.reset_default_registry()
        acp_agent = importlib.reload(acp_agent)
        command = shutil.which("true")
        assert command
        entry = ProviderEntry(
            name="acp:read-only-check",
            type="acp_agent",
            model="provider-model",
            options={"command": [command], "runtime_id": "acp:read-only-check"},
            declared_capabilities=acp_agent.ACP_AGENT_CAPABILITY.capabilities,
        )
        llm_registry.get_default_registry().register_entry(entry)
        providers._readiness_cache.clear()
        yield entry
    finally:
        providers._readiness_cache.clear()
        llm_registry.set_default_registry(previous_registry)
        if previous_module is not None:
            sys.modules["gideon.integrations.llm.acp_agent"] = previous_module
            llm_package.acp_agent = previous_package_attribute
        else:
            sys.modules.pop("gideon.integrations.llm.acp_agent", None)
            if hasattr(llm_package, "acp_agent"):
                delattr(llm_package, "acp_agent")
        agent_registry._providers.clear()
        agent_registry._providers.update(previous_agents)
        if original_pool is not None:
            connection_pool.set_acp_pool(original_pool)


def _payload(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_listing_is_read_only_and_explicit_test_requires_output(acp_entry, monkeypatch):
    from gideon.interfaces.dashboard.handlers import providers
    from gideon.extensions.providers.connection import get_connection_board
    from gideon.integrations.llm.registry import get_default_registry

    listing_entry = ProviderEntry(
        name="read-only-model-listing",
        type="openai",
        model="listed-model",
    )
    get_default_registry().register_entry(listing_entry)
    board = get_connection_board()

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("provider listing must not launch a connection probe")

    monkeypatch.setattr(board, "read", unexpected_probe)
    model_listing = asyncio.run(
        providers.api_providers_list(make_mocked_request("GET", "/api/model-providers"))
    )
    row = next(
        item
        for item in _payload(model_listing)["providers"]
        if item["name"] == listing_entry.name
    )
    assert row["connection"]["state"] == "untested"

    get_request = make_mocked_request("GET", "/api/agent-providers")
    listed = asyncio.run(providers.api_agent_providers_list(get_request))
    row = next(
        item
        for item in _payload(listed)["agent_providers"]
        if item["provider_id"] == acp_entry.name
    )
    assert row["state"] == "untested"
    assert row["ready"] is False

    test_request = make_mocked_request(
        "POST",
        f"/api/model-providers/{acp_entry.name}/test",
        match_info={"name": acp_entry.name},
    )
    tested = asyncio.run(providers.api_provider_test(test_request))
    result = _payload(tested)
    assert result["ok"] is False
    assert result["model"] == acp_entry.model
    assert result["status"] != "ready"
    assert result["message"]

    relisted = asyncio.run(providers.api_agent_providers_list(get_request))
    updated = next(
        item
        for item in _payload(relisted)["agent_providers"]
        if item["provider_id"] == acp_entry.name
    )
    assert updated["ready"] is False
    assert updated["state"] != "untested"
