"""Installer ownership and checkout commands use real distribution records."""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def probe(tmp_path, owner, code, *, path=None):
    info = tmp_path / 'gideon_agent_harness-0.1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: gideon-agent-harness\nVersion: 0.1.0\nProvides-Extra: local\nRequires-Dist: packaging; extra == "local"\n')
    (info / 'INSTALLER').write_text(owner)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT / 'runtime')]))
    if path is not None:
        env['PATH'] = path
    return subprocess.run([sys.executable, '-c', 'import json; from gideon.operations import _installer as i; '+code], env=env, capture_output=True, text=True, check=True).stdout


def test_pip_record_does_not_select_uv_on_path(tmp_path):
    result=json.loads(probe(tmp_path,'pip','print(json.dumps(i.install_argv(["-U", "example"])))'))
    assert result == [sys.executable, '-m', 'pip', 'install', '-U', 'example']


def test_missing_recorded_uv_refuses_without_fallback(tmp_path):
    output=probe(tmp_path,'uv','\ntry: i.require_own_installer()\nexcept i.NoInstallerError as exc: print(str(exc))\nelse: raise AssertionError("accepted missing uv")',path='')
    assert 'Nothing was changed' in output and 'terminal' in output


def test_uv_checkout_keeps_lock_extras_and_active_prefix(tmp_path):
    uv=Path('/root/.local/bin/uv')
    if not uv.exists():
        pytest.skip('uv executable unavailable for read-only ownership probe')
    subprocess.run([str(uv), '--version'], check=True, capture_output=True)
    (tmp_path/'pyproject.toml').write_text('[project.optional-dependencies]\nlocal = ["packaging"]\n')
    output=probe(tmp_path,'uv',f'print(json.dumps([i.checkout_install_argv({str(tmp_path)!r}), i.installer_env()["UV_PROJECT_ENVIRONMENT"]]))',path=str(uv.parent))
    argv,prefix=json.loads(output)
    assert argv == ['uv','sync','--locked','--inexact','--python',sys.executable,'--quiet','--extra','local']
    assert prefix == sys.prefix


def test_cli_preflight_precedes_checkout_and_journal_mutation():
    tree=ast.parse((ROOT/'runtime/gideon/interfaces/cli/server.py').read_text())
    for name in ('_update_git','_update_pip'):
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
        calls=[n for n in ast.walk(function) if isinstance(n,ast.Call)]
        preflight=next(n.lineno for n in calls if isinstance(n.func,ast.Name) and n.func.id=='_require_update_installer')
        mutations=[n.lineno for n in calls if isinstance(n.func,ast.Attribute) and n.func.attr in ('begin_update','git_reset_hard')]
        assert mutations and all(preflight < line for line in mutations)
