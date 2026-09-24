"""Advertised config keys must resolve through the CLI's actual key lookup."""

import re
import shlex
import sys

import pytest

from gideon.core.config import AppConfig
from gideon.interfaces.cli.config import _MISSING, _dict_get, _dict_set, _parse_value
from gideon.interfaces.cli.main import main


@pytest.mark.parametrize("subcommand", [[], ["get"], ["set"]])
def test_config_help_keys_resolve(subcommand, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("GIDEON_PORT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["gideon", "config", *subcommand, "--help"])

    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "dashboard.port" not in help_text

    keys = re.findall(r"Dot-separated key \(e\.g\. ([\w.]+)\)", help_text)
    examples = []
    for line in help_text.splitlines():
        command = shlex.split(line, comments=True)
        if command[:3] in (["gideon", "config", "get"], ["gideon", "config", "set"]):
            if len(command) > 3 and not command[3].startswith("-"):
                keys.append(command[3])
                examples.append(command)

    assert keys, "The help-key rail must inspect at least one advertised key"
    assert "dashboard.url" in keys
    configuration = AppConfig().to_dict()
    for key in keys:
        assert _dict_get(configuration, key) is not _MISSING, key
    for command in examples:
        if command[2] == "set":
            assert len(command) == 5, command
            key, value = command[3], _parse_value(command[4])
            assert isinstance(value, type(_dict_get(configuration, key)))
            assert _dict_set(configuration, key, value)
            assert _dict_get(configuration, key) == value


def test_retired_port_key_is_not_a_config_setting():
    configuration = AppConfig().to_dict()
    assert _dict_get(configuration, "dashboard.port") is _MISSING
    assert not _dict_set(configuration, "dashboard.port", 8888)
