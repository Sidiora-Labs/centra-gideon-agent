"""Unsupported frozen app children refuse; allowlisted native launchers survive."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_real_frozen_lifecycle_consumers_refuse_before_artifacts(tmp_path):
    script = tmp_path / "parse.py"
    script.write_text('raise AssertionError("unsupported child executed")\n')
    code = f"""import sys
from pathlib import Path
sys.frozen=True
from gideon.core.python_children import NeedsInterpreter
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.apps.app_manager import _core_version_gate, AppLifecycleError
from gideon.extensions.apps.backend_runtime import BackendSupervisor
from gideon.integrations.knowledge_providers.pack_parse import run_parse_script
from gideon.integrations.local_models.sidecar import SidecarInstall
script=Path({str(script)!r})
source=AppManifest.from_dict({{"name":"parser-probe","version":"1.0.0","sources":[{{"name":"source","parse":"parse.py"}}]}})
assert source.sources
assert not source.core_compatibility().admits
try: _core_version_gate(source,action="install")
except AppLifecycleError as exc: assert "desktop" in str(exc)
else: raise AssertionError("unsupported install admitted")
try: run_parse_script(script,b"input")
except NeedsInterpreter: pass
else: raise AssertionError("unsupported parser admitted")
venv=Path({str(tmp_path/'engine')!r})
install=SidecarInstall("engine-probe",venv=venv,cache_root=Path({str(tmp_path/'cache')!r}))
try: install._step_venv()
except NeedsInterpreter: pass
else: raise AssertionError("unsupported venv admitted")
assert not venv.exists()
cmd=BackendSupervisor._launch_cmd("python",script)
assert cmd==[sys.executable,"-m","gideon._app_python_child",str(script)]
supported=AppManifest.from_dict({{"name":"native-probe","version":"1.0.0","backend":{{"type":"python","entryPoint":"parse.py"}}}})
assert supported.core_compatibility().admits
missing=AppManifest.from_dict({{"name":"deps-probe","version":"1.0.0","dependencies":{{"pythonDependencies":["gideon-missing-frozen-probe==1"]}}}})
assert not missing.core_compatibility().admits
print("unsupported lifecycle refused; native launcher retained")
"""
    env = dict(
        os.environ,
        GIDEON_HOME=str(tmp_path / "home"),
        HOME=str(tmp_path),
        PYTHONPATH=str(ROOT / "runtime"),
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "native launcher retained" in result.stdout
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "engine").exists()
