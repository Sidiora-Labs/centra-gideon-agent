"""Owned declarations may suppress a duplicate start decision only."""
from gideon.engine.agents.native.tools import _describe_mcp_tool
from gideon.integrations.acp.mcp_servers import core_tool_work_asks
from gideon.integrations import mcp_core


def test_external_hint_cannot_confer_work_owned_decision():
    raw = next(item for item in mcp_core._aggregated_list_tools() if item['name'] == 'subagent_run')
    assert _describe_mcp_tool(raw, 'external').work_asks is False
    assert _describe_mcp_tool(raw, 'gideon-core', trusted_definition=True).work_asks is True


def test_exact_core_identity_and_schema_required():
    args = {'task': 'Inspect this repository'}
    assert core_tool_work_asks('mcp__gideon-core__subagent_run', 'other', args)
    assert not core_tool_work_asks('subagent_run', 'other', args)
    assert not core_tool_work_asks('mcp__external__subagent_run', 'other', args)
    assert not core_tool_work_asks('mcp__gideon-core__subagent_run', 'other', {**args, 'unrecognized': True})
