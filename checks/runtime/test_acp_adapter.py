from dataclasses import fields

from gideon.integrations.acp.adapter import acp_event_to_agent_event
from gideon.integrations.acp.types import AcpEvent
from gideon.integrations.llm.events import AgentEvent


def test_adapter_preserves_every_declared_event_field():
    source = AcpEvent(
        kind="tool_result",
        text="ready",
        tool_call_id="t1",
        title="Read",
        tool_kind="read",
        tool_purpose="inspect",
        context_usage_pct=0.0,
        stop_reason="end_turn",
        request_id=4,
        options=[{"id": "allow"}],
        tool_input="{}",
        tool_input_obj={"path": "a"},
        file_change={"path": "a", "before": "x", "after": "y"},
        tool_output="contents",
        input_tokens=2,
        output_tokens=3,
        cache_creation_tokens=5,
        cache_read_tokens=7,
        cost_usd=0.1,
        num_turns=11,
        duration_ms=13,
        event_count=17,
        tool_call_count=19,
        tool_meta={"ok": False},
    )
    event = acp_event_to_agent_event(source)
    assert isinstance(event, AgentEvent)
    for field in fields(AcpEvent):
        assert getattr(event, field.name) == getattr(source, field.name), field.name
    assert event.tool_meta is source.tool_meta
    assert event.tool_input_obj is source.tool_input_obj
    assert event.file_change is source.file_change
    assert event.options is source.options
    assert event.risk_level == ""


def test_adapter_retains_unknown_context_separately_from_measured_zero():
    assert acp_event_to_agent_event(AcpEvent(kind="complete")).context_usage_pct is None
    assert (
        acp_event_to_agent_event(
            AcpEvent(kind="complete", context_usage_pct=0.0)
        ).context_usage_pct
        == 0.0
    )
