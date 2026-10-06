"""Home isolation and updater refusal are observable without performing an upgrade."""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_probe(tmp_path, code, *, missing_uv=False):
    env=dict(os.environ, HOME=str(tmp_path), GIDEON_HOME=str(tmp_path/'gideon'), PYTHONPATH=os.pathsep.join([str(tmp_path),str(ROOT/'runtime')]))
    if missing_uv:
        info=tmp_path/'gideon_agent_harness-0.1.0.dist-info'
        info.mkdir()
        (info/'METADATA').write_text('Metadata-Version: 2.1\nName: gideon-agent-harness\nVersion: 0.1.0\n')
        (info/'INSTALLER').write_text('uv')
        env['PATH']=''
    return subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True).stdout


def test_real_child_installer_environment_creates_only_home_scratch(tmp_path):
    code='''import json, subprocess, sys
from gideon.security.sandbox import build_child_env
env=build_child_env(site="installer-boundary",installer="pip",source={"PATH":"/usr/bin","SECRET_TOKEN":"hidden","TMPDIR":"/outside"})
child=subprocess.run([sys.executable,"-c","import os,json; print(json.dumps(dict(os.environ)))"],env=env,capture_output=True,text=True,check=True)
print(child.stdout)
'''
    env=json.loads(run_probe(tmp_path,code))
    scratch=tmp_path/'gideon'/'installer-cache'/'tmp'
    assert scratch.is_dir() and scratch.stat().st_mode & 0o777 == 0o700
    assert env['TMPDIR']==str(scratch)
    assert env['npm_config_cache']==str(scratch.parent/'npm')
    assert env['PIP_NO_CACHE_DIR']==env['UV_NO_CACHE']==env['NODE_DISABLE_COMPILE_CACHE']=='1'
    assert 'SECRET_TOKEN' not in env


def test_actual_update_process_inherits_home_cache_settings(tmp_path):
    output=run_probe(tmp_path,'''import asyncio,json,sys
from gideon.operations.self_update import launch_update_process
async def probe():
    p=await launch_update_process(sys.executable,"-c","import os,json; print(json.dumps({k:os.environ[k] for k in ('TMPDIR','UV_PROJECT_ENVIRONMENT','PIP_NO_CACHE_DIR')}))",stdout=asyncio.subprocess.PIPE)
    out,_=await p.communicate()
    assert p.returncode==0
    print(out.decode())
asyncio.run(probe())
''')
    env=json.loads(output)
    assert env['TMPDIR']==str(tmp_path/'gideon'/'installer-cache'/'tmp')
    assert env['UV_PROJECT_ENVIRONMENT']==sys.prefix


def test_dashboard_missing_recorded_tool_refuses_before_any_update(tmp_path):
    output=run_probe(tmp_path,'''import json
from gideon.interfaces.dashboard.handlers.updates import _installer_preflight
response=_installer_preflight()
assert response.status==409
print(response.text)
''',missing_uv=True)
    assert 'Nothing was changed' in json.loads(output)['error']
    assert not (tmp_path/'gideon').exists()


def test_dashboard_apply_and_rollback_preflight_before_journal():
    tree=ast.parse((ROOT/'runtime/gideon/interfaces/dashboard/handlers/updates.py').read_text())
    for function in tree.body:
        if not isinstance(function,ast.AsyncFunctionDef):
            continue
        calls=[n for n in ast.walk(function) if isinstance(n,ast.Call)]
        transitions=[n.lineno for n in calls if isinstance(n.func,ast.Attribute) and n.func.attr in ('begin_update','begin_rollback')]
        if transitions:
            preflight=next(n.lineno for n in calls if isinstance(n.func,ast.Name) and n.func.id=='_installer_preflight')
            assert all(preflight < line for line in transitions)
