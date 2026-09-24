"""Exercise the task runner with real files, syntax checks and subprocesses."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

SOURCE=Path(__file__).resolve().parents[2]/'tooling/capability_workflow.py'
spec=importlib.util.spec_from_file_location('capability_workflow',SOURCE)
workflow=importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


def project(tmp_path):
    root=tmp_path/'project'
    folder=root/'spec/capability-expansion'
    folder.mkdir(parents=True)
    tasks=[{'id':'sample.01','owner_lane':'sample','status':'pending','requires':[]},
           {'id':'sample.02','owner_lane':'sample','status':'pending','requires':['sample.01']}]
    (folder/'backlog.json').write_text(json.dumps({'tasks':tasks,'product':'oss'}))
    return root


def source(root,relative,text):
    path=root/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text)
    return path


def detail(root,value):
    return source(root,'spec/capability-expansion/tasks/sample.01.json',json.dumps(value))


def test_load_task_has_exact_identity_and_empty_optional_detail(tmp_path):
    root=project(tmp_path)
    task,extra=workflow.load_task(root,'sample.01')
    assert task['owner_lane']=='sample'
    assert task['requires']==[]
    assert extra=={}
    detail(root,{'extra_files':['runtime/additional.py']})
    _,extra=workflow.load_task(root,'sample.01')
    assert extra['extra_files']==['runtime/additional.py']


@pytest.mark.parametrize('identifier',['../sample.01','sample','sample.1','sample.001','Sample.01','sample.01/','sample.01\n'])
def test_invalid_task_identifiers_are_not_paths(tmp_path,identifier):
    root=project(tmp_path)
    with pytest.raises(ValueError,match='invalid task'):
        workflow.load_task(root,identifier)
    assert not (tmp_path/'sample.01').exists()


def test_unknown_and_duplicate_tasks_fail(tmp_path):
    root=project(tmp_path)
    with pytest.raises(ValueError,match='exactly once'):
        workflow.load_task(root,'sample.03')
    path=root/'spec/capability-expansion/backlog.json'
    doc=json.loads(path.read_text())
    doc['tasks'].append(doc['tasks'][0])
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='exactly once'):
        workflow.load_task(root,'sample.01')


def test_owned_files_include_production_and_tests_once(tmp_path):
    root=project(tmp_path)
    prod=source(root,'runtime/gideon/workspace/capabilities/sample/store.py','value=1\n')
    test=source(root,'checks/runtime/capabilities/sample/test_store.py','def test_value():\n    assert 1 == 1\n')
    ui=source(root,'apps/console/src/features/capabilities/sample/Page.tsx','export const value = 1\n')
    uitest=source(root,'apps/console/src/features/capabilities/sample/Page.test.tsx','expect(1).toBe(1)\n')
    handler=source(root,'runtime/gideon/interfaces/dashboard/handlers/capabilities_sample.py','value=2\n')
    source(root,'runtime/gideon/workspace/capabilities/other/store.py','ignored=1\n')
    source(root,'runtime/gideon/workspace/capabilities/sample/data.json','{}')
    detail(root,{'production_files':[str(prod.relative_to(root))]})
    task,extra=workflow.load_task(root,'sample.01')
    production,tests=workflow.owned_files(root,task,extra)
    assert production==sorted([prod,ui,handler])
    assert tests==sorted([test,uitest])
    assert len(production)==3


@pytest.mark.parametrize('path',['/etc/passwd','../outside.py','runtime/../../escape.py'])
def test_declared_paths_cannot_escape_repository(tmp_path,path):
    root=project(tmp_path)
    task,_=workflow.load_task(root,'sample.01')
    with pytest.raises(ValueError,match='repository-relative'):
        workflow.owned_files(root,task,{'extra_files':[path]})


def test_symlinked_code_is_rejected(tmp_path):
    root=project(tmp_path)
    outside=tmp_path/'outside.py'
    outside.write_text('value=1\n')
    link=root/'runtime/gideon/workspace/capabilities/sample/store.py'
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    task,_=workflow.load_task(root,'sample.01')
    with pytest.raises(ValueError,match='symlinked'):
        workflow.owned_files(root,task,{})
    assert outside.read_text()=='value=1\n'


def test_python_line_count_excludes_comments_and_docstrings(tmp_path):
    path=tmp_path/'source.py'
    path.write_text('"""Module docs.\nSecond line.\n"""\n# comment\n\nvalue=1\ndef get():\n    """Function docs."""\n    return value\n')
    assert workflow.code_lines(path)==3
    path.write_text('class Item:\n    """Class docs."""\n    def value(self):\n        """Method docs."""\n        return 2\n')
    assert workflow.code_lines(path)==3


def test_javascript_line_count_excludes_comments(tmp_path):
    path=tmp_path/'source.ts'
    path.write_text('// comment\n\n/* multiple\ncomment */\nconst first = 1\n/* before */const second=2\nconst third=3 /* trailing\ncomment */\n')
    assert workflow.code_lines(path)==3


def test_syntax_errors_are_not_silently_counted(tmp_path):
    path=tmp_path/'bad.py'
    path.write_text('def broken(:\n')
    with pytest.raises(SyntaxError):
        workflow.code_lines(path)


def test_ratio_requires_nonempty_production_and_sufficient_tests(tmp_path):
    root=project(tmp_path)
    result=workflow.inspect_task(root,'sample.01')
    assert not result['ratio_pass']
    assert result['ratio'] is None
    source(root,'runtime/gideon/workspace/capabilities/sample/store.py','value=1\nother=2\n')
    source(root,'checks/runtime/capabilities/sample/test_store.py','assert True\n')
    result=workflow.inspect_task(root,'sample.01')
    assert result['production_lines']==2
    assert result['test_lines']==1
    assert result['ratio']==0.5
    assert not result['ratio_pass']
    source(root,'checks/runtime/capabilities/sample/test_store.py','first=1\nassert first==1\n')
    assert workflow.inspect_task(root,'sample.01')['ratio_pass']


def test_extra_native_provider_files_participate_in_ratio(tmp_path):
    root=project(tmp_path)
    provider=source(root,'runtime/gideon/extensions/apps/native/sample/provider.py','value=1\n')
    tests=source(root,'checks/runtime/test_extra.py','assert 1==1\n')
    detail(root,{'production_files':[str(provider.relative_to(root))],'test_files':[str(tests.relative_to(root))]})
    result=workflow.inspect_task(root,'sample.01')
    assert result['production_lines']==1
    assert result['test_lines']==1
    assert result['ratio_pass']


def test_real_command_records_output_and_exit_code(tmp_path):
    log=tmp_path/'command.log'
    code=workflow.run_command([sys.executable,'-c','import sys; print("actual output"); sys.exit(7)'],tmp_path,os.environ.copy(),log)
    assert code==7
    assert log.read_text().strip()=='actual output'
    assert len(list(tmp_path.iterdir()))==1


def test_verify_refuses_insufficient_ratio_with_durable_evidence(tmp_path):
    root=project(tmp_path)
    source(root,'runtime/gideon/workspace/capabilities/sample/store.py','value=1\n')
    code,evidence=workflow.verify(root,'sample.01')
    assert code==1
    assert evidence['status']=='failed'
    assert evidence['commands']==[]
    saved=json.loads((Path(evidence['evidence_dir'])/'evidence.json').read_text())
    assert saved['error'].startswith('test executable lines')
    assert saved['production_lines']==1


def test_verify_runs_real_syntax_and_pytest_and_hashes_files(tmp_path):
    root=project(tmp_path)
    source(root,'runtime/gideon/workspace/capabilities/sample/store.py','value=6\n')
    source(root,'checks/runtime/capabilities/sample/test_store.py',
           'import os\nfrom pathlib import Path\ndef test_real_store():\n    root=Path(__file__).parents[4]\n    value={}\n    exec((root/"runtime/gideon/workspace/capabilities/sample/store.py").read_text(),value)\n    assert value["value"]==6\n    assert Path(os.environ["GIDEON_HOME"]).name=="home"\n')
    code,evidence=workflow.verify(root,'sample.01')
    assert code==0,evidence
    assert evidence['status']=='locally_qualified'
    assert [row['name'] for row in evidence['commands']]==['compile','runtime']
    assert all(row['exit_code']==0 for row in evidence['commands'])
    assert len(evidence['files'])==2
    assert all(len(value)==64 for value in evidence['files'].values())
    assert evidence['completed_at']>=evidence['started_at']


def test_verify_stops_after_real_failing_test(tmp_path):
    root=project(tmp_path)
    source(root,'runtime/gideon/workspace/capabilities/sample/store.py','value=6\n')
    source(root,'checks/runtime/capabilities/sample/test_store.py','def test_expected_failure():\n    value=6\n    assert value==7\n')
    code,evidence=workflow.verify(root,'sample.01')
    assert code==1
    assert evidence['status']=='failed'
    assert evidence['error']=='runtime failed'
    assert evidence['commands'][-1]['exit_code']==1
    assert 'FAILED' in Path(evidence['commands'][-1]['log']).read_text()


def test_ui_implementation_requires_ui_behavior_tests(tmp_path):
    root=project(tmp_path)
    source(root,'apps/console/src/features/capabilities/sample/Page.tsx','export default ()=>null\n')
    source(root,'checks/runtime/capabilities/sample/test_store.py','def test_value():\n    assert 1==1\n')
    code,evidence=workflow.verify(root,'sample.01')
    assert code==1
    assert evidence['error']=='UI behavior tests are required for UI implementation'
    assert evidence['commands']==[]


def test_cli_ready_obeys_dependencies(tmp_path):
    root=project(tmp_path)
    result=subprocess.run([sys.executable,str(SOURCE),'ready','--root',str(root)],capture_output=True,text=True)
    assert result.returncode==0
    assert json.loads(result.stdout)==['sample.01']
    path=root/'spec/capability-expansion/backlog.json'
    doc=json.loads(path.read_text());doc['tasks'][0]['status']='done';path.write_text(json.dumps(doc))
    result=subprocess.run([sys.executable,str(SOURCE),'ready','--root',str(root)],capture_output=True,text=True)
    assert result.returncode==0
    assert json.loads(result.stdout)==['sample.02']


def test_cli_missing_identifier_and_malformed_backlog_fail(tmp_path):
    root=project(tmp_path)
    result=subprocess.run([sys.executable,str(SOURCE),'verify','--root',str(root)],capture_output=True,text=True)
    assert result.returncode==2
    assert 'task identifier is required' in json.loads(result.stderr)['error']
    (root/'spec/capability-expansion/backlog.json').write_text('{')
    result=subprocess.run([sys.executable,str(SOURCE),'ready','--root',str(root)],capture_output=True,text=True)
    assert result.returncode==2
    assert 'error' in json.loads(result.stderr)


def test_explicit_task_lists_exclude_prior_lane_files(tmp_path):
    root=project(tmp_path)
    old=source(root,'runtime/gideon/workspace/capabilities/sample/old.py','old=1\n')
    current=source(root,'runtime/gideon/workspace/capabilities/sample/new.py','new=2\n')
    test=source(root,'checks/runtime/capabilities/sample/test_new.py','assert 2==2\n')
    detail(root,{'production_files':[str(current.relative_to(root))],'test_files':[str(test.relative_to(root))]})
    result=workflow.inspect_task(root,'sample.01')
    assert result['production_files']==[str(current.relative_to(root))]
    assert str(old.relative_to(root)) not in result['production_files']
    assert result['production_lines']==1
    assert result['test_lines']==1


def test_baseline_counts_only_changed_executable_lines(tmp_path):
    root=project(tmp_path)
    relative='runtime/shared.py'
    current=source(root,relative,'old=1\n# revised\nnew=2\n')
    baseline=tmp_path/'before'
    source(baseline,relative,'old=1\n# previous\n')
    assert workflow.changed_lines(root,current,{'baseline_dir':str(baseline)})==1
    current.write_text('old=1\n# revised\n')
    assert workflow.changed_lines(root,current,{'baseline_dir':str(baseline)})==0
    new=source(root,'runtime/new.py','first=1\nsecond=2\n')
    assert workflow.changed_lines(root,new,{'baseline_dir':str(baseline)})==2
    with pytest.raises(ValueError,match='does not exist'):
        workflow.changed_lines(root,current,{'baseline_dir':str(tmp_path/'absent')})


def test_git_baseline_counts_real_committed_preimage(tmp_path):
    root=project(tmp_path)
    path=source(root,'runtime/shared.py','old=1\n')
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    subprocess.run(['git','add','runtime/shared.py'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','baseline'],cwd=root,check=True)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    path.write_text('old=1\nnew=2\n')
    assert workflow.changed_lines(root,path,{'base_revision':revision})==1
    new=source(root,'runtime/new.py','first=1\nsecond=2\n')
    assert workflow.changed_lines(root,new,{'base_revision':revision})==2
    with pytest.raises(ValueError,match='commit hash'):
        workflow.changed_lines(root,path,{'base_revision':'--output=/tmp/no'})
    with pytest.raises(ValueError,match='resolve to a commit'):
        workflow.changed_lines(root,path,{'base_revision':'0'*40})


def test_focused_gate_does_not_run_unchanged_prior_tests(tmp_path):
    root=project(tmp_path)
    prod=source(root,'runtime/gideon/workspace/capabilities/sample/store.py','value=6\n')
    source(root,'checks/runtime/capabilities/sample/test_old.py','raise RuntimeError("must not collect")\n')
    test=source(root,'checks/runtime/capabilities/sample/test_new.py','import os\nimport sys\ndef test_interpreter():\n    assert os.environ["GIDEON_TEST_PYTHON"]==sys.executable\n')
    detail(root,{'production_files':[str(prod.relative_to(root))],'test_files':[str(test.relative_to(root))],
                 'runtime_test_files':[str(test.relative_to(root))]})
    code,evidence=workflow.verify(root,'sample.01')
    assert code==0,evidence
    assert all('test_old.py' not in value for row in evidence['commands'] for value in row['argv'])
    assert evidence['status']=='locally_qualified'


def test_undeclared_test_selection_is_rejected(tmp_path):
    root=project(tmp_path)
    prod=source(root,'runtime/gideon/workspace/capabilities/sample/store.py','value=6\n')
    test=source(root,'checks/runtime/capabilities/sample/test_new.py','def test_value():\n    assert 6==6\n')
    detail(root,{'production_files':[str(prod.relative_to(root))],'test_files':[str(test.relative_to(root))],
                 'runtime_test_files':['checks/other.py']})
    with pytest.raises(ValueError,match='must be declared'):
        workflow.verify(root,'sample.01')
