"""Argument failures are rejected before approval and before real tool effects."""

import asyncio
import json

import pytest
from test_mcp_abandoned_queue import SERVER, configured
from test_native_runtime import _defn, _ScriptedModel

from gideon.engine.agents.native.builtin_tools import (
    NativeBuiltinToolProvider,
    create_platform_tools_provider,
)
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.native.tools import InProcessMcpToolProvider
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from gideon.integrations.mcp_client import McpServerConn, _coerce_args_to_schema
from gideon.integrations.tool_providers.arguments import argument_refusal


def test_schema_checks_do_not_echo_values_fetch_refs_or_reject_alternative_branches():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "required": ["name"]},
            }
        },
    }
    assert "name" in argument_refusal("t", {"items": [{}]}, schema)
    assert not argument_refusal("t", {"items": [{"name": "n"}]}, schema)
    assert not argument_refusal(
        "t", {}, {"anyOf": [{"required": ["a"]}, {"required": ["b"]}]}
    )
    assert not argument_refusal(
        "t", {}, {"$ref": "https://invalid.example/never-fetch"}
    )
    reason = argument_refusal(
        "t",
        {"v": "private-input"},
        {"type": "object", "properties": {"v": {"type": "integer"}}},
        types=True,
    )
    assert "expected JSON type integer" in reason and "private-input" not in reason
    assert not argument_refusal(
        "t",
        {"v": "x"},
        {
            "properties": {
                "v": {"type": "string", "pattern": "(a+)+$", "enum": ["other"]}
            }
        },
        types=True,
    )


def test_json_coercion_is_exact_once_and_preserves_ambiguous_string_types():
    schema = {
        "properties": {
            "i": {"type": "integer"},
            "n": {"type": ["number", "null"]},
            "b": {"type": "boolean"},
            "a": {"type": "array"},
            "o": {"type": "object"},
            "s": {"type": ["boolean", "string"]},
            "branch": {"anyOf": [{"type": "boolean"}]},
            "ambiguous": {"type": ["integer", "boolean"]},
        }
    }
    args = {
        "i": "5",
        "n": "1.25",
        "b": "false",
        "a": "[1]",
        "o": '{"x":2}',
        "s": "false",
        "branch": "false",
        "ambiguous": "1",
    }
    assert _coerce_args_to_schema(args, schema) == {
        "i": 5,
        "n": 1.25,
        "b": False,
        "a": [1],
        "o": {"x": 2},
        "s": "false",
        "branch": "false",
        "ambiguous": "1",
    }
    assert _coerce_args_to_schema({"b": "yes", "a": '"[1]"', "o": "[]"}, schema) == {
        "b": "yes",
        "a": '"[1]"',
        "o": "[]",
    }
    assert args["b"] == "false"


@pytest.mark.asyncio
async def test_real_sdk_invalid_arguments_never_reach_child_and_valid_call_still_runs(
    tmp_path,
):
    program = SERVER.replace(
        "'properties':{'value'", "'required':['value'],'properties':{'value'"
    )
    spec, log = configured(tmp_path, program=program)
    conn = McpServerConn("queue", spec)
    try:
        for args in ({}, {"value": 42}, {"value": "safe", "delay": "invalid"}):
            ok, reason = await conn.call_tool("record", args)
            assert not ok and "was not run" in reason
            assert not log.exists()
        assert await conn.call_tool("record", {"value": "accepted", "delay": "0"}) == (
            True,
            "accepted",
        )
    finally:
        await conn.shutdown()
    assert log.read_text().splitlines() == ["accepted"]


@pytest.mark.asyncio
async def test_native_stream_missing_write_input_does_not_ask_or_write(tmp_path):
    marker = tmp_path / "never-written"
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    title="write_file",
                    tool_call_id="missing",
                    tool_input=json.dumps({"path": str(marker)}),
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [
                AgentEvent(
                    kind=EVENT_TEXT_CHUNK, text="The invalid call was rejected."
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
        ]
    )
    model.takes_local_turns = True  # The scripted model performs no provider transport.
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[create_platform_tools_provider(cwd=tmp_path)],
    )
    await rt.start()
    assert "write_file" in rt._tool_index
    async with asyncio.timeout(5):
        events = [event async for event in rt.stream("write the file")]
    assert not any(event.kind == EVENT_PERMISSION_REQUEST for event in events)
    results = [event for event in events if event.kind == EVENT_TOOL_RESULT]
    assert results and "content" in str(results[0].tool_output)
    assert not marker.exists()
    provider = NativeBuiltinToolProvider(cwd=tmp_path)
    result = await provider.invoke("write_file", {"path": str(marker)})
    assert not result.success and result.metadata["effect_state"] == "not_started"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_owned_field_rules_preflight_and_acp_dispatch_do_not_invoke(
    tmp_path, monkeypatch
):
    from gideon.integrations import mcp_core

    marker = tmp_path / "inner-effect"

    def effect(*args):
        marker.write_text("invoked")
        return "unexpected"

    monkeypatch.setattr(mcp_core, "_call_tool_inner", effect)
    provider = InProcessMcpToolProvider()
    for args in (
        {"rule": "valid", "category": "invalid"},
        {"rule": "x" * 501},
        {"rule": "valid", "category": "knowledge", "scope": "wrong"},
    ):
        result = await provider.preflight("memory_remember", args)
        assert result is not None and not result.success
        assert result.metadata["effect_state"] == "not_started"
        invoked = await provider.invoke("memory_remember", args)
        assert not invoked.success
        assert mcp_core._call_tool("memory_remember", args).startswith("Error:")
        assert not marker.exists()


@pytest.mark.asyncio
async def test_try_it_missing_arguments_refuses_before_risk_confirmation(
    tmp_path, monkeypatch
):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers.tools import api_tool_invoke

    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path))
    app = web.Application()
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/tools/invoke", json={"tool": "bash", "arguments": {}}
        )
        body = await response.json()
        assert response.status == 400 and "command" in body["error"]
        assert body["metadata"]["effect_state"] == "not_started"
