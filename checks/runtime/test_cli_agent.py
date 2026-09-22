"""Unit tests for the ``gideon agent`` CLI subcommand group.

Tests cover list output format, create with defaults, create duplicate,
update non-existent, and delete default agent.
"""

import json
import unittest.mock
from pathlib import Path

import pytest

from gideon.core.config.loader import AgentProfile
from gideon.interfaces.cli.commands import render_agent_table
from gideon.interfaces.cli.main import main


def _write_config(tmp_path: Path, data: dict) -> Path:
    """Write a config.json to *tmp_path* and return the path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def _base_config() -> dict:
    """Return a minimal valid config with a default agent."""
    return {
        "agents": {
            "default": {
                "provider_agent": "gideon",
                "default_dir": "",
                "memory_store": "default",
            },
        },
        "default_agent": "default",
        "memory_stores": {"default": {}},
    }


class TestAgentList:
    """Test ``gideon agent list`` output format."""

    def test_render_agent_table_derives_widths_from_rows_and_default_marker(
        self,
    ) -> None:
        name = "agent-with-a-name-longer-than-the-old-column"
        provider = "provider-agent-with-a-long-name"
        default_dir = "workspace-with-a-long-default-directory"
        memory_store = "memory-store-with-a-long-name"

        output = render_agent_table(
            {
                name: AgentProfile(
                    provider_agent=provider,
                    default_dir=default_dir,
                    memory_store=memory_store,
                )
            },
            name,
        )

        header, row = output.splitlines()
        assert row == f"{name} *  {provider}  {default_dir}  {memory_store}"
        assert header.index("PROVIDER_AGENT") == row.index(provider)
        assert header.index("DEFAULT_DIR") == row.index(default_dir)
        assert header.index("MEMORY_STORE") == row.index(memory_store)

    def test_list_output_format(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch(
                "gideon.core.config.loader.config_path", return_value=cfg_path
            ),
            unittest.mock.patch("sys.argv", ["gideon", "agent", "list"]),
        ):
            main()

        out = capsys.readouterr().out
        assert "NAME" in out
        assert "PROVIDER_AGENT" in out
        assert "DEFAULT_DIR" in out
        assert "MEMORY_STORE" in out
        assert "default *" in out or "default*" in out

    def test_list_multiple_agents(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = _base_config()
        data["agents"]["oncall"] = {
            "provider_agent": "oncall-agent",
            "default_dir": "oncall-ws",
            "memory_store": "oncall-mem",
        }
        cfg_path = _write_config(tmp_path, data)

        with (
            unittest.mock.patch(
                "gideon.core.config.loader.config_path", return_value=cfg_path
            ),
            unittest.mock.patch("sys.argv", ["gideon", "agent", "list"]),
        ):
            main()

        out = capsys.readouterr().out
        assert "oncall" in out
        assert "oncall-agent" in out


class TestAgentCreate:
    """Test ``gideon agent create``."""

    def test_create_with_defaults(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch(
                "gideon.core.config.loader.config_path", return_value=cfg_path
            ),
            unittest.mock.patch(
                "sys.argv",
                ["gideon", "agent", "create", "--name", "research"],
            ),
        ):
            main()

        out = capsys.readouterr().out
        assert "Created agent: research" in out

        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "research" in saved["agents"]
        assert saved["agents"]["research"]["provider_agent"] == "gideon"
        assert saved["agents"]["research"]["default_dir"] == ""
        assert saved["agents"]["research"]["memory_store"] == "default"

    def test_create_duplicate_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch(
                "gideon.core.config.loader.config_path", return_value=cfg_path
            ),
            unittest.mock.patch(
                "sys.argv",
                ["gideon", "agent", "create", "--name", "default"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "already exists" in err


class TestAgentUpdate:
    """Test ``gideon agent update``."""

    def test_update_nonexistent_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch(
                "gideon.core.config.loader.config_path", return_value=cfg_path
            ),
            unittest.mock.patch(
                "sys.argv",
                ["gideon", "agent", "update", "nonexistent", "--provider-agent", "x"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "not found" in err


class TestAgentDelete:
    """Test ``gideon agent delete``."""

    def test_delete_default_agent_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch(
                "gideon.core.config.loader.config_path", return_value=cfg_path
            ),
            unittest.mock.patch(
                "sys.argv",
                ["gideon", "agent", "delete", "default"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "cannot delete default agent" in err
