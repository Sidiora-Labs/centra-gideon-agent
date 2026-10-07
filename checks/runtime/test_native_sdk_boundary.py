"""Native app contracts use SDK facades without broadening write or grant authority."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def test_channel_and_model_exports_preserve_native_contracts():
    from gideon.integrations import channel_delivery
    from gideon.integrations.llm.events import ContextUsage
    from gideon.sdk import channel, model
    from gideon.security.approval_answer import CHANNEL

    for name in (
        "ApprovalAnswer",
        "ONE_CALL_ANSWERS",
        "offered_answers",
        "raw_delivery_for",
    ):
        assert getattr(channel, name) is getattr(channel_delivery, name)
        assert name in channel.__all__
    assert model.ContextUsage is ContextUsage
    assert callable(model.served_on_this_machine)
    assert channel.on_channel("slack", "person", "team").kind == CHANNEL
    assert channel.offered_answers([channel.ONE_CALL_ANSWERS[0].as_dict()]) is None
    for forbidden in ("YOU", "grant_chat_trust", "ingress_record"):
        assert forbidden not in channel.__all__ and not hasattr(channel, forbidden)


def test_unbound_config_caller_has_no_native_write_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.core.config.loader import config_path
    from gideon.core.config.transactions import ConfigWriteError
    from gideon.sdk import settings

    target = config_path()
    target.write_text('{"default_agent":"old"}')
    before = target.read_bytes()
    with pytest.raises(ConfigWriteError, match="admitted native"):
        settings.mutate_channel_config(
            lambda values: values.update(default_agent="new")
        )
    assert target.read_bytes() == before
    assert "mutate_config" not in settings.__all__
    assert not hasattr(settings, "mutate_config")


def test_native_settings_migration_preserves_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.core.config.loader import config_path
    from gideon.core.config.transactions import ConfigWriteError
    from gideon.extensions.apps import code_provenance
    from gideon.extensions.apps.native_contract import NATIVE_DIR
    from gideon.sdk import settings

    bundle = NATIVE_DIR / "gideonai-slack-desk"
    source = bundle / "slack_desk_runtime/settings.py"
    spec = importlib.util.spec_from_file_location("native_sdk_settings", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    target = config_path()
    baseline = {
        "default_agent": "old",
        "security": {"outside_home": []},
        "slack": {"command": "/gideon", "owner_id": "retained"},
    }
    target.write_text(json.dumps(baseline))
    captured = {}

    def update(app, values):
        captured.update(values)
        return values

    def verify_scope():
        before = target.read_bytes()
        for mutation in (
            lambda document: document.update(security={"outside_home": ["/"]}),
            lambda document: document["slack"].update(owner_id="replacement"),
            lambda document: document["slack"].update(command="replacement"),
        ):
            with pytest.raises(ConfigWriteError, match="allowed fields"):
                settings.mutate_channel_config(mutation)
            assert target.read_bytes() == before
        with pytest.raises(ConfigWriteError, match="active config"):
            settings.mutate_channel_config(
                lambda document: None, path=tmp_path / "foreign.json"
            )
        assert not (tmp_path / "foreign.json").exists()
        settings.mutate_channel_config(
            lambda document: document.update(default_agent="new")
        )

    try:
        with code_provenance.loading("gideonai-slack-desk", bundle):
            spec.loader.exec_module(module)
            monkeypatch.setattr(module.ProviderSettings, "load", lambda _app: {})
            monkeypatch.setattr(module.ProviderSettings, "update", update)
            monkeypatch.setattr(module, "_migrated_marker_present", lambda: False)
            monkeypatch.setattr(module, "_mark_migration_done", verify_scope)
            module.migrate_from_core()
        result = json.loads(target.read_text())
        assert captured == {"command": "/gideon"}
        assert result["slack"] == {"owner_id": "retained"}
        assert result["security"] == baseline["security"]
        assert result["default_agent"] == "new"
    finally:
        code_provenance.release("gideonai-slack-desk")
