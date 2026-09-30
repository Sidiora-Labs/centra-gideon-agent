"""A real executable sentinel proves startup and read paths stay process-free."""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.integrations.llm.registry import ProviderEntry, get_default_registry, reset_default_registry


@pytest.fixture
def acp_registry():
    import sys

    import gideon.integrations.llm as llm_package
    from gideon.engine.agents import registry as agent_registry
    from gideon.integrations.llm import registry as model_registry

    old_registry = model_registry._default_registry
    old_module = sys.modules.get("gideon.integrations.llm.acp_agent")
    old_attribute = getattr(llm_package, "acp_agent", None)
    old_providers = dict(agent_registry._providers)
    reset_default_registry()
    import gideon.integrations.llm.acp_agent as acp_agent

    importlib.reload(acp_agent)
    try:
        yield get_default_registry()
    finally:
        model_registry.set_default_registry(old_registry)
        if old_module is not None:
            sys.modules["gideon.integrations.llm.acp_agent"] = old_module
            llm_package.acp_agent = old_attribute
        agent_registry._providers.clear()
        agent_registry._providers.update(old_providers)


def _response(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_reads_and_gateway_start_never_launch_configured_cli(
    acp_registry, monkeypatch, tmp_path
):
    """Startup, stale reads, and pool recovery must leave the real CLI untouched."""
    from gideon.integrations.acp import connection_pool
    from gideon.interfaces.dashboard.handlers import providers

    marker = tmp_path / "launched"
    executable = tmp_path / "acp-sentinel"
    executable.write_text(
        "#!/bin/sh\nprintf launched >> \"$1\"\nexit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    command = [str(executable), str(marker)]
    acp_registry.register_entry(
        ProviderEntry(
            name="acp:demand-sentinel",
            type="acp_agent",
            model="",
            options={"command": command, "dialect": "default"},
        )
    )

    old_pool = connection_pool.get_acp_pool()
    pool = await connection_pool.init_acp_pool(asyncio.Semaphore(2))
    monkeypatch.setattr(connection_pool, "_HEALTH_INTERVAL_SECS", 0.001)
    try:
        assert marker.exists() is False
        assert await providers.warm_readiness_cache() == 0
        listed = await providers.api_agent_providers_list(
            make_mocked_request("GET", "/api/agent-providers")
        )
        assert next(
            row for row in _response(listed)["agent_providers"]
            if row["name"] == "acp:demand-sentinel"
        )["state"] == "untested"

        discovery_request = make_mocked_request(
            "GET",
            "/api/agent-providers/acp:demand-sentinel/agents",
            match_info={"id": "acp:demand-sentinel"},
        )
        discovery = await providers.api_agent_provider_agents(discovery_request)
        assert discovery.status == 200
        payload = _response(discovery)
        assert payload["state"] == "untested"
        assert payload["agents"] == []

        await pool._slot("acp:demand-sentinel")
        maintenance = asyncio.create_task(pool._health_loop())
        await asyncio.sleep(0.02)
        pool._closed = True
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)
        assert marker.exists() is False

        from gideon.engine.agents.runtime_tests import test_runtime

        runtime_id = "acp:codex"
        no_model = ProviderEntry(
            name=runtime_id,
            type="acp_agent",
            model="",
            options={"command": command, "dialect": "codex"},
        )
        acp_registry.register_entry(no_model)
        no_model_result = await test_runtime(runtime_id, no_model)
        assert no_model_result["state"] == "no_model"
        assert no_model_result["ready"] is False
        assert marker.exists() is False

        acp_registry.unregister_entry(runtime_id)
        model_bound = ProviderEntry(
            name=runtime_id,
            type="acp_agent",
            model="test-model",
            options={"command": command, "dialect": "codex"},
        )
        acp_registry.register_entry(model_bound)
        failed_protocol = await test_runtime(runtime_id, model_bound)
        assert failed_protocol["ready"] is False
        assert failed_protocol["state"] == "error"
        assert failed_protocol["agents"] == []
        assert marker.read_text(encoding="utf-8") == "launched"
    finally:
        await pool.shutdown()
        connection_pool.set_acp_pool(old_pool)
