"""Original file names, bytes and deletion decisions survive real filesystem sync."""
import json
from pathlib import Path
import pytest
from test_durability_convergence_e2e import FolderTransport
from test_sync_latest_copy_filesystem import cycle
from gideon.operations.durability import inventory as inv
from gideon.operations.durability.shards import export_shards, import_shards, _json_rows_from_entity_dir
from gideon.operations.durability.writeback import apply_rows
from gideon.operations.durability.conflicts import ConflictQueue
from gideon.operations.durability.conflict_resolve import resolve_conflict


def test_original_text_and_binary_files_roundtrip_then_delete(tmp_path):
    a,b=tmp_path/'a',tmp_path/'b';store=a/'tasks';store.mkdir(parents=True)
    contents={'notes/readme.md':b'# Notes\n','settings.yaml':b'enabled: true\n','extensionless':b'raw\n','binary.bin':b'\x00\xff\x80'}
    for name,content in contents.items():
        path=store/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    transport=FolderTransport(tmp_path/'store')
    assert cycle(transport,a,'a').ok
    assert cycle(transport,b,'b').ok
    assert cycle(transport,a,'a').ok
    for name,content in contents.items():
        assert (b/'tasks'/name).read_bytes() == content
        assert not (b/'tasks'/(name+'.json')).exists()
    for name in contents:(a/'tasks'/name).unlink()
    assert cycle(transport,a,'a').ok
    assert cycle(transport,b,'b').ok
    for name in contents:assert not (b/'tasks'/name).exists()


def test_delete_review_removes_original_markdown_file(tmp_path):
    a,b=tmp_path/'a',tmp_path/'b';(a/'tasks').mkdir(parents=True)
    (a/'tasks'/'note.md').write_text('old')
    transport=FolderTransport(tmp_path/'store')
    assert cycle(transport,a,'a').ok;assert cycle(transport,b,'b').ok;assert cycle(transport,a,'a').ok
    (a/'tasks'/'note.md').unlink();(b/'tasks'/'note.md').write_text('unseen edit')
    assert cycle(transport,a,'a').ok;assert cycle(transport,b,'b').ok
    assert (b/'tasks'/'note.md').read_text() == 'unseen edit'
    record=ConflictQueue(b).items()[0]
    assert record.deleted == 'there' and record.entity_id == 'note.md'
    result=resolve_conflict(b,record.id,'take_remote')
    assert result.ok and result.removed == 1
    assert not (b/'tasks'/'note.md').exists() and not (b/'tasks'/'note.md.json').exists()


def test_row_collision_and_malformed_json_block_publish(tmp_path):
    home=tmp_path/'home';(home/'tasks').mkdir(parents=True)
    (home/'tasks'/'note').write_text('plain')
    (home/'tasks'/'note.json').write_text('{}')
    result=export_shards(home,tmp_path/'export')
    assert result.skipped and any('row id already' in why for why in result.skipped.values())


def test_database_and_runtime_lock_never_become_file_rows(tmp_path):
    home=tmp_path/'home';(home/'tasks').mkdir(parents=True)
    (home/'tasks'/'arbitrary').write_bytes(b'SQLite format 3\x00' + b'0'*32)
    (home/'tasks'/'.gideon-record-files.lock').write_bytes(b'')
    (home/'tasks'/'note.md').write_text('data')
    export_shards(home,tmp_path/'export')
    assert import_shards(tmp_path/'export').rows['tasks'] == [{'id':'note.md','text':'data'}]


def test_file_written_since_read_is_left_unchanged(tmp_path):
    root=tmp_path/'tasks';root.mkdir();(root/'note.md').write_text('old')
    entry=inv.by_id('tasks')
    read=_json_rows_from_entity_dir(root,entry_path=entry.path)
    (root/'note.md').write_text('new local writer')
    result=apply_rows(entry.kind,root,[{'id':'note.md','text':'stale peer'}],entry=entry,read_rows=read)
    assert result.moved == ['note.md']
    assert (root/'note.md').read_text() == 'new local writer'


def test_invalid_binary_representation_refused_before_write(tmp_path):
    root=tmp_path/'tasks'
    with pytest.raises(ValueError):
        apply_rows('json_entity_dir',root,[{'id':'bad.bin','base64':'%%%'}])
    assert not root.exists()


def test_replace_only_plain_workspace_pointer_roundtrips_exactly(tmp_path):
    home=tmp_path/'home';home.mkdir()
    pointer=home/'workspace_dir';pointer.write_bytes(b'/chosen/workspace')
    out=tmp_path/'export'
    result=export_shards(home,out,entries=['workspace_dir'])
    assert not result.skipped
    row=import_shards(out).rows['workspace_dir'][0]
    assert row == {'id':'workspace_dir','text':'/chosen/workspace'}
    target=tmp_path/'restored'/'workspace_dir'
    applied=apply_rows('json_file',target,[row])
    assert applied.written == 1 and target.read_bytes() == pointer.read_bytes()


def test_single_json_document_newer_writer_is_not_replaced(tmp_path):
    from gideon.operations.durability.shards import _json_rows_from_file
    path=tmp_path/'settings.json';path.write_text('{"v":1}')
    read=_json_rows_from_file(path)
    path.write_text('{"v":2}')
    result=apply_rows('json_file',path,[{'id':path.name,'data':{'v':0}}],read_rows=read)
    assert result.moved == [path.name] and json.loads(path.read_text()) == {'v':2}
