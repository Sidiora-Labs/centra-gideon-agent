"""Newest-copy cursor distinguishes poison, incomplete copies and linked landings."""
import json
from pathlib import Path
import pytest
from test_durability_convergence_e2e import FolderTransport
from test_sync_latest_copy_filesystem import task, cycle
from gideon.operations.durability.cursor import Cursor
from gideon.operations.durability.pull_engine import _materialize, _pull_one_seq
from gideon.integrations.sync_transports.base import SyncObject
from gideon.operations.durability import inventory as inv
from gideon.operations.durability.shards import export_shards, import_shards


def seed(tmp_path):
    a,b=tmp_path/'a',tmp_path/'b';task(a,'one')
    transport=FolderTransport(tmp_path/'store')
    assert cycle(transport,a,'a').ok
    return a,b,transport,tmp_path/'store'/'machines'/'a'/'seq-0001'/'manifest.json'


@pytest.mark.parametrize('value',[[],{'shards':[None]},{'shards':'wrong'},{'shards':[],'agreements':[]}])
def test_malformed_manifest_is_named_poison_and_advances(tmp_path,value):
    a,b,transport,manifest=seed(tmp_path)
    manifest.write_text(json.dumps(value))
    result=cycle(transport,b,'b')
    assert not result.ok
    outcome=result.pulled.outcomes[0]
    assert outcome.verdict == 'payload-bad' and outcome.advanced
    assert Cursor(b/'sync').seq_of('a') == 1
    assert not (b/'tasks'/'item.json').exists()


def test_manifest_escaping_path_refused_before_read_or_write(tmp_path):
    a,b,transport,manifest=seed(tmp_path)
    data=json.loads(manifest.read_text());data['shards'][0]['path']='../../outside'
    manifest.write_text(json.dumps(data))
    result=cycle(transport,b,'b')
    assert not result.ok and result.pulled.outcomes[0].verdict == 'payload-bad'
    assert 'outside' in result.pulled.outcomes[0].detail
    assert not (tmp_path/'outside').exists()


def test_linked_file_holds_cursor_but_other_file_arrives_then_retry(tmp_path):
    a,b,transport,manifest=seed(tmp_path)
    (a/'tasks'/'other.json').write_text('{"title":"safe"}')
    assert cycle(transport,a,'a').ok
    (b/'tasks').mkdir(parents=True)
    outside=tmp_path/'outside';outside.write_text('unchanged')
    (b/'tasks'/'item.json').symlink_to(outside)
    result=cycle(transport,b,'b')
    assert not result.ok
    outcome=result.pulled.outcomes[0]
    assert outcome.verdict == 'prerequisite-absent' and not outcome.advanced
    assert Cursor(b/'sync').seq_of('a') == 0
    assert (b/'tasks'/'other.json').exists()
    assert outside.read_text() == 'unchanged'
    (b/'tasks'/'item.json').unlink()
    assert cycle(transport,b,'b').ok
    assert Cursor(b/'sync').seq_of('a') == 2
    assert json.loads((b/'tasks'/'item.json').read_text())['title'] == 'one'


def test_object_path_validation_precedes_first_materialized_write(tmp_path):
    prefix='machines/a/seq-0001/'
    objects=[SyncObject(prefix+'safe.json',b'{}'),SyncObject(prefix+'../outside',b'bad')]
    with pytest.raises(ValueError):_materialize(objects,prefix,tmp_path/'stage')
    assert not (tmp_path/'stage').exists() and not (tmp_path/'outside').exists()


def test_foreign_machine_path_is_poison_without_transport_io(tmp_path):
    transport=FolderTransport(tmp_path/'store')
    result=_pull_one_seq(transport,tmp_path/'home','../foreign',1,None)
    assert result.verdict == 'payload-bad' and result.refused
    assert not (tmp_path/'home').exists()


def test_actual_agent_override_and_connector_catalog_are_declared_and_carried(tmp_path):
    home=tmp_path/'home';home.mkdir()
    for identity in ('agent_overrides','connector_catalog'):
        entry=inv.by_id(identity)
        assert entry and entry.exported_on_write and not entry.secret
        (home/entry.path).write_text('{"sample":"configuration"}')
    out=tmp_path/'export';result=export_shards(home,out)
    assert not result.skipped
    rows=import_shards(out).rows
    assert rows['agent_overrides'][0]['data'] == {'sample':'configuration'}
    assert rows['connector_catalog'][0]['data'] == {'sample':'configuration'}
