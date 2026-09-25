"""Exercise reporting and specification synchronization against real task files."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'tooling/capability_progress.py'
spec = importlib.util.spec_from_file_location('capability_progress', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_project(root, statuses):
    directory = root / 'spec/capability-expansion'
    (directory / 'tasks').mkdir(parents=True)
    tasks = []
    for index, status in enumerate(statuses):
        identifier = f'knowledge.{index:02d}'
        tasks.append({'id': identifier, 'title': f'Journal {index} — حفظ',
                      'owner_lane': 'knowledge', 'wave': index + 1, 'status': 'pending',
                      'requires': [f'knowledge.{index-1:02d}'] if index else [],
                      'verify_cmd': f'python3 tooling/capability_workflow.py verify {identifier}'})
        if status is not None:
            (directory / 'tasks' / f'{identifier}.json').write_text(json.dumps({'id': identifier, 'status': status}))
    (directory / 'backlog.json').write_text(json.dumps({'version': 1, 'product': 'oss', 'status': 'in_progress', 'tasks': tasks}))
    return directory


def test_exact_qualified_states_exclude_partial_external_and_missing_records(tmp_path):
    statuses = ['locally_qualified', 'implemented_locally_qualified', 'locally_qualified_scoped_paths',
                'implemented', 'implemented_inference_unqualified', 'locally_qualified_external_pending',
                'locally_checked_browser_pending', 'runtime_locally_qualified', 'blocked', 'dependency_blocked', None]
    directory = write_project(tmp_path, statuses)
    original = (directory / 'backlog.json').read_bytes()
    backlog, report = module.progress(tmp_path)
    assert report['counts'] == {'done': 3, 'in_progress': 5, 'blocked': 2, 'pending': 1}
    assert report['total'] == len(statuses)
    assert sum(report['counts'].values()) == report['total']
    assert report['tasks'][0]['evidence_record'].endswith('knowledge.00.json')
    assert report['tasks'][-1]['evidence_record'] is None
    assert report['tasks'][-1]['implementation_status'] == 'not_started'
    assert backlog['tasks'][5]['status'] == 'in_progress'
    assert 'Publication and deployment are separate' in report['definition']
    assert (directory / 'backlog.json').read_bytes() == original


def test_sync_preserves_dependency_contract_and_is_repeatable(tmp_path):
    directory = write_project(tmp_path, ['locally_qualified', 'blocked', None])
    path = directory / 'backlog.json'
    path.chmod(0o640)
    backlog, report = module.progress(tmp_path)
    module.synchronize(tmp_path, backlog)
    stored = json.loads(path.read_text())
    assert stored['tasks'][1]['requires'] == ['knowledge.00']
    assert stored['tasks'][1]['verify_cmd'].endswith('knowledge.01')
    assert stored['tasks'][0]['title'] == 'Journal 0 — حفظ'
    assert path.stat().st_mode & 0o777 == 0o640
    kvx = (directory / 'spec.kvx').read_text()
    assert kvx.count('[task.') == 3
    assert 'status = "done"' in kvx
    assert 'status = "blocked"' in kvx
    assert 'implementation_status = "not_started"' in kvx
    assert 'requires = ["knowledge.00"]' in kvx
    before = path.read_bytes(), (directory / 'spec.kvx').read_bytes()
    next_backlog, next_report = module.progress(tmp_path)
    assert next_report == report
    module.synchronize(tmp_path, next_backlog)
    assert before == (path.read_bytes(), (directory / 'spec.kvx').read_bytes())
    assert not list(directory.glob('.backlog.json.*'))
    assert not list(directory.glob('.spec.kvx.*'))


def test_bad_record_never_changes_backlog_or_spec(tmp_path):
    directory = write_project(tmp_path, ['locally_qualified'])
    (directory / 'spec.kvx').write_text('original specification')
    record = directory / 'tasks/knowledge.00.json'
    record.write_text('{unfinished')
    original = (directory / 'backlog.json').read_bytes()
    with pytest.raises(json.JSONDecodeError):
        module.progress(tmp_path)
    assert (directory / 'backlog.json').read_bytes() == original
    assert (directory / 'spec.kvx').read_text() == 'original specification'
    record.write_text(json.dumps({'id': 'wrong.01', 'status': 'locally_qualified'}))
    with pytest.raises(ValueError, match='identifier mismatch'):
        module.progress(tmp_path)


def test_duplicate_and_escaping_identifiers_are_rejected(tmp_path):
    directory = write_project(tmp_path, [None])
    path = directory / 'backlog.json'
    document = json.loads(path.read_text())
    document['tasks'].append(document['tasks'][0].copy())
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='Duplicate'):
        module.progress(tmp_path)
    document['tasks'].pop()
    for identifier in ['../outside', '..', 'folder\\record']:
        document['tasks'][0]['id'] = identifier
        path.write_text(json.dumps(document))
        with pytest.raises(ValueError, match='Invalid'):
            module.progress(tmp_path)


def test_real_cli_is_read_only_unless_write_requested(tmp_path):
    directory = write_project(tmp_path, ['locally_qualified', 'in_progress'])
    before = (directory / 'backlog.json').read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(tmp_path)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['counts']['done'] == 1
    assert (directory / 'backlog.json').read_bytes() == before
    assert not (directory / 'spec.kvx').exists()
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(tmp_path), '--write'], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['counts']['in_progress'] == 1
    assert (directory / 'spec.kvx').is_file()
    assert json.loads((directory / 'backlog.json').read_text())['tasks'][0]['status'] == 'done'


def test_original_workflow_task_identity_is_supported(tmp_path):
    directory = write_project(tmp_path, ['locally_qualified'])
    path = directory / 'tasks/knowledge.00.json'
    path.write_text(json.dumps({'task': 'knowledge.00', 'status': 'locally_qualified'}))
    _, report = module.progress(tmp_path)
    assert report['counts']['done'] == 1
    path.write_text(json.dumps({'task': 'foreign.00', 'status': 'locally_qualified'}))
    with pytest.raises(ValueError, match='identifier mismatch'):
        module.progress(tmp_path)
