"""Native MCP leaf context remains bound to its own request."""

from __future__ import annotations

import asyncio

import pytest

from gideon.automation.workflows.engine import leaf_spawn_env
from gideon.automation.workflows.models import Node
from gideon.engine.agents.native.tools import InProcessMcpToolProvider
from gideon.integrations import mcp_automation, mcp_shared, mcp_subagents
from gideon.integrations.acp import translate
from gideon.integrations.acp.adapter import acp_event_to_agent_event
from gideon.integrations.acp.dialect import DefaultDialect
from gideon.integrations.acp.mcp_servers import core_tool_declaration
from gideon.integrations.acp.types import JsonRpcMessage


def _stage_lineage(run_id: str, capability: str) -> dict[str, str]:
    node = Node.from_dict(
        {"kind": "stage", "id": "inspect", "config": {"prompt": "inspect"}}
    )
    env = leaf_spawn_env(
        node,
        {"prompt": "inspect", "capability": capability},
        run_id=run_id,
        depth=0,
    )
    return mcp_shared.leaf_lineage(env)


@pytest.mark.asyncio
async def test_leaf_lineage_is_request_scoped_and_read_only_posture_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in mcp_shared.LEAF_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIDEON_SESSION_KEY", "lineage-test")

    research = _stage_lineage("leaf0001", "research")
    mutating = _stage_lineage("leaf0002", "mutating")
    monkeypatch.setenv("__wf_run_id", "parent00")
    monkeypatch.setenv("__wf_depth", "3")

    async def observe(lineage: dict[str, str]) -> tuple[str, int, str, str, str, str]:
        token = mcp_shared.bind_leaf_lineage(lineage)
        try:
            await asyncio.sleep(0)
            denial = mcp_shared.leaf_tool_denial("automation_create")
            native_subagents = InProcessMcpToolProvider(
                module="gideon.integrations.mcp_subagents"
            )
            native_orchestration = await native_subagents.invoke(
                "subagent_run", {"task": "fan out"}
            )
            acp_write = mcp_automation._call_tool("automation_create", {})
            native_write = await InProcessMcpToolProvider(
                module="gideon.integrations.mcp_automation"
            ).invoke("automation_create", {})
            return (
                mcp_shared.leaf_run_id(),
                mcp_subagents._wf_depth(),
                denial,
                mcp_automation._resolve_resume_target({"resume_run_id": "self"})[
                    0
                ]["run_id"],
                native_orchestration.output,
                acp_write + "\n" + native_write.output,
            )
        finally:
            mcp_shared.reset_leaf_lineage(token)

    restricted, writable = await asyncio.gather(
        observe(research), observe(mutating)
    )
    assert restricted[:2] == ("leaf0001", 1)
    assert "read-only" in restricted[2].lower()
    assert restricted[3] == "leaf0001"
    assert "orchestration tool" in restricted[4]
    assert "read-only" in restricted[5].lower()
    assert writable[:2] == ("leaf0002", 1)
    assert writable[2] == ""
    assert writable[3] == "leaf0002"
    assert "orchestration tool" in writable[4]
    assert "read-only" not in writable[5].lower()

    empty = mcp_shared.bind_leaf_lineage({})
    try:
        assert mcp_shared.leaf_run_id() == ""
        assert mcp_subagents._wf_depth() == 0
    finally:
        mcp_shared.reset_leaf_lineage(empty)

    assert mcp_shared.leaf_run_id() == "parent00"
    assert mcp_subagents._wf_depth() == 3

    inputs: dict[str, str] = {}
    seen: dict = {}
    stats: list[tuple[str, str]] = []
    call = translate.extract_tool_event(
        JsonRpcMessage(
            method="session/update",
            params={
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "notice-1",
                    "title": "mcp__gideon-core__notify",
                    "kind": "other",
                    "rawInput": {"text": "Run finished", "session": "channel"},
                    "status": "pending",
                }
            },
        ),
        inputs,
        seen,
        stats,
    )
    assert call is not None
    permission = translate.build_permission_event(
        JsonRpcMessage(
            id=1,
            method="session/request_permission",
            params={
                "toolCall": {
                    "toolCallId": "notice-1",
                    "title": "mcp__gideon-core__notify",
                },
                "options": [],
            },
        ),
        DefaultDialect(),
        inputs,
        seen,
        {},
    )
    adapted = acp_event_to_agent_event(permission)
    assert adapted.risk_level == "caution"
    assert adapted.tool_meta == {"tells_owner": True}

    assert core_tool_declaration(
        "mcp__gideon-core__notify", "other", {"text": "x", "session": "channel"}
    ) == ("caution", True)
    assert core_tool_declaration(
        "mcp__gideon-core__notify", "other", {"text": "x"}
    ) == ("caution", False)
    assert core_tool_declaration(
        "mcp__gideon-core__notify",
        "other",
        {"text": "x", "session": "channel", "channel": "C12345678"},
    ) == ("caution", False)
    assert core_tool_declaration(
        "mcp__gideon-core__notify",
        "other",
        {"text": "x", "session": "channel", "unexpected": "value"},
    ) == ("", False)
    assert core_tool_declaration(
        "mcp__foreign__notify", "other", {"text": "x", "session": "channel"}
    ) == ("", False)
    assert core_tool_declaration(
        "Terminal", "execute", {"command": "notify --session channel"}
    ) == ("", False)
