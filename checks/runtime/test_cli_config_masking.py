"""Config rendering masks credentials without a named block or list entry."""

import argparse
import json

import pytest

from gideon.core.config import AppConfig
from gideon.core.config.document import CREDENTIAL_MASK
from gideon.interfaces.cli.config import _config_cmd


@pytest.mark.parametrize("key", [None, "custom_extension", "custom_extension.entries"])
def test_unnamed_config_blocks_mask_credentials_on_render(
    tmp_path, monkeypatch, capsys, key
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    fields = (
        "api_key",
        "APIKEY",
        "client_secret",
        "access_token",
        "refresh_token",
        "session_token",
        "bot_token",
        "app_token",
        "password",
        "passwd",
        "credential",
        "private_key",
        "bearer_token",
        "signing_key",
        "webhook_secret",
    )
    credentials = {
        field: f"private-value-{index}" for index, field in enumerate(fields)
    }
    entry = {**credentials, "enabled": True, "endpoint": "https://example.test"}
    block = {"entries": [entry, {"nested": credentials}], "label": "visible label"}
    data = {"custom_extension": block}
    assert "custom_extension" not in AppConfig().to_dict()
    assert all("name" not in item for item in block["entries"])
    path = tmp_path / "config.json"
    original = json.dumps(data)
    path.write_text(original)

    _config_cmd(argparse.Namespace(config_action="get", key=key))

    captured = capsys.readouterr()
    for secret in credentials.values():
        assert secret not in captured.out
        assert secret not in captured.err
    rendered = json.loads(captured.out)
    if key is None:
        rendered = rendered["custom_extension"]
    entries = rendered if key == "custom_extension.entries" else rendered["entries"]
    masked = {field: CREDENTIAL_MASK for field in fields}
    assert entries == [
        {**masked, "enabled": True, "endpoint": "https://example.test"},
        {"nested": masked},
    ]
    if key != "custom_extension.entries":
        assert rendered["label"] == "visible label"
    assert path.read_text() == original


def test_direct_credential_lookup_masks_before_key_selection(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    original = '{"arbitrary_block": {"password": "private-value"}}'
    path.write_text(original)

    _config_cmd(argparse.Namespace(config_action="get", key="arbitrary_block.password"))

    captured = capsys.readouterr()
    assert captured.out.strip() == CREDENTIAL_MASK
    assert "private-value" not in captured.err
    assert path.read_text() == original


def test_noncredentials_and_empty_values_remain_readable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    block = {
        "password": "",
        "api_key": None,
        "enabled": False,
        "token_limit": 1024,
        "secretary": "visible",
        "labels": ["one", "two"],
    }
    path = tmp_path / "config.json"
    original = json.dumps({"arbitrary_block": block})
    path.write_text(original)

    _config_cmd(argparse.Namespace(config_action="get", key="arbitrary_block"))

    assert json.loads(capsys.readouterr().out) == block
    assert path.read_text() == original
