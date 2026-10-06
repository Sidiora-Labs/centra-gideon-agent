"""Sidecars need no ensurepip and use the active environment's declared pip."""

import os
import subprocess
import sys
from pathlib import Path

from gideon.integrations.local_models.sidecar import SidecarInstall, venv_python
from gideon.operations._installer import env_install_argv, prefix_install_argv

_REPO = Path(__file__).resolve().parents[2]


def test_real_sidecar_environment_has_no_pip_and_accepts_running_pip_probe(tmp_path):
    install = SidecarInstall("offline-probe", venv=tmp_path / "venv")
    assert install._step_venv()[0] == "done"
    python = venv_python(install.venv)
    subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.util; assert importlib.util.find_spec('pip') is None",
        ],
        check=True,
    )
    argv = env_install_argv(python, ["--help"])
    assert argv[:3] == [sys.executable, "-m", "pip"]
    assert argv[3:6] == ["--python", str(python), "install"]
    result = subprocess.run(argv, capture_output=True, text=True, check=True)
    assert "Usage:" in result.stdout
    assert not install._receipt_path().exists()
    assert install._step_venv()[0] == "skipped"


def test_prefix_installer_resolves_against_current_interpreter():
    argv = prefix_install_argv(["--prefix", "/tmp/isolated-prefix", "--help"])
    assert argv[:4] == [sys.executable, "-m", "pip", "install"]
    result = subprocess.run(argv, check=True, capture_output=True, text=True)
    assert "Usage:" in result.stdout


def test_stripped_interpreter_refuses_without_claiming_install_success():
    code = (
        "from gideon.operations._installer import env_install_argv, NoInstallerError; "
        "\ntry: env_install_argv('/tmp/pipless-python', [])"
        "\nexcept NoInstallerError as error: print(error.problem); print(error.fix)"
        "\nelse: raise AssertionError('missing pip was accepted')"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code],
        env=dict(os.environ, PYTHONPATH=str(_REPO / "runtime")),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "has no pip module" in result.stdout
    assert "reinstall Gideon" in result.stdout
    assert "ensurepip" not in result.stdout


def test_app_prefix_consumer_uses_prefix_install_contract():
    import ast

    path = _REPO / "runtime/gideon/extensions/apps/app_python.py"
    tree = ast.parse(path.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_pip_install"
    )
    calls = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "prefix_install_argv" in calls
    assert "install_argv" not in calls
