from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_runtime_and_resume_learning_use_structured_tool_status(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))
    from gideon.cognition.resume_account import _facts_from_tool_history
    from gideon.engine.agents.native.builtin_tools import create_platform_tools_provider
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.integrations.llm.credentials import Credential
    from gideon.integrations.llm.events import (
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )
    from gideon.integrations.llm.openai import OpenAIProvider

    source = tmp_path / "error-prefixed-success.txt"
    source.write_text("Error: this file was read successfully", encoding="utf-8")
    model = OpenAIProvider(
        model="mcp05-no-request",
        credential=Credential(
            name="mcp05-inert",
            kind="api_key",
            secret="mcp05-inert-test-credential",
            source="none",
        ),
    )
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="mcp05_status_regression", provider="native", model="mcp05-no-request"
        ),
        model_provider=model,
        tool_providers=[create_platform_tools_provider(cwd=tmp_path)],
        cwd=tmp_path,
        unattended=True,
    )
    try:
        await runtime.start()
        success_call = AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id="mcp05-success",
            title="read_file",
            tool_input={"path": source.name},
        )
        runtime._messages.append(runtime._assistant_msg("", [success_call]))
        success_events = [
            event async for event in runtime._execute_tool_batch([success_call])
        ]
        success = next(
            event for event in success_events if event.kind == EVENT_TOOL_RESULT
        )
        assert success.tool_output.startswith("Error: this file was read successfully")
        assert success.tool_meta["ok"] is True

        failed_call = AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id="mcp05-failed",
            title="tool_schema",
            tool_input={"tool_name": "not-in-the-catalog"},
        )
        runtime._messages.append(runtime._assistant_msg("", [failed_call]))
        failed_events = [
            event async for event in runtime._execute_tool_batch([failed_call])
        ]
        failed = next(
            event for event in failed_events if event.kind == EVENT_TOOL_RESULT
        )
        assert "No tool named" in failed.tool_output
        assert failed.tool_meta["ok"] is False
        assert runtime.drain_tool_outcomes() == [
            ("read_file", "success"),
            ("tool_schema", "failed"),
        ]

        cancelled_call = AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id="mcp05-cancelled",
            title="read_file",
            tool_input={"path": source.name},
        )
        runtime._messages.append(runtime._assistant_msg("", [cancelled_call]))
        cancelled_events = [
            event
            async for event in runtime._drop_queued_call(
                runtime._prepare_call(cancelled_call)
            )
        ]
        cancelled = next(
            event for event in cancelled_events if event.kind == EVENT_TOOL_RESULT
        )
        assert cancelled.tool_meta["ok"] is False
        assert runtime._messages[-1]["_tool_status"] is False
        assert runtime.drain_tool_outcomes() == [("read_file", "failed")]

        facts, _, _ = _facts_from_tool_history(runtime._messages)
        by_call = {fact.ref: fact for fact in facts}
        assert by_call["mcp05-success"].status == "done"
        assert by_call["mcp05-failed"].status == "failed"
        assert by_call["mcp05-cancelled"].status == "failed"

        provider_messages = runtime._messages_with_staged_images(runtime._messages)
        assert all("_tool_status" not in message for message in provider_messages)
        assert any(message.get("_tool_status") is True for message in runtime._messages)
        assert any(
            message.get("_tool_status") is False for message in runtime._messages
        )
    finally:
        await model.shutdown()
