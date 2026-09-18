"""Per-kind self-update apply routing (plan 34 S4 T4.3).

Verifies POST /api/update branches on the detected install kind: container and
desktop return a structured instructions payload (no apply runs); pip/git route
into their apply pipelines. Uses a mocked request + a minimal state stub so no
real gateway, subprocess, or network is involved (hermetic).
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.interfaces.dashboard.handlers import updates as upd


class _StateStub:
    def __init__(self) -> None:
        self._background_tasks: set = set()
        self.refreshes: list = []
        self.progress: list = []

    def push_refresh(self, *kinds: str) -> None:
        self.refreshes.extend(kinds)

    def push_update_progress(self, step: str, detail: str = "") -> None:
        self.progress.append((step, detail))


def _req() -> object:
    req = make_mocked_request("POST", "/api/update")
    req.app["state"] = _StateStub()
    return req


@pytest.fixture(autouse=True)
def _reset_flight():
    upd._apply_in_flight = False
    yield
    upd._apply_in_flight = False


@pytest.mark.asyncio
async def test_container_returns_instructions(monkeypatch) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")

    async def _fake_status(_cur):
        return {
            "kind": "container",
            "apply_method": "instructions",
            "instructions": ["docker compose pull", "docker compose up -d"],
        }

    monkeypatch.setattr(
        "gideon.operations.self_update.build_update_status", _fake_status
    )
    resp = await upd.api_update_apply(_req())
    body = json.loads(resp.body.decode())
    assert resp.status == 200
    assert body["status"] == "instructions"
    assert body["kind"] == "container"
    assert body["instructions"] == ["docker compose pull", "docker compose up -d"]
    assert upd._apply_in_flight is False


@pytest.mark.asyncio
async def test_desktop_returns_instructions(monkeypatch) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "desktop")

    async def _fake_status(_cur):
        return {
            "kind": "desktop",
            "apply_method": "desktop_delegate",
            "instructions": [],
        }

    monkeypatch.setattr(
        "gideon.operations.self_update.build_update_status", _fake_status
    )
    resp = await upd.api_update_apply(_req())
    body = json.loads(resp.body.decode())
    assert resp.status == 200
    assert body["kind"] == "desktop"
    assert body["status"] == "instructions"
    assert "releases page" in body["detail"]
    assert "updates itself" not in body["detail"]


@pytest.mark.asyncio
async def test_pip_kind_routes_to_pip_update(monkeypatch) -> None:
    monkeypatch.delenv("GIDEON_INSTALL_KIND", raising=False)
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)

    called = {"pip": False}

    async def _fake_pip(request, state):
        from aiohttp import web

        called["pip"] = True
        return web.json_response({"ok": True, "status": "updating", "kind": "pip"})

    monkeypatch.setattr(upd, "_apply_pip_update", _fake_pip)
    resp = await upd.api_update_apply(_req())
    body = json.loads(resp.body.decode())
    assert called["pip"] is True
    assert body["kind"] == "pip"


def _post_auto(body: object):
    req = make_mocked_request("POST", "/api/update/auto")

    async def _j():
        return body

    req.json = _j  # type: ignore[method-assign]
    return req


class TestAutomaticUpdateMode:
    """RUM-58: the staged mode is the control, and it REPLACED the legacy one.

    The old surface was a pair of booleans — ``auto_update`` plus a git-only
    ``dashboard.update_dev_mode`` — and neither said when an update would land.
    """

    @pytest.mark.asyncio
    async def test_the_mode_persists_into_the_updates_block(
        self, monkeypatch, tmp_path
    ) -> None:
        cfg = tmp_path / "config.json"
        monkeypatch.setattr(upd, "config_path", lambda: cfg, raising=False)
        resp = await upd.api_update_auto(_post_auto({"mode": "staged"}))
        assert json.loads(resp.body.decode()) == {
            "ok": True,
            "auto": "staged",
            "auto_update": True,
        }
        assert json.loads(cfg.read_text())["updates"]["auto"] == "staged"
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        from gideon.core.config.loader import AppConfig

        assert AppConfig.load().updates.auto == "staged"

    @pytest.mark.asyncio
    async def test_off_is_written_explicitly_so_the_legacy_flag_cannot_resurrect_it(
        self, monkeypatch, tmp_path
    ) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"auto_update": True}))
        monkeypatch.setattr(upd, "config_path", lambda: cfg, raising=False)
        await upd.api_update_auto(_post_auto({"mode": "off"}))
        assert json.loads(cfg.read_text())["updates"]["auto"] == "off"
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        from gideon.core.config.loader import AppConfig

        assert AppConfig.load().updates.auto == "off"

    @pytest.mark.asyncio
    async def test_a_legacy_enabled_body_still_maps_to_a_mode(
        self, monkeypatch, tmp_path
    ) -> None:
        cfg = tmp_path / "config.json"
        monkeypatch.setattr(upd, "config_path", lambda: cfg, raising=False)
        await upd.api_update_auto(_post_auto({"enabled": True}))
        assert json.loads(cfg.read_text())["updates"]["auto"] == "staged"
        await upd.api_update_auto(_post_auto({"enabled": False}))
        assert json.loads(cfg.read_text())["updates"]["auto"] == "off"

    @pytest.mark.asyncio
    async def test_an_unknown_mode_is_refused(self, monkeypatch, tmp_path) -> None:
        cfg = tmp_path / "config.json"
        monkeypatch.setattr(upd, "config_path", lambda: cfg, raising=False)
        resp = await upd.api_update_auto(_post_auto({"mode": "yolo"}))
        assert resp.status == 400
        assert not cfg.exists()

    @pytest.mark.asyncio
    async def test_a_non_bool_enabled_is_still_refused(
        self, monkeypatch, tmp_path
    ) -> None:
        cfg = tmp_path / "config.json"
        monkeypatch.setattr(upd, "config_path", lambda: cfg, raising=False)
        assert (await upd.api_update_auto(_post_auto({"enabled": "yes"}))).status == 400

    @pytest.mark.asyncio
    async def test_other_config_keys_survive_the_write(
        self, monkeypatch, tmp_path
    ) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "dashboard": {"user_name": "Keyur"},
                    "updates": {"channel": "beta", "pin": "0.2.0"},
                }
            )
        )
        monkeypatch.setattr(upd, "config_path", lambda: cfg, raising=False)
        await upd.api_update_auto(_post_auto({"mode": "staged"}))
        saved = json.loads(cfg.read_text())
        assert saved["dashboard"]["user_name"] == "Keyur"
        assert saved["updates"] == {
            "channel": "beta",
            "pin": "0.2.0",
            "auto": "staged",
        }

    def test_the_developer_mode_endpoint_is_retired(self) -> None:
        """RUM-59 ac_3: the superseded control is gone from the handler AND the
        router — a route left registered is a control that still exists."""
        import inspect

        from gideon.interfaces.dashboard import server as dash_server

        assert not hasattr(upd, "api_update_dev_mode")
        assert "/api/update/dev-mode" not in inspect.getsource(dash_server)
