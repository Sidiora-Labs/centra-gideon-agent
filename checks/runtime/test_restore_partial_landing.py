"""Restore and import retain refused parts while independent files still arrive."""
import json
import os
import zipfile
from gideon.workspace.snapshot import _do_merge, _do_replace
from gideon.workspace.portability import apply_import_zip


def seed(tmp_path):
    home, incoming, outside = tmp_path/'home',tmp_path/'incoming',tmp_path/'outside'
    home.mkdir(); incoming.mkdir(); outside.mkdir()
    (incoming/'tasks').mkdir(); (home/'tasks').mkdir()
    (incoming/'tasks'/'blocked.json').write_text(json.dumps({'id':'blocked','title':'incoming'}))
    (incoming/'tasks'/'safe.json').write_text(json.dumps({'id':'safe','title':'safe'}))
    (outside/'blocked.json').write_text('outside original')
    (home/'tasks'/'blocked.json').symlink_to(outside/'blocked.json')
    return home,incoming,outside


def test_merge_tree_refuses_one_file_and_keeps_independent_file(tmp_path):
    home,incoming,outside=seed(tmp_path)
    left=_do_merge(incoming,home,None)
    assert left and 'blocked.json' in left[0]
    assert (home/'tasks'/'safe.json').exists()
    assert (outside/'blocked.json').read_text() == 'outside original'
    assert (home/'tasks'/'blocked.json').is_symlink()


def test_replace_core_refusal_does_not_block_independent_component(tmp_path):
    home,incoming,outside=seed(tmp_path)
    (incoming/'config.json').write_text('{}')
    (home/'config.json').symlink_to(outside/'config.json')
    (incoming/'workspace').mkdir();(incoming/'workspace'/'safe.txt').write_text('safe')
    left=_do_replace(incoming,home,['config','workspace'])
    assert left and 'config.json' in left[0]
    assert (home/'workspace'/'safe.txt').read_text() == 'safe'
    assert not (outside/'config.json').exists()


def test_export_zip_import_reports_partial_with_independent_data(tmp_path,monkeypatch):
    home,incoming,outside=seed(tmp_path)
    monkeypatch.setenv('GIDEON_HOME',str(home))
    archive=tmp_path/'data.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        for path in incoming.rglob('*'):
            if path.is_file():bundle.write(path,'snapshot/'+path.relative_to(incoming).as_posix())
    result=apply_import_zip(archive)
    assert result['partial'] is True and result['left_unchanged']
    assert (home/'tasks'/'safe.json').exists()
    assert (outside/'blocked.json').read_text() == 'outside original'


def test_restore_api_result_reports_partial_actual_archive(tmp_path, monkeypatch):
    import tarfile
    from gideon.workspace.snapshot import restore_apply
    home,incoming,outside=seed(tmp_path)
    monkeypatch.setenv('GIDEON_HOME',str(home))
    archive=tmp_path/'snapshot.tar.gz'
    with tarfile.open(archive,'w:gz') as bundle:
        bundle.add(incoming,arcname='gideon-snapshot-test')
    result=restore_apply(archive,'merge',None)
    assert result['ok'] is True and result['partial'] is True
    assert result['left_unchanged'] and 'blocked.json' in result['left_unchanged'][0]
    assert (home/'tasks'/'safe.json').exists()
    assert (outside/'blocked.json').read_text() == 'outside original'
