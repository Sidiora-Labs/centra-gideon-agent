from __future__ import annotations

import pytest

from gideon.core.config.decoding import decode_configuration
from gideon.core.config.loader import AppConfig
from gideon.integrations import channel_delivery


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path


def test_approval_channel_is_decoded_and_unavailable_origin_does_not_pick_another(
    isolated_home,
):
    config = decode_configuration(
        {"agent": {"approval_channel": "telegram"}}, AppConfig
    )
    assert config.agent.approval_channel == "telegram"

    previous = {
        name: channel_delivery.raw_delivery_for(name)
        for name in channel_delivery.registered_providers()
    }
    channel_delivery.register(None)
    try:
        assert channel_delivery.approval_delivery("unavailable-origin") is None
    finally:
        for name, handle in previous.items():
            if handle is not None:
                channel_delivery.register(handle, provider=name)
