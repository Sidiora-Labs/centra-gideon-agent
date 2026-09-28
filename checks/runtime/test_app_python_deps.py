"""Per-app python-dependency mechanism (dep-shedding completion).

Core ships lean; an app declares the heavy libs it needs via
``manifest.dependencies.pythonDependencies`` and the installer pip-installs them
into the shared venv. A newly-installed dep ⇒ the gateway must restart to import
it (surfaced via ``InstallResult.restart_required``).
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager
from gideon.extensions.apps.manifest import AppManifest


def _manifest(deps: list[str]) -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": "dep-app",
            "version": "1.0.0",
            "dependencies": {"pythonDependencies": deps},
            "provider": {"type": "tool", "implementation": "provider:make"},
        }
    )


def _wheel(path: Path) -> Path:
    name = "gideon_storage_probe"
    version = "1.0"
    dist_info = f"{name}-{version}.dist-info"
    entries = {
        f"{name}/__init__.py": "value = 'installed'\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\nName: gideon-storage-probe\nVersion: 1.0\n"
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: local-vector\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = io.StringIO()
    rows = csv.writer(record, lineterminator="\n")
    for entry, contents in entries.items():
        raw = contents.encode()
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        rows.writerow((entry, f"sha256={digest}", len(raw)))
    rows.writerow((f"{dist_info}/RECORD", "", ""))
    entries[f"{dist_info}/RECORD"] = record.getvalue()
    wheel = path / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for entry, contents in entries.items():
            archive.writestr(entry, contents)
    return wheel


def test_manifest_parses_and_roundtrips_python_deps():
    m = _manifest(["faster-whisper>=1.0", "numpy>=1.21,<2"])
    assert m.dependencies.pythonDependencies == [
        "faster-whisper>=1.0",
        "numpy>=1.21,<2",
    ]
    rt = AppManifest.from_dict(m.to_dict())
    assert rt.dependencies.pythonDependencies == m.dependencies.pythonDependencies


def test_no_deps_is_noop_no_restart():
    assert app_manager._install_python_deps(_manifest([])) is False


def test_already_satisfied_dep_needs_no_restart():
    assert app_manager._install_python_deps(_manifest(["pytest"])) is False


def test_missing_dep_triggers_pip_and_restart(monkeypatch):
    calls: list[list[str]] = []

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _OK()

    monkeypatch.setattr(app_manager.subprocess, "run", fake_run)
    result = app_manager._install_python_deps(
        _manifest(["totally-not-a-real-pkg-xyz==9.9.9"])
    )
    assert result is True
    assert calls and "pip" in calls[0] and "install" in calls[0]
    assert "totally-not-a-real-pkg-xyz==9.9.9" in calls[0]


def test_pip_failure_raises_lifecycle_error(monkeypatch):
    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "could not find a version"

    monkeypatch.setattr(app_manager.subprocess, "run", lambda cmd, **kw: _Fail())
    import pytest

    with pytest.raises(app_manager.AppLifecycleError):
        app_manager._install_python_deps(
            _manifest(["totally-not-a-real-pkg-xyz==9.9.9"])
        )


def test_uses_uv_on_a_pip_less_venv(monkeypatch):
    """The #46 repro: a uv-created venv ships no pip, so hardcoding
    ``python -m pip`` made EVERY dep-declaring app un-installable on the project's
    own documented dev setup. The installer must resolve uv instead."""
    from gideon.operations import _installer

    monkeypatch.setattr(_installer, "_have_uv", lambda: True)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)

    calls: list[list[str]] = []

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        app_manager.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _OK())[1]
    )
    assert (
        app_manager._install_python_deps(
            _manifest(["totally-not-a-real-pkg-xyz==9.9.9"])
        )
        is True
    )
    argv = calls[0]
    assert argv[:3] == ["uv", "pip", "install"]
    assert "--python" in argv
    assert "--disable-pip-version-check" not in argv


def test_no_installer_raises_actionable_lifecycle_error(monkeypatch):
    """Previously surfaced as ``pip install failed …: No module named pip``, which
    points at pip when the real answer is usually "uv isn't on PATH"."""
    import pytest

    from gideon.operations import _installer

    monkeypatch.setattr(_installer, "_have_uv", lambda: False)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)

    def unreachable(cmd, **kw):  # pragma: no cover — must fail before spawning
        raise AssertionError("attempted a subprocess with no installer available")

    monkeypatch.setattr(app_manager.subprocess, "run", unreachable)
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(
            _manifest(["totally-not-a-real-pkg-xyz==9.9.9"])
        )
    assert "uv" in str(ei.value)


def test_real_install_uses_no_cache_and_cleans_its_home_scratch(tmp_path):
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    _wheel(wheelhouse)
    child_python = tmp_path / "venv" / "bin" / "python"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(child_python.parent.parent)
    user_home = tmp_path / "user-home"
    active_home = tmp_path / "gideon-home"
    user_home.mkdir()
    active_home.mkdir()
    script = r'''
import json
import sys
from gideon.extensions.apps import app_manager
from gideon.extensions.apps.manifest import AppManifest
manifest = AppManifest.from_dict({
    "name": "storage-probe", "version": "1.0.0",
    "dependencies": {"pythonDependencies": ["gideon-storage-probe @ " + __import__("pathlib").Path(sys.argv[1]).as_uri()]},
    "provider": {"type": "tool", "implementation": "provider:make"},
})
installed = app_manager._install_python_deps(manifest)
from importlib.metadata import version
print(json.dumps({"installed": installed, "version": version("gideon-storage-probe")}))
'''
    env = {
        **os.environ,
        "HOME": str(user_home),
        "GIDEON_HOME": str(active_home),
        "PATH": os.pathsep.join([str(child_python.parent), "/usr/bin", "/bin"]),
        "PYTHONPATH": os.pathsep.join(
            [str(Path(__file__).resolve().parents[2] / "runtime"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    result = subprocess.run(
        [str(child_python), "-c", script, str(next(wheelhouse.glob("*.whl")))],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert '"installed": true' in result.stdout
    assert '"version": "1.0"' in result.stdout
    assert list(user_home.iterdir()) == []
    assert not (active_home / "cache").exists()
    assert list((active_home / "tmp").iterdir()) == []
