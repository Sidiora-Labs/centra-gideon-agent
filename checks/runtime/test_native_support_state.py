"""Native support contracts exercised with actual futures, files and tool modules."""

import asyncio

import pytest

from gideon.engine.agents.native import read_gate
from gideon.engine.agents.native.approval import APPROVE, REJECT, ApprovalGate
from gideon.engine.agents.native.tools import (
    InProcessMcpToolProvider,
    tool_definitions_to_openai_schema,
)
from gideon.integrations.tool_providers.base import ToolDefinition


@pytest.mark.asyncio
async def test_decision_can_arrive_before_wait_and_cannot_be_replaced():
    gate = ApprovalGate()
    future = gate.register("early")
    assert gate.approve("early")
    assert not gate.reject("early")
    assert await gate.wait("early", future, timeout=0) == APPROVE
    assert not gate.approve("early")


@pytest.mark.asyncio
async def test_session_shutdown_rejects_all_registered_requests():
    gate = ApprovalGate()
    pending = [(str(index), gate.register(str(index))) for index in range(4)]
    gate.cancel_all()
    outcomes = await asyncio.gather(
        *(gate.wait(key, future) for key, future in pending)
    )
    assert outcomes == [REJECT] * 4
    assert all(not gate.approve(key) for key, _ in pending)


@pytest.mark.asyncio
async def test_cancelling_waiter_cancels_its_future_and_removes_request():
    gate = ApprovalGate()
    future = gate.register("cancel")
    waiter = asyncio.create_task(gate.wait("cancel", future))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert future.cancelled()
    assert not gate.resolve("cancel", APPROVE)


@pytest.fixture
def evidence():
    read_gate.reset_all()
    yield
    read_gate.reset_all()


def test_retrieval_from_old_snapshot_cannot_extend_a_later_edit(tmp_path, evidence):
    path = tmp_path / "content.txt"
    path.write_text("first part\nhidden part\n")
    read_gate.record_read(
        "editing",
        path,
        observed_text="first part",
        complete=False,
        content_sha256=read_gate.file_sha256(path),
        raw_ref="original",
    )
    path.write_text("changed part\nhidden part\n")
    read_gate.record_edit("editing", path, old="first", new="changed")
    read_gate.record_retrieval("editing", "original", observed_text="hidden part")
    snapshot = read_gate.observation("editing", path)
    assert snapshot.fragments == ("changed part",)
    assert not snapshot.complete
    refusal = read_gate.admit_write(
        "editing",
        path,
        operation="edit",
        display_path="content.txt",
        required_text="hidden",
    )
    assert refusal.reason == "region_not_observed"
    assert read_gate.observation("other-session", path) is None
    read_gate.begin_turn("editing")
    assert read_gate.observation("editing", path) is None


def test_read_capacity_evicts_oldest_and_discards_overflowed_references(
    tmp_path, evidence
):
    for index in range(read_gate.MAX_PATHS_PER_SESSION + 1):
        read_gate.record_read(
            "bounded",
            tmp_path / str(index),
            observed_text=str(index),
            content_sha256=str(index),
            complete=False,
            raw_ref=f"ref-{index}",
        )
    assert read_gate.observation("bounded", tmp_path / "0") is None
    latest = tmp_path / str(read_gate.MAX_PATHS_PER_SESSION)
    snapshot = read_gate.observation("bounded", latest)
    assert snapshot is not None
    read_gate.record_retrieval(
        "bounded", f"ref-{read_gate.MAX_PATHS_PER_SESSION}", observed_text="extra"
    )
    assert read_gate.observation("bounded", latest) == snapshot


@pytest.mark.asyncio
async def test_inprocess_provider_discovers_and_invokes_actual_prompt_module():
    provider = InProcessMcpToolProvider(module="gideon.integrations.mcp_prompts")
    catalog = await provider.list_tools()
    assert any(tool.name == "prompt_render" for tool in catalog)
    assert await provider.list_tools() is catalog
    result = await provider.invoke("prompt_render", {})
    assert result.success
    assert result.output == "Error: prompt_id is required."
    exception = await provider.invoke("prompt_render", {"prompt_id": 42})
    assert not exception.success
    assert "strip" in exception.error


def test_schema_conversion_preserves_declared_parameters_and_supplies_empty_object():
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    declared = ToolDefinition("read_file", "Read content", parameters=parameters)
    empty = ToolDefinition("status", "")
    schemas = tool_definitions_to_openai_schema([declared, empty])
    assert schemas[0]["function"]["parameters"] is parameters
    assert schemas[1] == {
        "type": "function",
        "function": {
            "name": "status",
            "description": "",
            "parameters": {"type": "object", "properties": {}},
        },
    }
