"""Exercise frozen command dispatch with real child processes; no packaged artifact is claimed."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gideon.core import frozen_child
from gideon.engine import restart_request
from gideon.operations import self_update


@pytest.mark.parametrize("frozen", [False, True])
def test_restart_retains_home_and_removes_one_time_seed_flags(monkeypatch, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "argv", ["gideon", "gateway", "--home", "/tmp/owner-home", "--seed", "fixture",
        "--seed-replace", "--seed-local-model", "--port", "auto", "--json-ready", "--seed=other"])
    expected = [sys.executable] if frozen else [sys.executable, "-m", "gideon"]
    assert self_update.cli_argv() == expected
    assert restart_request.relaunch_argv() == expected + ["gateway", "--home", "/tmp/owner-home", "--port", "auto", "--json-ready"]


def frozen_process(tmp_path, module, arguments=(), stdin=""):
    env = dict(os.environ, GIDEON_HOME=str(tmp_path / "home"), HOME=str(tmp_path),
        _PYI_ARCHIVE_FILE="private-bundle", LD_LIBRARY_PATH="/bundle-only", LD_LIBRARY_PATH_ORIG="/usr/lib",
        XDG_CACHE_HOME="original-child-cache")
    script = (
        "import runpy,sys; sys.frozen=True; sys._MEIPASS='/bundle-only'; "
        f"sys.argv=['bundle','-m',{module!r},*{list(arguments)!r}]; "
        "runpy.run_module('gideon',run_name='__main__')"
    )
    return subprocess.run([sys.executable, "-c", script], env=env, input=stdin, text=True, capture_output=True, timeout=20)


def test_real_ceiling_child_restores_loader_environment_and_runs_command(tmp_path):
    payload = "import os,json; print(json.dumps({k:os.environ.get(k) for k in ['_PYI_ARCHIVE_FILE','LD_LIBRARY_PATH','LD_LIBRARY_PATH_ORIG','XDG_CACHE_HOME']}))"
    result = frozen_process(tmp_path, "gideon.engine._spawn_exec_shim", ["{}", "--", sys.executable, "-c", payload])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"_PYI_ARCHIVE_FILE": None, "LD_LIBRARY_PATH": "/usr/lib",
        "LD_LIBRARY_PATH_ORIG": None, "XDG_CACHE_HOME": "original-child-cache"}


def test_frozen_app_child_keeps_existing_script_argument_interface(tmp_path):
    entry = tmp_path / "entry.py"
    entry.write_text("import sys; print('app-child:'+sys.argv[1])")
    result = frozen_process(tmp_path, "gideon._app_python_child", [str(entry), "accepted"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "app-child:accepted"


def test_driver_child_uses_native_json_protocol_without_desktop_side_effect(tmp_path):
    result = frozen_process(tmp_path, "gideon.integrations.computer_use.driver_host", stdin="not-json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["error"]["why"] == "stdin did not carry a JSON object."


def test_undeclared_frozen_module_is_refused(tmp_path):
    result = frozen_process(tmp_path, "http.server")
    assert result.returncode != 0
    assert frozen_child.child_module(["bundle", "-m", "http.server"]) is None
    assert frozen_child.child_module(["bundle", "-c", "print(1)"]) is None


def test_post_shutdown_exec_failure_is_nonzero_with_actionable_stderr(tmp_path):
    command = (
        "from gideon.engine.restart_request import RestartRequest,reexec; "
        f"reexec(RestartRequest({str(tmp_path / 'missing')!r},('gateway',),'local_token'))"
    )
    env = dict(os.environ, GIDEON_HOME=str(tmp_path / "home"))
    result = subprocess.run([sys.executable, "-c", command], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 1
    assert "Gideon could not restart" in result.stderr
    assert "Traceback" not in result.stderr


def test_frozen_namespace_uses_declared_module_without_interpreter_script_argument(monkeypatch, tmp_path):
    from gideon.security import sandbox
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    command = sandbox.namespace_argv([sys.executable, "-c", "pass"])
    assert command[:3] == [sys.executable, "-m", "gideon.security.namespace_child"]
    assert "--" in command
    assert frozen_child.child_module(command) == "gideon.security.namespace_child"
