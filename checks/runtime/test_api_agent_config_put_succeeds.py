"""Integration test for api_agent_config PUT.

Regression test for bug where local variable 'config_path' shadowed the
imported config_path() function, causing "'PosixPath' object is not callable".
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.interfaces.dashboard.handlers import api_agent_config


@pytest.mark.asyncio
async def test_api_agent_config_put_succeeds(tmp_path):
    installed = tmp_path / "gideon.json"
    installed.write_text(json.dumps({"name": "gideon"}))
    defaults = tmp_path / "defaults.json"
    pc_cfg = tmp_path / "config.json"

    request = make_mocked_request(
        "PUT", "/api/agent/config", headers={"Content-Type": "application/json"}
    )
    request._read_bytes = json.dumps(
        {"config": {"name": "test", "tools": ["a"], "allowedTools": ["b"]}}
    ).encode()

    with (
        patch(
            "gideon.interfaces.dashboard.handlers._installed_agent_config",
            return_value=installed,
        ),
        patch(
            "gideon.interfaces.dashboard.handlers._find_agent_config",
            return_value=defaults,
        ),
        patch(
            "gideon.interfaces.dashboard.handlers._reset_all_sessions",
            new_callable=AsyncMock,
        ),
        patch("gideon.interfaces.dashboard.handlers.config_path", return_value=pc_cfg),
        patch(
            "gideon.engine.agent.build_agent_config",
            return_value={
                "toolsSettings": {"execute_bash": {"deniedCommands": ["rm -rf"]}}
            },
        ),
        patch(
            "gideon.engine.agent.get_shipped_tools",
            return_value={"tools": ["a", "c"], "allowedTools": ["b"]},
        ),
    ):

        response = await api_agent_config(request)

    assert response.status == 200
    assert installed.exists()
    assert json.loads(installed.read_text())["name"] == "test"
    assert pc_cfg.exists()
    assert json.loads(pc_cfg.read_text())["removedTools"]["tools"] == ["c"]
