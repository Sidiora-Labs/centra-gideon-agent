from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.roles import RoleProcess
from gideon.hypermid.tool_provider import (
    CancellationState,
    HypermidRoleToolProvider,
)


def test_real_role_process_is_a_gideon_tool_provider(tmp_path: Path) -> None:
    state_root = tmp_path / "tool-role"
    payload = tmp_path / "payload.txt"
    payload.write_bytes(b"real Hypermid provider")
    route = RoleProcess(
        [sys.executable, "-m", "gideon.hypermid.roles", "serve"],
        state_root,
    )
    route.start()
    provider = HypermidRoleToolProvider(
        route=route,
        scope=Scope(Id("owner-1"), Id("project-1")),
        provider_name="hypermid-local",
        display_name="Hypermid Local Modules",
        carrier_id=Id("gideon-session-1"),
        composition={"modules": ["files"]},
    )
    try:
        tools = asyncio.run(provider.list_tools())
        assert len(tools) == 1
        definition = tools[0]
        assert definition.name == "hypermid.file_digest"
        assert definition.provider == provider.name
        assert definition.requires_approval is True
        assert provider.capabilities[0].schema_digest
        assert provider.health().session_capabilities == ("synchronous",)

        result = asyncio.run(provider.invoke(definition.name, {"path": str(payload)}))
        assert result.success is True
        assert json.loads(result.output) == {
            "bytes": len(payload.read_bytes()),
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        }
        assert result.metadata["effect_state"] == "committed"
        assert result.metadata["call_key"].startswith("call-")

        cancellation = asyncio.run(
            provider.cancel(result.metadata["call_key"], Id("owner-1"))
        )
        assert cancellation.state is CancellationState.REFUSED
        assert cancellation.effect_state.value == "not_started"
        assert provider.health().active_calls == 0
    finally:
        route.close()
    assert provider.connected is False
