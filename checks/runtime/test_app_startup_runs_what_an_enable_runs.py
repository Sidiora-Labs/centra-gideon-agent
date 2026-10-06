"""Gateway startup and user enable share the same in-process app registration path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, app_runtime, manager


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("APP_RUNTIME_TRACE", str(tmp_path / "imports.log"))
    yield tmp_path
    if manager._read_installed("startup-path-app") is not None:
        app_manager.force_uninstall("startup-path-app")


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source" / "startup-path-app"
    root.mkdir(parents=True)
    (root / "app.json").write_text(
        json.dumps(
            {
                "name": "startup-path-app",
                "version": "1.0.0",
                "displayName": "Startup Path",
                "description": "Lifecycle registration fixture.",
                "provider": {
                    "type": "channel",
                    "implementation": "provider:create_provider",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "provider.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['APP_RUNTIME_TRACE']).open('a').write('provider\\n')\n"
        "def create_provider(config=None):\n    return None\n",
        encoding="utf-8",
    )
    return root


def test_startup_and_enable_import_the_same_installed_registration(tmp_path):
    assert app_manager.install(_source(tmp_path)).ok
    assert app_manager.disable("startup-path-app")
    trace = tmp_path / "imports.log"
    trace.write_text("", encoding="utf-8")

    meta = manager._read_installed("startup-path-app")
    meta.enabled = True
    manager._write_installed("startup-path-app", meta)
    assert app_runtime.start_installed(gateway=False) == ["startup-path-app"]
    startup_imports = trace.read_text(encoding="utf-8").splitlines()

    assert app_manager.disable("startup-path-app")
    trace.write_text("", encoding="utf-8")
    assert app_manager.enable("startup-path-app")
    enable_imports = trace.read_text(encoding="utf-8").splitlines()

    assert startup_imports == ["provider"]
    assert enable_imports == startup_imports
