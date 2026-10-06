"""Real price HTTP routes, settings transactions and recent native usage."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import ClientSession, web

import gideon.sdk.model
from gideon.interfaces.dashboard.handlers.model_rates import (
    _owner_only,
    register_model_rates_routes,
)


@pytest.fixture
async def prices(tmp_path, monkeypatch):
    from gideon.core.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader, "resolve_config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": [{"name": "Cloud", "type": "openai"}], "marker": True})
    )
    (tmp_path / "active_models.json").write_text(json.dumps({"chat": ["Cloud:gpt-4o"]}))
    (tmp_path / "usage").mkdir()
    now = datetime.now(timezone.utc)
    rows = [
        {"provider": "Retired", "model": "whisper-1", "ts": now.isoformat()},
        {
            "provider": "Old",
            "model": "old-model",
            "ts": (now - timedelta(days=31)).isoformat(),
        },
    ]
    (tmp_path / "usage/turns.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows)
    )
    app = web.Application()
    register_model_rates_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    async with ClientSession() as client:
        yield client, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/models/rates", tmp_path
    await runner.cleanup()


@pytest.mark.asyncio
async def test_unit_edit_reset_and_recent_unbound_model(prices):
    client, url, home = prices
    async with client.get(url) as response:
        view = await response.json()
        assert response.status == 200
    refs = {row["ref"] for row in view["models"]}
    assert {"Cloud:gpt-4o", "Retired:whisper-1"} <= refs
    assert "Old:old-model" not in refs
    row = next(row for row in view["models"] if row["ref"] == "Retired:whisper-1")
    assert row["unit"] == "minute" and row["per_unit"] == 0.006
    async with client.put(
        url, json={"key": "Retired:whisper-1", "unit": "minute", "per_minute": 0.1}
    ) as response:
        assert response.status == 200
        view = await response.json()
    row = next(row for row in view["models"] if row["ref"] == "Retired:whisper-1")
    assert row["source"] == "overlay" and row["per_unit"] == 0.1
    assert row["default"]["per_unit"] == 0.006
    assert json.loads((home / "config.json").read_text())["marker"] is True
    async with client.delete(url, params={"key": "Retired:whisper-1"}) as response:
        assert response.status == 200
        view = await response.json()
    row = next(row for row in view["models"] if row["ref"] == "Retired:whisper-1")
    assert row["source"] == "builtin" and row["per_unit"] == 0.006
    async with client.delete(url, params={"key": "missing"}) as response:
        assert response.status == 404


@pytest.mark.asyncio
async def test_invalid_json_bad_price_and_unreadable_settings_refuse(prices):
    client, url, home = prices
    for payload in [[], {"key": "bad", "unit": "minute", "per_minute": -1}]:
        async with client.put(url, json=payload) as response:
            assert response.status == 400
    async with client.put(
        url, data="{bad", headers={"Content-Type": "application/json"}
    ) as response:
        assert response.status == 400
    config = home / "config.json"
    config.write_text("{bad")
    async with client.put(
        url, json={"key": "new", "in_per_mtok": 1, "out_per_mtok": 2}
    ) as response:
        assert response.status == 409
        assert (await response.json())["error"]["code"] == "model_rates_unreadable"
    assert config.read_text() == "{bad"


@pytest.mark.asyncio
async def test_tier_body_survives_reload(prices):
    client, url, home = prices
    body = {
        "key": "Cloud:image",
        "unit": "image",
        "tiers": [{"size": "1024x1024", "quality": "standard", "per_image": 0.04}],
        "default_size": "1024x1024",
        "default_quality": "standard",
    }
    async with client.put(url, json=body) as response:
        assert response.status == 200
    async with client.get(url) as response:
        row = next(
            row
            for row in (await response.json())["rates"]
            if row["key"] == "Cloud:image"
        )
    assert row["tiers"] == body["tiers"] and row["default_size"] == "1024x1024"
    from gideon.core.config.loader import AppConfig

    assert (
        AppConfig.load().model_prices.overrides["Cloud:image"]["tiers"] == body["tiers"]
    )


def test_app_cannot_read_or_write_owner_prices():
    response = _owner_only({"app": "an-app"})
    assert response.status == 403
    assert _owner_only({}) is None


@pytest.mark.asyncio
async def test_read_missing_home_has_no_side_effect(tmp_path, monkeypatch):
    from gideon.core.config import loader
    from gideon.interfaces.dashboard.handlers.model_rates import _view

    home = tmp_path / "missing"
    monkeypatch.setattr(loader, "resolve_config_dir", lambda: home)
    assert json.loads(_view().text) == {"rates": [], "models": [], "unreadable": ""}
    assert not home.exists()
