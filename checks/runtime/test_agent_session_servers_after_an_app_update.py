"""An app update reports a real server process that still holds the previous tree."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, manager


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    yield tmp_path
    if manager._read_installed("session-server-app") is not None:
        app_manager.force_uninstall("session-server-app")


def _source(tmp_path: Path, version: str, subdir: str) -> Path:
    root = tmp_path / subdir / "session-server-app"
    root.mkdir(parents=True)
    (root / "app.json").write_text(
        json.dumps({
            "name": "session-server-app",
            "version": version,
            "displayName": "Session Server",
            "description": "An installed app with a live server process.",
            "provider": {"type": "channel", "implementation": "provider:create_provider"},
        }),
        encoding="utf-8",
    )
    (root / "provider.py").write_text(
        "def create_provider(config=None):\n    return None\n", encoding="utf-8"
    )
    return root


def test_update_exposes_live_previous_version_process(tmp_path):
    assert app_manager.install(_source(tmp_path, "1.0.0", "first")).ok
    installed_root = manager.app_dir("session-server-app")
    server = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=installed_root
    )
    try:
        result = app_manager.update(_source(tmp_path, "2.0.0", "second"))
        assert result.ok
        assert result.restart_reason
        assert str(server.pid) in result.restart_reason
        assert "process" in result.restart_reason.lower()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_disabled_backend_stays_held_across_a_watchdog_sweep(tmp_path, monkeypatch):
    """The real watchdog cannot restart a child while its app tree is being retired."""
    from gideon.extensions.apps import backend_runtime
    from gideon.extensions.apps.manifest import AppManifest

    app_name = "watchdog-hold-app"
    monkeypatch.delenv("GIDEON_SKIP_APP_BACKENDS", raising=False)
    source = _source(tmp_path, "1.0.0", "watchdog-source")
    backend = source / "backend"
    backend.mkdir()
    ready = tmp_path / "backend-started.log"
    (backend / "server.py").write_text(
        "import pathlib, time\n"
        f"pathlib.Path({str(ready)!r}).open('a').write('started\\n')\n"
        "while True: time.sleep(0.05)\n",
        encoding="utf-8",
    )
    manifest_path = source / "app.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["name"] = app_name
    document["backend"] = {"entryPoint": "backend/server.py", "type": "python"}
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    try:
        assert app_manager.install(source).ok
        live_manifest = AppManifest.from_json_file(manager.app_dir(app_name) / "app.json")
        supervisor = backend_runtime.get_backend_supervisor()
        process = supervisor.get(app_name)
        assert process is not None and process.is_alive()
        deadline = __import__("time").monotonic() + 5
        while not ready.exists() and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.02)
        assert ready.read_text(encoding="utf-8").splitlines() == ["started"]

        assert app_manager.disable(app_name)
        assert supervisor.is_held(app_name)
        backend_runtime._check_and_revive()
        assert supervisor.start(live_manifest) is None
        assert ready.read_text(encoding="utf-8").splitlines() == ["started"]
    finally:
        app_manager.force_uninstall(app_name)
