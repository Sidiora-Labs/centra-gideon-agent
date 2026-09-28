"""App-prefix import activation keeps gateway and standard-library paths first."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from gideon.extensions.apps import app_python
from gideon.extensions.apps.app_python import PackageInstallError
from gideon.operations.durability import inventory
import pytest


def test_prefix_paths_are_appended_and_inventory_ignores_rebuildable_tree(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    app_sites = app_python.site_dirs()
    for path in app_sites:
        path.mkdir(parents=True, exist_ok=True)

    before = list(sys.path)
    app_python.activate()
    stdlib = str(Path(sysconfig_path("stdlib")).resolve())
    site_index = sys.path.index(str(app_sites[0]))
    assert site_index > next(index for index, value in enumerate(sys.path) if value and Path(value).resolve() == Path(stdlib))
    assert all(value in sys.path for value in before)
    assert inventory.is_ignored("app-python/lib/site-packages/probe.py")

    env = app_python.app_packages_env()
    assert env["GIDEON_APP_PYTHON_PATH"] == os.pathsep.join(str(path) for path in app_sites)
    assert Path(env["PYTHONPATH"]).resolve() == (app_python.root() / "bootstrap").resolve()
    child = subprocess.run(
        [sys.executable, "-c", "import json, pathlib, sys; print(json.dumps([pathlib.__file__, sys.path]))"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    child_paths = json.loads(child.stdout)[1]
    assert child_paths.index(str(app_sites[0])) > child_paths.index(stdlib)

    outside = tmp_path / "outside"
    outside.mkdir()
    bad_home = tmp_path / "bad-home"
    bad_home.mkdir()
    (bad_home / "app-python").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("GIDEON_HOME", str(bad_home))
    with pytest.raises(PackageInstallError):
        app_python.root()


def sysconfig_path(name: str) -> str:
    import sysconfig

    return sysconfig.get_path(name)
