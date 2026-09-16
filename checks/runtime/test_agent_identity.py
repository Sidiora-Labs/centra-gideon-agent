"""Agent identity resolution — the canonical "which agent is this turn?" rule.

These cases were in `test_workflows_composition.py`, which WORKFLOWS-V2 Phase 1
deletes wholesale. The rule itself is not workflow-specific (chat_runner stamps every
turn's `resolved_agent_id` with it), so it keeps its coverage here rather than
disappearing with the feature that happened to need it first.
"""

from __future__ import annotations

from gideon.engine.agents.identity import resolve_agent_id


class TestResolveAgentId:
    def test_native_turn_is_the_bare_profile_name(self) -> None:
        assert resolve_agent_id("default", "native", None) == "default"
        assert resolve_agent_id("gideon-loop", "native", None) == "gideon-loop"

    def test_acp_turn_carries_the_mode(self) -> None:
        assert (
            resolve_agent_id(None, "acp:test-cli", "researcher")
            == "acp:test-cli/researcher"
        )

    def test_acp_turn_without_a_mode_is_just_the_cli(self) -> None:
        assert resolve_agent_id(None, "acp:claude-code", "") == "acp:claude-code"
        assert resolve_agent_id(None, "acp:claude-code", None) == "acp:claude-code"

    def test_unknown_kind_falls_back_to_the_agent_name(self) -> None:
        assert resolve_agent_id("default", "", None) == "default"
        assert resolve_agent_id(None, "", "fallback") == "fallback"

    def test_everything_absent_is_empty_not_none(self) -> None:
        assert resolve_agent_id(None, None, None) == ""

    def test_whitespace_is_stripped(self) -> None:
        assert resolve_agent_id("  default  ", "native", None) == "default"
        assert resolve_agent_id(None, "acp:cli", "  mode  ") == "acp:cli/mode"
