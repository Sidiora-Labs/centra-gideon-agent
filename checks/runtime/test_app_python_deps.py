"""Real local-wheel vectors for app dependency resolution and replacement."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from gideon.extensions.apps.manifest import AppManifest


def _manifest(deps: list[str]) -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": "dependency-app",
            "version": "1.0.0",
            "dependencies": {"pythonDependencies": deps},
        }
    )


def _wheel(path: Path, version: str) -> Path:
    distribution = "gideon_lifecycle_probe"
    dist_info = f"{distribution}-{version}.dist-info"
    entries = {
        "lifecycle_probe/__init__.py": f"VERSION = {version!r}\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\nName: gideon-lifecycle-probe\n"
            f"Version: {version}\n"
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: local-vector\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = io.StringIO()
    rows = csv.writer(record, lineterminator="\n")
    for name, contents in entries.items():
        raw = contents.encode()
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        )
        rows.writerow((name, f"sha256={digest}", len(raw)))
    rows.writerow((f"{dist_info}/RECORD", "", ""))
    entries[f"{dist_info}/RECORD"] = record.getvalue()
    wheel = path / f"{distribution}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    return wheel


def test_manifest_preserves_python_dependencies() -> None:
    manifest = _manifest(["gideon-lifecycle-probe==1.0"])
    restored = AppManifest.from_dict(manifest.to_dict())
    assert restored.dependencies.pythonDependencies == ["gideon-lifecycle-probe==1.0"]


def test_real_prefix_upgrade_downgrade_and_child_bootstrap(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    _wheel(wheelhouse, "1.0")
    _wheel(wheelhouse, "2.0")
    home = tmp_path / "gideon-home"
    home.mkdir()
    runtime = Path(__file__).resolve().parents[2]
    script = r"""
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from gideon.extensions.apps import app_python
from gideon.extensions.apps.manifest import AppManifest
from gideon.core.config.loader import config_dir

def install(requirements):
    return app_python.ensure("dependency-app", requirements, label="dependency-app")

assert install(["gideon-lifecycle-probe==1.0"]) == []
import lifecycle_probe
assert lifecycle_probe.VERSION == "1.0"
up = install(["gideon-lifecycle-probe==2.0"])
assert up and any("1.0" in item and "2.0" in item for item in up), up
assert lifecycle_probe.VERSION == "1.0"
sys.modules.pop("lifecycle_probe")
import lifecycle_probe
assert lifecycle_probe.VERSION == "2.0"
down = install(["gideon-lifecycle-probe==1.0"])
assert down and any("2.0" in item and "1.0" in item for item in down), down
sys.modules.pop("lifecycle_probe")
import lifecycle_probe
assert lifecycle_probe.VERSION == "1.0"
site_dirs = [str(item) for item in app_python.site_dirs() if item.is_dir()]
dists = [d for d in importlib.metadata.distributions(path=site_dirs)
         if d.metadata.get("Name") == "gideon-lifecycle-probe"]
versions = [d.version for d in dists]
assert versions == ["1.0"], versions

entry = Path(os.environ["GIDEON_HOME"]) / "entry.py"
entry.write_text("import json, pathlib, lifecycle_probe; print(json.dumps([lifecycle_probe.VERSION, pathlib.__file__]))\n")
from gideon.security.sandbox import build_child_env
child_env = build_child_env(site="app-python-test")
child_env.update(app_python.child_env())
child = subprocess.run(app_python.child_argv(entry), env=child_env, capture_output=True, text=True, check=True)
assert json.loads(child.stdout)[0] == "1.0", child.stdout
assert "site-packages" not in json.loads(child.stdout)[1], child.stdout

hook_env = app_python.app_packages_env()
hook = subprocess.run([sys.executable, "-c", "import json, pathlib, lifecycle_probe, site, sys; from pathlib import Path; print(json.dumps([lifecycle_probe.VERSION, pathlib.__file__, sys.path.index(str(Path(lifecycle_probe.__file__).parent.parent)), sys.path.index(site.getsitepackages()[0])]))"], env=hook_env, capture_output=True, text=True, check=True)
hook_data = json.loads(hook.stdout)
assert hook_data[0] == "1.0", hook.stdout
assert "site-packages" not in hook_data[1], hook.stdout
assert hook_data[2] > hook_data[3], hook.stdout
marker = Path(os.environ["GIDEON_HOME"]) / "keep.txt"
marker.write_text("preserved")
removed = app_python.collect()
assert any("gideon-lifecycle-probe" in item for item in removed), removed
assert not [d for d in importlib.metadata.distributions(path=site_dirs)
            if d.metadata.get("Name") == "gideon-lifecycle-probe"]
assert marker.read_text() == "preserved"
assert not list((Path(os.environ["GIDEON_HOME"]) / "tmp").iterdir())
print(json.dumps({"up": up, "down": down, "versions": versions, "collected": removed, "child": child.stdout.strip(), "hook": hook.stdout.strip(), "prefix": str(app_python.root())}))
"""
    env = {
        **os.environ,
        "GIDEON_HOME": str(home),
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "PYTHONPATH": str(runtime),
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": str(wheelhouse),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    details = json.loads(result.stdout.strip().splitlines()[-1])
    assert details["versions"] == ["1.0"]
    assert str(home / "app-python") in details["prefix"]
