"""subagent_list redacts credentials before truncating task strings."""

from unittest.mock import patch

from gideon.integrations.mcp_subagents import _call_tool_inner


class TestSpawnListRedactBeforeTruncate:
    def test_credential_at_truncation_boundary_is_redacted(self):
        """A credential straddling the 60-char boundary must be fully redacted."""
        padding = "A" * 50
        secret = "AKIAIOSFODNN7EXAMPLE"
        task = padding + secret

        fake_response = {
            "agents": [
                {
                    "id": "agent-1",
                    "task": task,
                    "done": False,
                    "turns": 1,
                    "last_tool": "shell",
                    "elapsed": 5,
                    "started": 0,
                }
            ]
        }

        with patch(
            "gideon.integrations.mcp_subagents._get", return_value=fake_response
        ):
            result = _call_tool_inner("subagent_list", {})

        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "AKIA" not in result


class TestSpawnListAgentNames:
    """Cover the Available agents line in subagent_list description."""

    def test_spawn_list_includes_agent_names(self):
        from unittest.mock import MagicMock

        fake_response = {"agents": []}

        fake_cfg = MagicMock()
        fake_cfg.agents = {"yolo-general": MagicMock()}

        with (
            patch("gideon.integrations.mcp_subagents._get", return_value=fake_response),
            patch("gideon.core.config.loader.AppConfig.load", return_value=fake_cfg),
        ):
            result = _call_tool_inner("subagent_list", {})

        assert "yolo-general" in result
