"""An update retires the old app module before the installed tree changes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, manager
from gideon.extensions.apps.native_contract import namespaced_module_name


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    yield tmp_path
    if manager._read_installed("switchboard-app") is not None:
        app_manager.force_uninstall("switchboard-app")


def _source(tmp_path: Path, *, version: str, subdir: str = "source", update_hook: str = "") -> Path:
    root = tmp_path / subdir / "switchboard-app"
    (root / "ui").mkdir(parents=True)
    manifest = {
        "name": "switchboard-app",
        "version": version,
        "displayName": "Switchboard",
        "description": "Versioned lifecycle fixture.",
        "provider": {"type": "channel", "implementation": "provider:create_provider"},
    }
    if update_hook:
        manifest["setup"] = {"onUpdate": update_hook}
    (root / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "provider.py").write_text(
        f"VERSION = {version!r}\n\ndef create_provider(config=None):\n    return None\n",
        encoding="utf-8",
    )
    (root / "ui" / "page.js").write_text(f"export const version = {version!r};\n", encoding="utf-8")
    return root


def test_update_reimports_current_provider_and_ui(tmp_path):
    assert app_manager.install(_source(tmp_path, version="1.0.0")).ok
    module_name = namespaced_module_name("switchboard-app", "provider")
    first = sys.modules[module_name]
    assert first.VERSION == "1.0.0"

    result = app_manager.update(_source(tmp_path, version="2.0.0", subdir="next"))

    assert result.ok
    second = sys.modules[module_name]
    assert second is not first
    assert second.VERSION == "2.0.0"
    assert result.ui_revision
    assert "reload the console" in result.restart_reason
    assert manager._read_installed("switchboard-app").version == "2.0.0"
    assert app_manager.force_uninstall("switchboard-app")


def test_failed_update_restores_and_reloads_previous_version(tmp_path):
    assert app_manager.install(_source(tmp_path, version="1.0.0")).ok
    module_name = namespaced_module_name("switchboard-app", "provider")
    first = sys.modules[module_name]

    result = app_manager.update(
        _source(tmp_path, version="2.0.0", subdir="bad", update_hook="exit 7")
    )

    assert not result.ok
    restored = sys.modules[module_name]
    assert restored is not first
    assert restored.VERSION == "1.0.0"
    assert manager._read_installed("switchboard-app").version == "1.0.0"
    assert app_manager.force_uninstall("switchboard-app")


def test_update_keeps_a_disabled_app_disabled(tmp_path):
    assert app_manager.install(_source(tmp_path, version="1.0.0")).ok
    assert app_manager.disable("switchboard-app")
    module_name = namespaced_module_name("switchboard-app", "provider")
    assert module_name not in sys.modules

    result = app_manager.update(_source(tmp_path, version="2.0.0", subdir="next"))

    assert result.ok
    assert manager._read_installed("switchboard-app").enabled is False
    from gideon.extensions.providers.registry import get_provider_registry

    provider = get_provider_registry().get("switchboard-app")
    assert provider is not None
    assert all(not item.enabled for item in provider.chain())
    assert module_name not in sys.modules
