"""The update settings API refuses unattended updates for manually managed installs."""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.core.config.loader import config_path
from gideon.interfaces.dashboard.handlers import updates


@pytest.mark.parametrize("kind", ("pip", "container", "desktop"))
@pytest.mark.asyncio
async def test_unsupported_install_kind_cannot_enable_auto_update(
    kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(updates.self_update, "detect_install_kind", lambda: kind)
    path = config_path()
    initial = {"auto_update": False, "dashboard": {"theme": "dark"}}
    path.write_text(json.dumps(initial), encoding="utf-8")

    request = make_mocked_request(
        "POST",
        "/api/update/auto",
        headers={"Content-Type": "application/json"},
    )
    request._read_bytes = b'{"enabled": true}'
    response = await updates.api_update_auto(request)

    assert response.status == 409
    assert json.loads(response.body.decode())["auto_update"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == initial
