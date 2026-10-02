from __future__ import annotations

import hashlib
import sys

import pytest

from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.modules import HypermidModuleSupervisor, ToolModuleSpec


@pytest.mark.asyncio
async def test_real_role_module_is_session_scoped_and_retired(tmp_path) -> None:
    supervisor = HypermidModuleSupervisor(
        tmp_path / "modules",
        scope=Scope("owner-module", "project-module", "workspace-module"),
    )
    supervisor.enable()
    spec = ToolModuleSpec(
        module_id=Id("module-file-digest"),
        command=(sys.executable, "-m", "gideon.hypermid.roles", "serve"),
        provider_name="hypermid-local-role",
        display_name="Hypermid Local Role",
        carrier_id=Id("carrier-file-digest"),
    )
    source = tmp_path / "source.txt"
    source.write_bytes("module payload: café 界".encode())

    try:
        provider = await supervisor.open_tool_module("session-a", spec)
        definitions = await provider.list_tools()

        assert len(definitions) == 1
        assert definitions[0].name == "hypermid.file_digest"
        assert definitions[0].requires_approval is True
        assert supervisor.providers_for_session("session-a") == (provider,)
        assert supervisor.providers_for_session("session-b") == ()
        assert supervisor.health()[0].connected is True

        result = await provider.invoke(
            "hypermid.file_digest", {"path": str(source)}
        )
        assert result.success is True
        assert hashlib.sha256(source.read_bytes()).hexdigest() in result.output

        await supervisor.close_session("session-a")
        assert supervisor.providers_for_session("session-a") == ()
        assert provider.connected is False
    finally:
        await supervisor.disable()
