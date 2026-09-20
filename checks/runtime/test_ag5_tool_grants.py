import io

import pytest

from gideon.engine.subagent import DelegationSupervisor, SubagentInfo, _ExecutionPass
from gideon.integrations.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
from gideon.security.guardrails import ceiling
from gideon.security.guardrails.policy import tool_grant_denial, tool_grant_posture


@pytest.fixture(autouse=True)
def open_governance():
    previous = ceiling._ACTIVE
    ceiling._ACTIVE = ceiling.OPEN_CEILING
    try:
        yield
    finally:
        ceiling._ACTIVE = previous


def test_tier_algebra_fails_closed_and_uses_ceiling_name_globs():
    assert tool_grant_denial("artifact_update", "read")
    assert tool_grant_denial("memory_recall", "read") == ""
    assert tool_grant_denial("artifact_update", "read_write") == ""
    assert tool_grant_denial("artifact_update", "unknown")
    assert tool_grant_denial("memory_recall", "custom", (" ",))
    assert tool_grant_denial("read_file", "custom", ("read_*",)) == ""

    ceiling._ACTIVE = ceiling.parse_ceiling(
        {"scopes": {"tools": {"allow": ["read_*"]}}}
    )
    posture = tool_grant_posture("read_write", ("read_file", "artifact_update"))
    assert posture.tool_grants == "custom"
    assert posture.tool_allowlist == ("read_file",)
    assert tool_grant_denial("artifact_update", "read_write")


@pytest.mark.asyncio
async def test_mcp_and_subagent_permission_seams_ask_the_tier(monkeypatch):
    from gideon.integrations import mcp_shared

    monkeypatch.setenv("GIDEON_SESSION_KEY", "subagent:focused")
    assert mcp_shared.leaf_tool_denial("artifact_update")
    assert mcp_shared.leaf_tool_denial("memory_recall") == ""

    info = SubagentInfo(id="focused", task="task")
    run = _ExecutionPass(
        info=info,
        client=None,
        session_key="subagent:focused",
        policy="auto",
        turn_limit=1,
        research=False,
        output=io.StringIO(),
    )
    event = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="artifact_update",
        request_id="request",
    )
    supervisor = DelegationSupervisor.__new__(DelegationSupervisor)
    approved, error, metadata = await supervisor._permission_decision(run, event)
    assert approved is False
    assert "read-only" in (error or "")
    assert metadata == {"subagent_id": "focused", "reason": "tool_grant_deny"}
