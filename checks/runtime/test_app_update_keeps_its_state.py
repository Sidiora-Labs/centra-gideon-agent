from __future__ import annotations

import json
import os
import venv
from pathlib import Path

from gideon.extensions.apps import app_manager, manager


def _bundle(root: Path, version: str, *, update_hook: str = "") -> Path:
    app = root / "service-client"
    app.mkdir(parents=True)
    manifest = {
        "name": "service-client",
        "version": version,
        "displayName": "Service Client",
        "description": "Keeps its local provider engine during updates.",
        "setup": {"onUpdate": update_hook} if update_hook else {},
    }
    (app / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (app / "entry.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    return app


def test_update_carries_the_real_environment_through_hook_and_failure_rollback(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    first = _bundle(tmp_path / "first", "1.0.0")
    assert app_manager.install(first, confirm=True).ok
    live = manager.app_dir("service-client")
    venv.EnvBuilder(with_pip=False).create(live / "venv")
    interpreter_inode = os.stat(live / "venv" / "bin" / "python").st_ino
    (live / "data").mkdir(exist_ok=True)
    (live / "data" / "saved.txt").write_text("kept", encoding="utf-8")

    hook = "test -x venv/bin/python && touch engine-visible-to-update-hook"
    second = _bundle(tmp_path / "second", "2.0.0", update_hook=hook)
    result = app_manager.update(second, confirm=True)

    assert result.ok, result.error
    assert (live / "engine-visible-to-update-hook").is_file()
    assert os.stat(live / "venv" / "bin" / "python").st_ino == interpreter_inode
    assert (live / "data" / "saved.txt").read_text(encoding="utf-8") == "kept"

    third = _bundle(tmp_path / "third", "3.0.0", update_hook="exit 7")
    failed = app_manager.update(third, confirm=True)
    assert not failed.ok
    assert manager._read_installed("service-client").version == "2.0.0"
    assert os.stat(live / "venv" / "bin" / "python").st_ino == interpreter_inode
    assert (live / "data" / "saved.txt").read_text(encoding="utf-8") == "kept"
