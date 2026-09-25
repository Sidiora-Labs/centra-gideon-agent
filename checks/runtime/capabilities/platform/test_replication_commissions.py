import copy
import json

import pytest

from gideon.automation.triggers.store import TriggerStore
from gideon.core.sqlite_compat import sqlite3
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.creative.commissions import CommissionStore, TRIGGER_PREFIX
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform import replication_commissions as adapter


def source(home, suffix):
    works = WorkStore(home)
    work = works.create({'request_id':'work-'+suffix,'title':'Source '+suffix,'kind':'work','prompt':'A source.',
        'author_ref':None,'universe_ref':None,'active_draft_id':None})
    work = works.draft(work['id'], {'request_id':'draft-'+suffix,'revision':work['revision'],
        'text':'Canonical manuscript '+suffix+'.','note':'approved'})['work']
    store = CommissionStore(home)
    record = store.create({'request_id':'commission-'+suffix,'name':'Commission '+suffix,'target_ability':'series',
        'brief':{'intent':'Develop a pinned treatment.','genre':'mystery','category':'treatment','style':'Precise.',
                 'constraints':{'rating':'PG'},'seed_refs':[]},
        'cadence':{'kind':'interval','seconds':900,'timezone':'UTC'},
        'sources':[{'kind':'work','id':work['id'],'revision':work['revision']}],
        'steps':[{'id':'verify','title':'Verify source','operation':'source.verify','depends_on':[]},
                 {'id':'snapshot','title':'Save treatment','operation':'treatment.snapshot','depends_on':['verify']}],
        'enabled':True,'max_attempts':2})
    return store, work, record


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def raw(home, identity):
    with sqlite3.connect(home/'capabilities/creative/commissions.sqlite3') as database:
        return json.loads(database.execute('SELECT record FROM creative_commissions WHERE id=?',(identity,)).fetchone()[0])


def test_two_home_projection_preserves_definition_pins_and_materializes_no_execution_authority(tmp_path):
    first, second = tmp_path/'first', tmp_path/'second'
    _, work, commission = source(first, 'one')
    projected = rows(first)
    assert projected == [{'id':commission['id'],'data':{key:commission[key] for key in (
        'id','revision','name','target_ability','brief','sources','steps','created_at','updated_at')}}]
    wire = json.dumps(projected, sort_keys=True)
    for forbidden in ('cadence','enabled','max_attempts','mode','dispatch','schedule_','next_fire_at','credential','claim','runs','feedback'):
        assert forbidden not in wire
    received = adapter.apply_rows(second, adapter.ENTRY_ID, projected, {}, conflicts.ConflictQueue(second),
                                  '2026-09-25T12:00:00+00:00')
    assert (received.added,received.updated,received.removed,received.conflicts)==(1,0,0,0)
    stored = raw(second, commission['id'])
    assert stored['sources'] == [{'kind':'work','id':work['id'],'revision':work['revision']}]
    assert stored['enabled'] is False and stored['mode']=='planning' and stored['dispatch'] is None
    assert stored['max_attempts']==1 and stored['schedule_state']=='disabled' and stored['next_fire_at']==''
    assert TriggerStore(base_dir=second).get(TRIGGER_PREFIX+commission['id']) is None
    assert WorkStore(second).list()['items'] == []
    assert adapter.missing_dependencies(second, projected[0]) == stored['sources']
    with sqlite3.connect(second/'capabilities/creative/commissions.sqlite3') as database:
        for table in ('creative_commission_requests','creative_commission_runs','creative_commission_feedback'):
            assert database.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0] == 0


