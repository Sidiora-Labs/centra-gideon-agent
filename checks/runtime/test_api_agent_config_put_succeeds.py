"""Integration test for api_agent_config PUT.

Regression test for bug where local variable 'config_path' shadowed the
imported config_path() function, causing "'PosixPath' object is not callable".
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from gideon.dashboard.handlers import api_agent_config


@pytest.mark.asyncio
async def test_api_agent_config_put_succeeds(tmp_path):
    installed = tmp_path / "gideon.json"
    installed.write_text(json.dumps({"name": "gideon"}))
    defaults = tmp_path / "defaults.json"
    pc_cfg = tmp_path / "config.json"

    request = MagicMock(spec=web.Request)
    request.method = "PUT"
    request.app = {"state": MagicMock()}

    async def mock_json():
        return {"config": {"name": "test", "tools": ["a"], "allowedTools": ["b"]}}

    request.json = mock_json

    with (
        patch("gideon.dashboard.handlers._installed_agent_config", return_value=installed),
        patch("gideon.dashboard.handlers._find_agent_config", return_value=defaults),
        patch("gideon.dashboard.handlers._reset_all_sessions", new_callable=AsyncMock),
        patch("gideon.dashboard.handlers.config_path", return_value=pc_cfg),
        patch(
            "gideon.agent.build_agent_config",
            return_value={"toolsSettings": {"execute_bash": {"deniedCommands": ["rm -rf"]}}},
        ),
        patch(
            "gideon.agent.get_shipped_tools",
            return_value={"tools": ["a", "c"], "allowedTools": ["b"]},
        ),
    ):

        response = await api_agent_config(request)

    assert response.status == 200
    # Verify the handler actually wrote the config files
    assert installed.exists()
    assert json.loads(installed.read_text())["name"] == "test"
    assert pc_cfg.exists()
    assert json.loads(pc_cfg.read_text())["removedTools"]["tools"] == ["c"]
