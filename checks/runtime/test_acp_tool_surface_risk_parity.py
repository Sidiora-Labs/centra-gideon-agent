import importlib

import pytest

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.engine.agents.native.tools import InProcessMcpToolProvider
from gideon.engine.task_modes import resolve_effective_risk
from gideon.integrations import mcp_core
from gideon.integrations.acp.adapter import acp_event_to_agent_event
from gideon.integrations.acp.dialect import DefaultDialect
from gideon.integrations.acp.translate import (
    build_permission_event,
    extract_tool_event,
)
from gideon.integrations.acp.types import JsonRpcMessage


@pytest.mark.asyncio
async def test_acp_surface_excludes_platform_shell_and_file_tools(tmp_path):
    platform = NativeBuiltinToolProvider(
        cwd=tmp_path, categories={"filesystem", "shell"}
    )
    platform_names = {tool.name for tool in await platform.list_tools()}
    assert {
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "glob",
        "grep",
    } <= platform_names
    tools = mcp_core._aggregated_list_tools()
    acp_names = {tool["name"] for tool in tools}
    assert {"artifact_save", "memory_recall", "subagent_run", "notify"} <= acp_names
    assert len(acp_names) == len(tools), "duplicate ACP tool declarations"
    assert not acp_names & platform_names


@pytest.mark.asyncio
async def test_every_reachable_core_tool_keeps_at_least_its_native_risk(tmp_path):
    acp_names = {tool["name"] for tool in mcp_core._aggregated_list_tools()}
    assert {"artifact_delete", "artifact_save", "memory_recall"} <= acp_names
    owners = ("gideon.integrations.mcp_core", *mcp_core._AGGREGATED_CATEGORY_MODULES)
    native = {}
    for module in owners:
        provider = InProcessMcpToolProvider(module=module, provider_name=module)
        definitions = await provider.list_tools()
        assert definitions, f"empty native catalog: {module}"
        assert callable(importlib.import_module(module)._call_tool)
        for definition in definitions:
            assert definition.name not in native, f"duplicate owner: {definition.name}"
            native[definition.name] = definition
    assert acp_names == set(
        native
    ), "reachable tools must all have native risk evidence"

    builtin = NativeBuiltinToolProvider(cwd=tmp_path)
    comparisons = [
        *native.values(),
        *(
            definition
            for definition in await builtin.list_tools()
            if definition.name in acp_names
        ),
    ]
    rank = {"safe": 0, "caution": 1, "destructive": 2}
    assert {definition.risk_level.value for definition in comparisons} == set(rank)
    downgraded = []
    for definition in comparisons:
        for title in (definition.name, f"mcp/gideon-core/{definition.name}"):
            inputs, seen, offered = {}, {}, {}
            frame = JsonRpcMessage(
                method="session/update",
                params={
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "parity",
                        "title": title,
                        "rawInput": {},
                        "status": "pending",
                    }
                },
            )
            opening = extract_tool_event(frame, inputs, seen, [])
            assert opening is not None
            permission = build_permission_event(
                JsonRpcMessage(
                    id=1,
                    method="session/request_permission",
                    params={
                        "toolCall": {"toolCallId": "parity", "title": title},
                        "options": [],
                    },
                ),
                DefaultDialect(),
                inputs,
                seen,
                offered,
            )
            for event in (opening, permission):
                delivered = acp_event_to_agent_event(event)
                risk = resolve_effective_risk(
                    getattr(delivered, "risk_level", ""),
                    delivered.title,
                    delivered.tool_kind,
                    delivered.tool_input,
                )
                if rank[risk] < rank[definition.risk_level.value]:
                    downgraded.append(
                        (title, event.kind, definition.risk_level.value, risk)
                    )
    assert not downgraded, f"ACP downgraded reachable core tools: {downgraded}"
