from gideon.integrations.llm_helpers import LLMEvent
from gideon.security.approval_brief import channel_approval_brief


def test_channel_brief_masks_arguments_and_includes_purpose_and_effective_risk():
    event = LLMEvent(
        kind="permission_request",
        request_id="request-1",
        title="web_fetch",
        tool_input={
            "url": "https://example.test",
            "api_key": "sk-12345678901234567890123456789012",
        },
        tool_purpose="Read the public status page.",
        risk_level="safe",
    )
    brief = channel_approval_brief(event)
    assert "Tool: web_fetch" in brief
    assert "Arguments:" in brief and "Purpose: Read the public status page." in brief
    assert "Risk:" in brief and "Touches: uses the network" in brief
    assert "sk-12345678901234567890123456789012" not in brief