def test_fast_forward_conflict_selected_restore_and_deletion_preserve_receiver_authority(tmp_path):
    first, second = tmp_path/'first', tmp_path/'second'
    first_store, _, commission = source(first, 'shared')
    baseline = rows(first)
    copied = adapter.apply_rows(second, adapter.ENTRY_ID, baseline, {}, conflicts.ConflictQueue(second),
                                '2026-09-25T12:05:00+00:00')
    ancestor = copied.new_ancestors
    source_changed = first_store.update(commission['id'], {'revision':commission['revision'],'name':'Remote title'})
    forwarded = adapter.apply_rows(second, adapter.ENTRY_ID, rows(first), ancestor, conflicts.ConflictQueue(second),
                                   '2026-09-25T12:06:00+00:00')
    assert (forwarded.added,forwarded.updated,forwarded.removed,forwarded.conflicts)==(0,1,0,0)
    assert raw(second,commission['id'])['name']=='Remote title'
    assert raw(second,commission['id'])['enabled'] is False and TriggerStore(base_dir=second).get(TRIGGER_PREFIX+commission['id']) is None

    second_store = CommissionStore(second)
    local = second_store.update(commission['id'], {'revision':source_changed['revision'],
        'brief':{**source_changed['brief'],'intent':'Receiver-authored intent.'}})
    remote = first_store.update(commission['id'], {'revision':source_changed['revision'],'name':'Second remote title'})
    queue = conflicts.ConflictQueue(second)
    held = adapter.apply_rows(second, adapter.ENTRY_ID, rows(first), forwarded.new_ancestors, queue,
                              '2026-09-25T12:07:00+00:00')
    assert (held.added,held.updated,held.removed,held.conflicts)==(0,0,0,1)
    assert raw(second,commission['id'])['brief']['intent']=='Receiver-authored intent.'
    conflict = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)[0]
    with pytest.raises(ValueError, match='field restoration'):
        adapter.restore_fields(second, conflict.id, ['dispatch'], '2026-09-25T12:08:00+00:00')
    result = adapter.restore_fields(second, conflict.id, ['name'], '2026-09-25T12:08:00+00:00')
    assert result['fields']==['name']
    merged = raw(second, commission['id'])
    assert merged['name']==remote['name'] and merged['brief']==local['brief'] and merged['revision']==local['revision']+1
    assert merged['enabled'] is False and merged['dispatch'] is None
    delete_source, delete_target = tmp_path/'delete-source', tmp_path/'delete-target'
    _, _, doomed = source(delete_source, 'delete')
    initial = rows(delete_source)
    replica = adapter.apply_rows(delete_target, adapter.ENTRY_ID, initial, {}, conflicts.ConflictQueue(delete_target),
                                 '2026-09-25T12:10:00+00:00')
    adapter.write_row(delete_source, adapter.ENTRY_ID, None, doomed['id'])
    removed = adapter.apply_rows(delete_target, adapter.ENTRY_ID, [], replica.new_ancestors,
                                 conflicts.ConflictQueue(delete_target), '2026-09-25T12:11:00+00:00')
    assert (removed.added,removed.updated,removed.removed,removed.conflicts)==(0,0,1,0)
    assert rows(delete_target)==[] and TriggerStore(base_dir=delete_target).get(TRIGGER_PREFIX+doomed['id']) is None


def test_missing_dependencies_private_shapes_and_unrelated_local_records_fail_closed(tmp_path):
    first, second = tmp_path/'first', tmp_path/'second'
    _, _, commission = source(first, 'validation')
    projected = rows(first)
    bad = copy.deepcopy(projected); bad[0]['data']['dispatch']={'provider':'external'}
    with pytest.raises(ValueError, match='definition'):
        adapter.validate_entries([{'entry_id':adapter.ENTRY_ID,'rows':bad}])
    bad = copy.deepcopy(projected); bad[0]['data']['steps'][1]['depends_on']=['missing']
    with pytest.raises(ValueError, match='earlier'):
        adapter.validate_entries([{'entry_id':adapter.ENTRY_ID,'rows':bad}])
    local = copy.deepcopy(projected); local[0]['data']['name']='Unrelated local definition'
    adapter.write_row(second, adapter.ENTRY_ID, local[0], commission['id'])
    queue = conflicts.ConflictQueue(second)
    result = adapter.apply_rows(second, adapter.ENTRY_ID, projected, {}, queue, '2026-09-25T12:12:00+00:00')
    assert result.conflicts==1 and raw(second,commission['id'])['name']=='Unrelated local definition'
    assert queue.items(status=conflicts.STATUS_NEEDS_REVIEW)[0].ancestor_sha==''
    assert adapter.missing_dependencies(second, projected[0]) == projected[0]['data']['sources']
