"""Tests for the app-contributed CLI seams (plan 32: PROVIDER-BOUNDARY-COMPLETION).

Covers ``gideon.extensions.app_cli``:
- ``run_app_setup_steps`` — imports + runs each installed+enabled app's ``cli.setup``
  with a ``SetupContext``; a raising step warns and continues; ``--app`` filters.
- ``run_app_doctor_probes`` — imports + runs each ``cli.doctor`` under a timeout,
  renders ``DoctorLine``s, and turns a hung/raising probe into one fail line.

Fixture apps are written under a tmp ``apps/<name>/`` (installed.json + app.json +
a real module .py) mirroring what ``manager.list_apps()`` + ``app_dir()`` read.
"""

import io
import json
import sys

import pytest

from gideon.extensions import app_cli
from gideon.extensions.apps import manager


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the apps dir at tmp_path so list_apps()/app_dir() read our fixtures."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    from gideon.core.config import loader as cfg_loader

    monkeypatch.setattr(cfg_loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _install_app(root, name, *, module_file="", module_body="", cli=None, enabled=True):
    """Write an installed app: installed.json + app.json (+ optional module .py)."""
    d = root / "apps" / name
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": enabled}),
        encoding="utf-8",
    )
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": name,
    }
    if cli is not None:
        manifest["cli"] = cli
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    if module_file:
        (d / module_file).write_text(module_body, encoding="utf-8")
    return d


def test_setup_step_runs_and_receives_context(_isolate):
    _install_app(
        _isolate,
        "cfg-app",
        module_file="cli_setup.py",
        module_body=(
            "def run(ctx):\n"
            "    ctx.save_credential('CFG_APP_TOKEN', 'xyz')\n"
            "    assert ctx.get_credential('CFG_APP_TOKEN') == 'xyz'\n"
            "    ctx.print('cfg-app configured')\n"
        ),
        cli={"setup": "cli_setup:run"},
    )
    failures = app_cli.run_app_setup_steps()
    assert failures == []
    env = (_isolate / ".env").read_text(encoding="utf-8")
    assert "CFG_APP_TOKEN=xyz" in env


def test_setup_step_that_raises_does_not_abort(_isolate, capsys):
    bad_dir = _install_app(
        _isolate,
        "a-bad",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    from support import VALUE\n    raise RuntimeError(VALUE + ' boom')\n",
        cli={"setup": "cli_setup:run"},
    )
    _add_support_package(bad_dir)
    _install_app(
        _isolate,
        "b-missing",
        cli={"setup": "missing_setup:run"},
    )
    _install_app(
        _isolate,
        "z-good",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('z-good ran')\n",
        cli={"setup": "cli_setup:run"},
    )
    failures = app_cli.run_app_setup_steps()
    out = capsys.readouterr().out
    assert "a-bad" in out and "boom" in out
    assert any("a-bad: RuntimeError: from-app-package boom" == failure for failure in failures)
    assert any("b-missing: ImportError:" in failure and "missing_setup" in failure for failure in failures)
    assert "z-good ran" in out
    assert str(bad_dir.resolve()) not in sys.path


def test_setup_only_app_filter(_isolate, capsys):
    _install_app(
        _isolate,
        "one",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('ONE ran')\n",
        cli={"setup": "cli_setup:run"},
    )
    _install_app(
        _isolate,
        "two",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('TWO ran')\n",
        cli={"setup": "cli_setup:run"},
    )
    app_cli.run_app_setup_steps(only_app="two")
    out = capsys.readouterr().out
    assert "TWO ran" in out
    assert "ONE ran" not in out


def test_setup_disabled_app_skipped(_isolate, capsys):
    _install_app(
        _isolate,
        "off",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('OFF ran')\n",
        cli={"setup": "cli_setup:run"},
        enabled=False,
    )
    app_cli.run_app_setup_steps()
    assert "OFF ran" not in capsys.readouterr().out


def test_doctor_probe_renders_lines(_isolate, capsys):
    _install_app(
        _isolate,
        "probe-app",
        module_file="cli_doctor.py",
        module_body=(
            "from gideon.sdk.cli import DoctorLine\n"
            "def probe():\n"
            "    return [DoctorLine('token', 'ok', 'present'),\n"
            "            DoctorLine('workspace', 'fail', 'unreachable')]\n"
        ),
        cli={"doctor": "cli_doctor:probe"},
    )
    issues = app_cli.run_app_doctor_probes()
    out = capsys.readouterr().out
    assert "probe-app" in out and "token" in out and "workspace" in out
    assert any("workspace" in i for i in issues)


