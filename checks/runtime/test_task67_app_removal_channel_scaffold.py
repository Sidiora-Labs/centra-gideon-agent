from __future__ import annotations

import json
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.apps import app_manager, manager
from gideon.extensions.apps.native_contract import namespaced_module_name
from gideon.extensions.providers.registry import get_provider_registry
from gideon.interfaces.cli.app_new import scaffold
from gideon.interfaces.dashboard.handlers.apps import register_app_routes


def _app_source(tmp_path, name: str):
    source = tmp_path / "source" / name
    source.mkdir(parents=True)
    (source / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Removal Fixture",
                "description": "Dashboard removal fixture",
            }
        ),
        encoding="utf-8",
    )
    return source


@pytest.mark.asyncio
async def test_dashboard_removal_preserves_data_for_reinstall(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setattr(manager, "config_dir", lambda: home)

    source = _app_source(tmp_path, "removal-fixture")
    assert app_manager.install(source, confirm=True).ok
    state = manager.app_data_dir("removal-fixture") / "state.json"
    state.write_text('{"draft":"kept"}\n', encoding="utf-8")

    app = web.Application()
    register_app_routes(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.delete("/api/apps/removal-fixture?remove=1")
        assert response.status == 200, await response.text()
        assert (await response.json())["dataPreserved"] is True

    assert not manager.app_dir("removal-fixture").exists()
    assert app_manager.install(source, confirm=True).ok
    assert (manager.app_data_dir("removal-fixture") / "state.json").read_text(
        encoding="utf-8"
    ) == '{"draft":"kept"}\n'


def test_channel_scaffold_registration_failure_is_reported(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setattr(manager, "config_dir", lambda: home)

    generated = scaffold("broken-channel", "channel", dest=tmp_path).path
    provider = generated / "provider.py"
    provider.write_text(
        provider.read_text(encoding="utf-8").replace(
            "return BrokenChannelProvider(config)",
            'raise RuntimeError("channel registration refused")',
        ),
        encoding="utf-8",
    )

    result = app_manager.install(generated, origin="local", confirm=True)
    assert result.ok
    registered = get_provider_registry().get("broken-channel")
    try:
        assert registered is not None
        assert registered.enabled is False
        assert registered.error == "channel registration refused"
    finally:
        get_provider_registry().deregister("broken-channel")
        sys.modules.pop(namespaced_module_name("broken-channel", "provider"), None)
