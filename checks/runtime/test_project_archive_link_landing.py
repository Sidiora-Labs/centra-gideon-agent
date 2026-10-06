import json
import os
from argparse import Namespace

import pytest

from gideon.automation.workflows import project_archive as archive
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.interfaces.cli.project import _import


def actual_archive(tmp_path):
    source = tmp_path / 'source'
    (source / 'context').mkdir(parents=True)
    (source / 'project.json').write_text(json.dumps({'id': 'p-foreign', 'name': 'Foreign', 'brief': 'portable brief', 'agent_instructions_template': 'portable template', 'workspace_dir': '/foreign', 'is_builtin': True}))
    (source / 'context/blocked.md').write_text('incoming blocked')
    (source / 'context/safe.md').write_text('incoming safe')
    data, _ = archive.export_project_archive('p-foreign', project_root=source, project_name='Imported Project')
    path = tmp_path / 'project.zip'
    path.write_bytes(data)
    return path


def test_cli_import_keeps_native_identity_and_portable_fields(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('GIDEON_HOME', str(home))
    path = actual_archive(tmp_path)
    assert _import(Namespace(archive=str(path), passphrase='', dry_run=False)) == 0
    store = HierarchyStore()
    project = next(item for item in store.list_projects() if item.name == 'Imported Project')
    assert project.id != 'p-foreign'
    assert project.brief == 'portable brief'
    assert project.agent_instructions_template == 'portable template'
    assert project.workspace_dir == ''
    assert project.is_builtin is False
    record = json.loads((home / 'projects' / project.id / 'project.json').read_text())
    assert record['id'] == project.id
    assert (home / 'projects' / project.id / 'context/safe.md').read_text() == 'incoming safe'


def test_cli_refuses_linked_projects_before_native_store_writes(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (home / 'projects').symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv('GIDEON_HOME', str(home))
    path = actual_archive(tmp_path)
    assert _import(Namespace(archive=str(path), passphrase='', dry_run=False)) == 1
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('kind', ['hard', 'symbolic'])
def test_real_archive_commit_reports_link_and_preserves_safe_file(tmp_path, monkeypatch, kind):
    home = tmp_path / 'home'
    monkeypatch.setenv('GIDEON_HOME', str(home))
    path = actual_archive(tmp_path)
    plan, extracted = archive.read_archive_plan(path, existing_names=[])
    store = HierarchyStore()
    project = store.create_project('Destination')
    root = home / 'projects' / project.id
    (root / 'context').mkdir(exist_ok=True)
    outside = tmp_path / 'outside'
    outside.write_text('untouched')
    blocked = root / 'context/blocked.md'
    if kind == 'hard':
        os.link(outside, blocked)
    else:
        blocked.symlink_to(outside)
    left = []
    written = archive.commit_import(plan, extracted, project_root=root, left=left)
    assert 'context/blocked.md' not in written
    assert 'context/safe.md' in written
    assert any('context/blocked.md' in item for item in left)
    assert outside.read_text() == 'untouched'
    assert store.get_project(project.id).name == 'Destination'
    assert (root / 'context/safe.md').read_text() == 'incoming safe'
