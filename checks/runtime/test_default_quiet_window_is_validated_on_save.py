"""Default workflow quiet windows must use the scheduler's execution parser."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def _seed_config():
    return {
        "default_agent": "gideon",
        "agents": {"gideon": {"provider_agent": "gideon"}},
        "workflows": {"default_quiet_windows": "22:00-08:00"},
    }


@pytest.mark.asyncio
async def test_invalid_default_window_is_rejected_without_replacing_stored_value(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(json.dumps(_seed_config()), encoding="utf-8")
    from gideon.interfaces.dashboard.handlers import api_gideon_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/gideon", api_gideon_config_patch)
    async with TestClient(TestServer(app)) as client:
        refused = await client.patch(
            "/api/config/gideon",
            json={"path": "workflows.default_quiet_windows", "value": "25:00-08:00"},
        )
        assert refused.status == 400
        assert "valid quiet window" in (await refused.json())["error"]

        saved = json.loads(config.read_text(encoding="utf-8"))
        assert saved["workflows"]["default_quiet_windows"] == "22:00-08:00"