def test_doctor_probe_timeout_does_not_hang(_isolate, capsys):
    monkey_timeout = 0.3
    import gideon.extensions.app_cli as ac

    ac._DOCTOR_TIMEOUT_SECS = monkey_timeout
    _install_app(
        _isolate,
        "hang-app",
        module_file="cli_doctor.py",
        module_body="import time\ndef probe():\n    time.sleep(5)\n    return []\n",
        cli={"doctor": "cli_doctor:probe"},
    )
    issues = app_cli.run_app_doctor_probes()
    out = capsys.readouterr().out
    assert "hang-app" in out and "probe error" in out
    assert any("hang-app" in i for i in issues)


def test_doctor_probe_exception_becomes_fail(_isolate, capsys):
    _install_app(
        _isolate,
        "err-app",
        module_file="cli_doctor.py",
        module_body="def probe():\n    raise ValueError('nope')\n",
        cli={"doctor": "cli_doctor:probe"},
    )
    issues = app_cli.run_app_doctor_probes()
    assert "nope" in capsys.readouterr().out
    assert any("err-app" in i for i in issues)


def test_malformed_cli_ref_is_a_warning_not_a_crash(_isolate, capsys):
    _install_app(
        _isolate,
        "bad-ref",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('never')\n",
        cli={"setup": "not_a_valid_ref"},
    )
    failures = app_cli.run_app_setup_steps()
    assert "bad-ref" in capsys.readouterr().out
    assert any("bad-ref: ValueError:" in failure for failure in failures)


def _add_support_package(app_dir, package="support"):
    support = app_dir / package
    support.mkdir()
    (support / "__init__.py").write_text("from .value import VALUE\n", encoding="utf-8")
    (support / "value.py").write_text("VALUE = 'from-app-package'\n", encoding="utf-8")


def test_setup_and_doctor_callbacks_resolve_app_package(_isolate, capsys):
    setup_dir = _install_app(
        _isolate,
        "package-setup",
        module_file="cli_setup.py",
        module_body=(
            "from setup_import_support import VALUE as IMPORTED\n"
            "def run(ctx):\n"
            "    from setup_callback_support import VALUE\n"
            "    ctx.print(f'{IMPORTED}:{VALUE}')\n"
        ),
        cli={"setup": "cli_setup:run"},
    )
    _add_support_package(setup_dir, "setup_import_support")
    _add_support_package(setup_dir, "setup_callback_support")
    doctor_dir = _install_app(
        _isolate,
        "package-doctor",
        module_file="cli_doctor.py",
        module_body=(
            "from gideon.sdk.cli import DoctorLine\n"
            "def probe():\n"
            "    from doctor_callback_support import VALUE\n"
            "    return [DoctorLine('app package', 'ok', VALUE)]\n"
        ),
        cli={"doctor": "cli_doctor:probe"},
    )
    _add_support_package(doctor_dir, "doctor_callback_support")

    assert app_cli.run_app_setup_steps() == []
    issues = app_cli.run_app_doctor_probes()
    output = capsys.readouterr().out
    assert "from-app-package:from-app-package" in output
    assert "app package  from-app-package" in output
    assert issues == []
    assert str(setup_dir.resolve()) not in sys.path
    assert str(doctor_dir.resolve()) not in sys.path


def test_same_app_module_name_reloads_for_a_different_home(tmp_path):
    from gideon.extensions.apps.native_contract import load_bundle_module

    first = tmp_path / "first" / "apps" / "shared"
    second = tmp_path / "second" / "apps" / "shared"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "cli_setup.py").write_text("VALUE = 'first-home'\n", encoding="utf-8")
    (second / "cli_setup.py").write_text("VALUE = 'second-home'\n", encoding="utf-8")

    first_module = load_bundle_module(first, "shared", "cli_setup")
    second_module = load_bundle_module(second, "shared", "cli_setup")
    assert first_module.VALUE == "first-home"
    assert second_module.VALUE == "second-home"
    assert first_module is not second_module


def test_targeted_setup_failure_exits_nonzero_without_done(_isolate, capsys):
    from gideon.interfaces.cli import setup

    _install_app(
        _isolate,
        "targeted-failure",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    raise RuntimeError('targeted boom')\n",
        cli={"setup": "cli_setup:run"},
    )
    with pytest.raises(SystemExit) as raised:
        setup._setup(only_app="targeted-failure")
    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "targeted-failure: RuntimeError: targeted boom" in output
    assert "Done!" not in output


def test_full_setup_failure_exits_nonzero_without_done(_isolate, monkeypatch, capsys):
    from gideon.interfaces.cli import setup

    _install_app(
        _isolate,
        "full-failure",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    raise RuntimeError('full boom')\n",
        cli={"setup": "cli_setup:run"},
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n" * 20))
    with pytest.raises(SystemExit) as raised:
        setup._setup()
    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "full-failure: RuntimeError: full boom" in output
    assert "Done!" not in output
