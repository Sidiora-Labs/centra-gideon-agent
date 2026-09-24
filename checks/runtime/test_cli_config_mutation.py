"""File imports and key removal operate on real isolated configuration documents."""

import argparse
import json
import sys

import pytest

from gideon.core.config import AppConfig
from gideon.interfaces.cli.config import _config_cmd
from gideon.interfaces.cli.main import main


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("GIDEON_PORT", raising=False)
    return tmp_path / "config.json"


def import_file(config_file, data):
    incoming = config_file.with_name("incoming.json")
    incoming.write_text(json.dumps(data))
    _config_cmd(argparse.Namespace(config_action="set", file=str(incoming)))


def unset(key):
    _config_cmd(argparse.Namespace(config_action="unset", key=key))


@pytest.mark.parametrize(
    "block",
    [
        {"unknown_section": {"enabled": True}},
        {"dashboard": "wrong shape"},
        {"dashboard": {"port": 8888}},
        {"agent": {"max_subagents": "not an integer"}},
        {"providers": []},
        {"agents": {"research": {"not_a_field": True}}},
    ],
)
def test_file_refusal_is_atomic(config_file, block, capsys):
    original = '{"timezone": "UTC", "providers": {"private": {"enabled": true}}}\n'
    config_file.write_text(original)
    with pytest.raises(SystemExit) as result:
        import_file(config_file, {"timezone": "Europe/Berlin", **block})
    assert result.value.code == 1
    assert config_file.read_text() == original
    output = capsys.readouterr()
    assert "cannot apply" in output.err
    assert "Config loaded" not in output.out


def test_file_import_applies_supported_blocks_and_preserves_existing(config_file):
    preserved = {"custom_section": {"version": 2}, "slack": {"channels": ["team"]}}
    config_file.write_text(json.dumps(preserved))
    data = {
        "dashboard": {"url": "http://localhost:8888"},
        "providers": {"private": {"enabled": True}},
        "use_cases": {"chat": "private"},
        "agents": {"research": {"model": "private:small"}},
    }
    import_file(config_file, data)
    stored = json.loads(config_file.read_text())
    assert {key: stored[key] for key in data} == data
    assert {key: stored[key] for key in preserved} == preserved
    loaded = AppConfig.load()
    assert loaded.dashboard.url == "http://localhost:8888"
    assert loaded.agents["research"].model == "private:small"


def test_exported_config_can_roundtrip_with_unchanged_opaque_block(config_file):
    data = AppConfig().to_dict()
    data["custom_section"] = {"version": 2}
    config_file.write_text(json.dumps(data))
    import_file(config_file, data)
    stored = json.loads(config_file.read_text())
    assert {key: stored[key] for key in data} == data


def test_unset_leaf_preserves_siblings_and_opaque_sections(config_file):
    data = {
        "dashboard": {
            "url": "http://localhost:8888",
            "public_url": "https://example.test",
        },
        "providers": {"private": {"enabled": True}},
        "custom_section": {"version": 2},
    }
    config_file.write_text(json.dumps(data))
    unset("dashboard.url")
    del data["dashboard"]["url"]
    assert json.loads(config_file.read_text()) == data
    assert AppConfig.load().dashboard.url == AppConfig().dashboard.url


@pytest.mark.parametrize("key", ["timezone", "providers", "providers.private.enabled"])
def test_unset_can_remove_root_and_opaque_keys(config_file, key):
    data = {"timezone": "UTC", "providers": {"private": {"enabled": False}}, "other": 7}
    config_file.write_text(json.dumps(data))
    unset(key)
    owner = data
    parts = key.split(".")
    for part in parts[:-1]:
        owner = owner[part]
    del owner[parts[-1]]
    assert json.loads(config_file.read_text()) == data


@pytest.mark.parametrize("exists", [False, True])
def test_unset_absent_known_key_is_a_no_write_success(config_file, exists, capsys):
    original = '{"custom_section": {"version": 2}}\n'
    if exists:
        config_file.write_text(original)
    unset("dashboard.url")
    assert "not set" in capsys.readouterr().out
    assert config_file.exists() == exists
    if exists:
        assert config_file.read_text() == original


@pytest.mark.parametrize(
    "key",
    [
        "",
        ".dashboard",
        "dashboard.",
        "dashboard..url",
        "dashboard. url",
        "unknown.key",
        "dashboard.url.child",
    ],
)
def test_unset_invalid_key_never_changes_file(config_file, key):
    original = '{"dashboard": {"url": "http://localhost:8888"}}\n'
    config_file.write_text(original)
    with pytest.raises(SystemExit) as result:
        unset(key)
    assert result.value.code == 1
    assert config_file.read_text() == original


@pytest.mark.parametrize("original", ["{invalid", "[]", "null"])
def test_unset_refuses_unreadable_document(config_file, original):
    config_file.write_text(original)
    with pytest.raises(SystemExit) as result:
        unset("dashboard.url")
    assert result.value.code == 1
    assert config_file.read_text() == original


def test_unset_is_wired_through_cli_parser(config_file, monkeypatch, capsys):
    config_file.write_text('{"dashboard": {"url": "http://localhost:8888"}}')
    monkeypatch.setattr(sys, "argv", ["gideon", "config", "unset", "dashboard.url"])
    monkeypatch.chdir(config_file.parent)
    main()
    assert "Unset dashboard.url" in capsys.readouterr().out
    assert json.loads(config_file.read_text()) == {"dashboard": {}}
