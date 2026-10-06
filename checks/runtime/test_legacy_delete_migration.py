"""Old delete intent migrates without removing logs or restoring stale records."""
import asyncio
import json
from test_sync_deletions_filesystem import homes
from test_sync_latest_copy_filesystem import cycle, task
from gideon.operations.durability.ancestors import Ancestors
from gideon.operations.durability.tombstones import record_tombstone, TOMBSTONE_FILE
from gideon.operations.durability.conflicts import ConflictQueue


def test_existing_delete_log_migrates_and_original_is_untouched(tmp_path):
    a,b,transport=homes(tmp_path)
    (a/'tasks'/'item.json').unlink()
    record_tombstone(a/'tasks','item',now='2026-10-06T11:00:00+00:00')
    log=a/'tasks'/TOMBSTONE_FILE
    original=log.read_bytes()
    assert cycle(transport,a,'a').ok
    mark=Ancestors(a/'sync').deleted('tasks')['item']
    assert mark.held and mark.at == '2026-10-06T11:00:00+00:00'
    assert log.read_bytes() == original
    assert cycle(transport,b,'b').ok
    assert not (b/'tasks'/'item.json').exists()
    # A recreated record supersedes the old log; later copies never replay its deletion.
    task(a,'restored')
    assert cycle(transport,a,'a').ok
    assert cycle(transport,b,'b').ok
    assert json.loads((b/'tasks'/'item.json').read_text())['title'] == 'restored'
    assert cycle(transport,a,'a').ok
    assert (a/'tasks'/'item.json').exists()
    assert log.read_bytes() == original


def test_unknown_old_delete_requires_review_instead_of_erasing_peer_edit(tmp_path):
    from test_durability_convergence_e2e import FolderTransport
    a,b=tmp_path/'a',tmp_path/'b';a.mkdir()
    record_tombstone(a/'tasks','item',now='2026-10-06T11:00:00+00:00')
    task(b,'unseen edit')
    transport=FolderTransport(tmp_path/'store')
    assert cycle(transport,a,'a').ok
    assert cycle(transport,b,'b').ok
    assert json.loads((b/'tasks'/'item.json').read_text())['title'] == 'unseen edit'
    assert ConflictQueue(b).items()[0].deleted == 'there'


def test_native_task_project_and_list_deletes_write_no_old_log(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path))
    from gideon.engine.tasks.native import NativeTaskProvider
    from gideon.engine.tasks.hierarchy import HierarchyStore
    provider=NativeTaskProvider()
    row=asyncio.run(provider.create_task(title='temporary'))
    assert asyncio.run(provider.delete_task(row.id))
    hierarchy=HierarchyStore()
    project=hierarchy.create_project('Example')
    listing=hierarchy.create_task_list('List',project_id=project.id)
    assert hierarchy.delete_task_list(listing.id)
    assert hierarchy.delete_project(project.id)
    assert not list(tmp_path.rglob(TOMBSTONE_FILE))
