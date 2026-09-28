"""Ownership arbitration over the real in-process MCP provider implementation."""

import asyncio

from gideon.engine.agents.native.tools import InProcessMcpToolProvider
from gideon.integrations.tool_providers.registry import (
    register_provider,
    resolve_tool_catalog,
    list_providers,
    unregister_provider,
)


def _workflow_provider(name: str) -> InProcessMcpToolProvider:
    return InProcessMcpToolProvider(
        module="gideon.integrations.mcp_workflows",
        provider_name=name,
        display="Workflows",
    )


def _unregister(provider: InProcessMcpToolProvider, owner: str, instance: str) -> None:
    unregister_provider(
        provider.name,
        owner_type="app",
        owner=owner,
        instance_id=instance,
    )


def test_core_precedes_apps_and_app_winner_is_restart_order_independent() -> None:
    later = _workflow_provider("ownership-z-provider")
    first = _workflow_provider("ownership-a-provider")
    app = _workflow_provider("ownership-core-collision")
    core = _workflow_provider("ownership-core")
    registered = [
        (later, "zeta-app", "zeta-instance", "app"),
        (first, "alpha-app", "alpha-instance", "app"),
        (app, "builtin-app", "singleton", "app"),
        (core, "gideon-tools", "singleton", "core"),
    ]
    statuses: dict[str, dict] = {}
    try:
        for provider, owner, instance, owner_type in registered:
            register_provider(
                provider,
                owner_type=owner_type,
                owner=owner,
                instance_id=instance,
                status_callback=lambda status, key=owner: statuses.__setitem__(key, status),
            )
        catalog = asyncio.run(resolve_tool_catalog([later, first, app, core]))
        core_names = {tool.name for tool in asyncio.run(core.list_tools())}
        assert set(catalog.providers) == core_names
        assert all(provider is core for provider in catalog.providers.values())
        refusals = {item["owner"]: item["reason"] for item in catalog.refusals}
        assert refusals == {
            "builtin-app": "tool_name_owned_by_core",
            "alpha-app": "tool_name_owned_by_core",
            "zeta-app": "tool_name_owned_by_core",
        }
        assert statuses["zeta-app"]["accepted"] is False
        assert statuses["zeta-app"]["reason"] == "tool_name_owned_by_core"

        # Without a core claimant, stable app identity decides the same collision,
        # independent of the input/restart order.
        catalog = asyncio.run(resolve_tool_catalog([later, first]))
        first_names = {tool.name for tool in asyncio.run(first.list_tools())}
        assert set(catalog.providers) == first_names
        assert all(provider is first for provider in catalog.providers.values())
        assert catalog.refusals[0]["owner"] == "zeta-app"
        assert catalog.refusals[0]["reason"] == "tool_name_already_owned"
        assert catalog.refusals[0]["existing_owner"] == "app:alpha-app@alpha-instance"
    finally:
        _unregister(later, "zeta-app", "zeta-instance")
        _unregister(first, "alpha-app", "alpha-instance")
        _unregister(app, "builtin-app", "singleton")
        unregister_provider(
            core.name, owner_type="core", owner="gideon-tools", instance_id="singleton"
        )


def test_same_app_instance_replacement_is_atomic() -> None:
    original = _workflow_provider("ownership-replaceable")
    replacement = _workflow_provider("ownership-replaceable")
    companion = InProcessMcpToolProvider(
        module="gideon.integrations.mcp_memory",
        provider_name="ownership-companion",
        display="Memory",
    )
    try:
        register_provider(
            original,
            owner_type="app",
            owner="single-app",
            instance_id="instance-7",
        )
        register_provider(
            replacement,
            owner_type="app",
            owner="single-app",
            instance_id="instance-7",
        )
        register_provider(
            companion, owner_type="app", owner="single-app", instance_id="instance-7"
        )
        assert replacement in list_providers() and companion in list_providers()
        assert original not in list_providers()
        catalog = asyncio.run(resolve_tool_catalog([replacement]))
        assert "workflow_list_defs" in catalog.providers
        assert all(provider is replacement for provider in catalog.providers.values())
        catalog = asyncio.run(resolve_tool_catalog([replacement, companion]))
        assert catalog.providers["workflow_list_defs"] is replacement
        assert catalog.providers["memory_recall"] is companion
    finally:
        _unregister(replacement, "single-app", "instance-7")
        _unregister(companion, "single-app", "instance-7")
