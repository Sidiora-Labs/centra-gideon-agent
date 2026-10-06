from __future__ import annotations

import importlib.util
from pathlib import Path

from gideon.integrations.tool_providers.portable_schema import offered_tool_payload


def _builtin_project_run_create():
    repository = Path(__file__).resolve().parents[2]
    source = repository / "runtime/gideon/engine/agents/native/project_run_tool_defs.py"
    spec = importlib.util.spec_from_file_location(
        "_gateway_project_run_tool_defs", source
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.project_run_tool_definitions(
        "native", {"type": "object", "properties": {}}
    )[0]


def test_managed_gateway_payload_is_normalized_as_one_complete_tool_block():
    project_run_create = _builtin_project_run_create()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "managed_safe",
                "description": "A safe tool",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "examples": ["x"]}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "project_run_create",
                "description": project_run_create.description,
                "parameters": project_run_create.parameters,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mcp__remote__malformed",
                "description": "Remote tool with a non-portable array",
                "parameters": {
                    "type": "object",
                    "properties": {"values": {"type": "array"}},
                },
            },
        },
    ]
    offered = offered_tool_payload(tools, provider="openai-compatible")
    assert [item["function"]["name"] for item in offered] == [
        "managed_safe",
        "project_run_create",
    ]
    safe = offered[0]["function"]["parameters"]
    assert safe["required"] == ["query"]
    assert "additionalProperties" not in safe
    assert "examples" not in safe["properties"]["query"]
    assert offered[1]["function"]["parameters"]["properties"]["deliverables"][
        "items"
    ] == {"type": "string"}
    assert tools[0]["function"]["parameters"]["additionalProperties"] is False
