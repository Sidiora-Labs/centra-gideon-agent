from __future__ import annotations

import json
import sys

import pytest

from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.integrations import (
    IntegrationScopeError,
    IntegrationStatusFacade,
)
from gideon.hypermid.modules import HypermidModuleSupervisor, ToolModuleSpec


@pytest.mark.asyncio
async def test_real_stdio_module_has_scoped_truthful_connection_lifecycle(tmp_path) -> None:
    scope = Scope("owner-integrations", "project-integrations", "workspace-integrations")
    supervisor = HypermidModuleSupervisor(tmp_path / "modules", scope=scope)
    supervisor.enable()
    provider = await supervisor.open_tool_module(
        "session-integrations",
        ToolModuleSpec(
            module_id=Id("module-integrations"),
            command=(sys.executable, "-m", "gideon.hypermid.roles", "serve"),
            provider_name="hypermid-real-stdio",
            display_name="Hypermid real stdio",
            carrier_id=Id("carrier-integrations"),
        ),
    )
    facade = IntegrationStatusFacade(scope, module_supervisor=supervisor)

    try:
        snapshot = await facade.snapshot(scope, "session-integrations")
        connections = {item.connection_id: item for item in snapshot.connections}

        assert connections["hypermid-host"].state == "unavailable"
        assert connections["hypermid-host"].coverage == "full_host"
        assert connections["mcp-bridge"].state == "unavailable"
        assert connections["mcp-bridge"].coverage == "tool_bridge"
        assert connections["stdio-supervisor"].state == "ready"
        module = connections["stdio:module-integrations"]
        assert module.state == "connected"
        assert module.coverage == "tool_bridge"
        assert module.transport == "stdio"
        assert module.capabilities == ("catalog", "invoke", "cancel", "health")
        assert module.active_calls == 0
        assert snapshot.conditions[0].to_wire()["availability"] == "unavailable"
        assert snapshot.conditions[0].to_wire()["transition_identity"] == "unavailable"

        aggregate = await facade.snapshot(scope)
        assert all(item.kind != "stdio_module" for item in aggregate.connections)
        assert aggregate.conditions[0].to_wire()["failure_code"] == (
            "condition_session_unavailable"
        )

        wire = snapshot.to_wire()
        serialized = json.dumps(wire, sort_keys=True).lower()
        assert "command" not in serialized
        assert "credential" not in serialized
        assert "authorization" not in serialized
        assert all(
            item["budget"]["state"] == "unavailable"
            and item["fault"]["state"] == "unavailable"
            for item in wire["connections"]
            if item["transport"] == "stdio"
        )

        unsupported = await facade.act(
            scope, "session-integrations", "stdio-supervisor", "reconnect"
        )
        assert unsupported.to_wire() == {
            "connection_id": "stdio-supervisor",
            "operation": "reconnect",
            "state": "unsupported",
            "error_code": "operation_unsupported",
        }

        stopped = await facade.act(
            scope, "session-integrations", "stdio-supervisor", "stop"
        )
        assert stopped.state == "applied"
        assert provider.connected is False
        assert supervisor.health() == ()

        with pytest.raises(IntegrationScopeError) as error:
            await facade.snapshot(
                Scope("owner-other", "project-integrations"),
                "session-integrations",
            )
        assert error.value.code == "scope_mismatch"
    finally:
        await supervisor.disable()
