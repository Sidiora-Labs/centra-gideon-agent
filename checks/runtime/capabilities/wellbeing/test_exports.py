import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_exports import register
from gideon.workspace.capabilities.wellbeing.exports import ExportStore, TABLES
from gideon.workspace.capabilities.wellbeing.store import MeasurementError, MeasurementStore
from gideon.workspace.capabilities.wellbeing.labs import LabStore
from gideon.workspace.capabilities.wellbeing.apple_health import AppleHealthStore
from gideon.workspace.capabilities.wellbeing.genome import GenomeStore
from gideon.workspace.capabilities.wellbeing.memory_practice import MemoryPracticeStore
from gideon.workspace.capabilities.wellbeing.cognition import CognitiveStore
from gideon.workspace.capabilities.wellbeing.substances import ConsumptionStore
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore
from gideon.workspace.capabilities.wellbeing.life_calendar import LifeCalendarStore
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider


def weight(request='weight', value=72):
    return dict(request_id=request, kind='body_weight', observed_at='2026-09-25T09:00:00+02:00', values={'weight': value}, unit='kg', source='manual scale', notes='original')


def commit_import(store, payload, request):
    preview = store.preview(payload)
    return store.commit(dict(payload, preview_id=preview['preview_id'], request_id=request))


def seed(home):
    measurements = MeasurementStore(home)
    row = measurements.create(weight())
    measurements.correct(row['id'], dict(request_id='weight-edit', revision=1, values={'weight': 73}))
    lab_text = 'analyte,observed_at,value,unit\r\nGlucose,2026-09-25T09:00:00Z,92,mg/dL\r\n'
    commit_import(LabStore(home), dict(filename='labs.csv', format='csv', content=lab_text, source='original laboratory'), 'labs-import')
    xml = b'<HealthData><Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="123" startDate="2026-09-25 09:00:00 +0000" endDate="2026-09-25 10:00:00 +0000"/></HealthData>'
    commit_import(AppleHealthStore(home), dict(filename='export.xml', format='xml', content_base64=base64.b64encode(xml).decode(), source='user watch'), 'apple-import')
    genome = '\ufeffrsid\tchromosome\tposition\tgenotype\r\nrs1\t1\t123\tAG\r\n'
    commit_import(GenomeStore(home), dict(filename='genome.tsv', format='tsv', content=genome, assembly='GRCh37', source='user export'), 'genome-import')
    memory = MemoryPracticeStore(home)
    card = memory.create(dict(request_id='memory', front='Question', back='Answer', source='personal notes', tags=['study']))
    memory.update(card['id'], dict(request_id='memory-edit', revision=1, back='Revised answer'))
    cognition = CognitiveStore(home)
    session = cognition.start(dict(request_id='session', kind='arithmetic', planned_trials=2, time_limit_seconds=600))
    cognition.cancel(session['id'], dict(request_id='cancel', revision=1))
    substances = ConsumptionStore(home)
    entry = substances.create_entry(dict(request_id='substance', kind='nicotine', name='Labeled gum', observed_at='2026-09-25T09:00:00Z', count=1, details={'mg_per_unit': 2}, source='manual', notes=''))
    substances.delete_entry(entry['id'], dict(request_id='substance-delete', revision=1))
    interventions = InterventionStore(home)
    plan = interventions.create_plan(dict(request_id='plan', name='Walk', kind='activity', instructions='Personal plan', source='personal', timezone='UTC', start_date='2026-09-20', end_date=None, weekdays=list(range(7))))
    interventions.record(plan['id'], dict(request_id='completion', date='2026-09-25', status='completed', observed_at='2026-09-25T09:00:00Z', notes='done'))
    life = LifeCalendarStore(home)
    life.configure(dict(request_id='life-config', revision=0, birth_date='2000-02-29', horizon_years=80, sleep_hours=8, timezone='UTC', budgets=[], source='assumption', reminder={'enabled': False, 'time': '09:00'}))
    life.create_event(dict(request_id='life-event', date='2026-09-25', title='Milestone', notes='My record', kind='recorded', source='journal'))
    return lab_text.encode(), xml, genome.encode()


def test_all_domain_history_originals_and_canonical_archive(tmp_path):
    originals = seed(tmp_path)
    store = ExportStore(tmp_path)
    preview = store.preview()
    assert preview['schema'] == 'gideon.wellbeing-export'
    assert preview['version'] == 1
    assert set(preview['counts']) == set(TABLES)
    assert preview['counts']['measurements'] == 2
    assert preview['counts']['laboratory'] == 3
    assert preview['counts']['apple'] == 2
    assert preview['counts']['genome'] == 2
    assert preview['counts']['memory'] == 2
    assert preview['counts']['cognition'] == 2
    assert preview['counts']['substances'] == 2
    assert preview['counts']['interventions'] == 2
    assert preview['counts']['life'] == 2
    assert preview['attachment_count'] == 5
    assert store.list_exports() == []
    metadata = store.create({'request_id': 'archive'})
    raw = store.download(metadata['id'])
    document = json.loads(raw)
    assert document['counts'] == preview['counts'] == metadata['counts']
    assert document['schema'] == metadata['schema']
    assert document['version'] == metadata['version']
    assert document['generated_at'] == metadata['generated_at']
    assert metadata['sha256'] == hashlib.sha256(raw).hexdigest()
    assert metadata['bytes'] == len(raw)
    assert document['sections']['measurements']['history'][0]['values'] == {'weight': 72}
    assert document['sections']['measurements']['history'][1]['values'] == {'weight': 73}
    assert document['sections']['substances']['entries'][-1]['deleted'] is True
    assert document['sections']['interventions']['records'][0]['status'] == 'completed'
    assert document['sections']['life']['history'][0]['assumption'] == 'user_declared_horizon'
    assert 'expected' not in json.dumps(document['sections']['cognition'])
    assert document['omitted'] == metadata['omitted'] == preview['omitted']
    assert 'operation_retry_receipts' in document['omitted']
    embedded = [base64.b64decode(item['content_base64']) for item in document['attachments']]
    assert all(original in embedded for original in originals)
    for item in document['attachments']:
        assert hashlib.sha256(base64.b64decode(item['content_base64'])).hexdigest() == item['reference']['sha256']
        assert hashlib.sha256(item['descriptor'].encode()).hexdigest() == item['descriptor_sha256']
        assert item['bytes'] == len(base64.b64decode(item['content_base64']))
    cards = [json.loads(raw) for raw in embedded if b'"front"' in raw]
    assert {card['back'] for card in cards} == {'Answer', 'Revised answer'}
    artifact = store.artifacts.get(metadata['artifact']['slug'], version=1)
    assert artifact.readonly
    assert json.loads(artifact.content) == metadata
    assert ExportStore(tmp_path).download(metadata['id']) == raw
    assert ExportStore(tmp_path).get(metadata['id']) == metadata
    assert ExportStore(tmp_path).list_exports() == [metadata]


def test_snapshot_is_immutable_and_receipts_do_not_reexport(tmp_path):
    store = ExportStore(tmp_path)
    first = store.create({'request_id': 'empty'})
    original = store.download(first['id'])
    assert first['attachment_count'] == 0
    assert all(value == 0 for value in first['counts'].values())
    MeasurementStore(tmp_path).create(weight())
    second = store.create({'request_id': 'with-record'})
    assert second['id'] != first['id']
    assert second['counts']['measurements'] == 1
    assert store.create({'request_id': 'empty'}) == first
    assert store.download(first['id']) == original
    assert [row['id'] for row in store.list_exports()] == [second['id'], first['id']]
    assert 'wellbeing_exports' not in json.loads(store.download(second['id']))['sections']
    with pytest.raises(MeasurementError, match='already used'):
        store.create({'request_id': 'weight'})
    assert len(store.list_exports()) == 2


def test_concurrent_retry_single_durable_export(tmp_path):
    MeasurementStore(tmp_path).create(weight())
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: ExportStore(tmp_path).create({'request_id': 'same'}), range(4)))
    assert all(row == rows[0] for row in rows)
    assert len(ExportStore(tmp_path).list_exports()) == 1
    assert len(list((tmp_path / 'artifacts').glob('health-export-*'))) == 1


@pytest.mark.parametrize('payload', [None, [], {}, {'request_id': ''}, {'request_id': 5}, {'request_id': 'a', 'home': '/elsewhere'}, {'request_id': 'a', 'domains': ['measurements']}])
def test_create_rejects_overrides_without_archive(tmp_path, payload):
    store = ExportStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.create(payload)
    assert store.list_exports() == []
    assert not list((tmp_path / 'artifacts').glob('health-export-*'))


def test_pagination_never_truncates_export_and_large_archive_is_companion(tmp_path):
    store = MeasurementStore(tmp_path)
    for index in range(301):
        store.create(dict(weight(str(index)), notes='evidence ' * 430))
    exports = ExportStore(tmp_path)
    row = exports.create({'request_id': 'large'})
    assert row['bytes'] > 1024 * 1024
    assert row['counts']['measurements'] == 301
    assert len(json.loads(exports.download(row['id']))['sections']['measurements']['history']) == 301
    assert len(exports.artifacts.get(row['artifact']['slug'], version=1).content) < 1024 * 1024


def test_missing_and_modified_export_fail_explicitly(tmp_path):
    store = ExportStore(tmp_path)
    with pytest.raises(MeasurementError) as missing:
        store.download('not-an-export')
    assert missing.value.status == 404
    row = store.create({'request_id': 'export'})
    path = tmp_path / 'artifacts' / row['artifact']['slug'] / 'versions' / row['artifact']['filename']
    path.write_bytes(b'changed')
    with pytest.raises(MeasurementError, match='hash mismatch'):
        store.download(row['id'])
    path.unlink()
    with pytest.raises(MeasurementError, match='unavailable'):
        store.download(row['id'])
    assert store.get(row['id']) == row


def test_original_hash_corruption_prevents_partial_export(tmp_path):
    seed(tmp_path)
    original = next((tmp_path / 'artifacts').glob('genome-source-*/versions/original@*'))
    original.write_bytes(b'corrupt')
    store = ExportStore(tmp_path)
    with pytest.raises(MeasurementError, match='hash mismatch'):
        store.preview()
    with pytest.raises(MeasurementError, match='hash mismatch'):
        store.create({'request_id': 'rejected'})
    assert store.list_exports() == []
    assert not list((tmp_path / 'artifacts').glob('health-export-*'))


def test_symlink_original_rejected_even_with_matching_content(tmp_path):
    seed(tmp_path)
    original = next((tmp_path / 'artifacts').glob('genome-source-*/versions/original@*'))
    target = tmp_path / 'outside.tsv'
    target.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(target)
    with pytest.raises(MeasurementError, match='unsafe'):
        ExportStore(tmp_path).create({'request_id': 'unsafe'})


def test_source_artifact_missing_is_explicit(tmp_path):
    import shutil
    seed(tmp_path)
    folder = next((tmp_path / 'artifacts').glob('memory-card-*'))
    shutil.rmtree(folder)
    with pytest.raises(MeasurementError, match='unavailable'):
        ExportStore(tmp_path).preview()


@pytest.mark.asyncio
async def test_native_agent_archive_preview_download_and_isolation(tmp_path):
    MeasurementStore(tmp_path).create(weight())
    provider = WellbeingProvider(tmp_path)
    tool = (await provider.list_tools())[0]
    assert 'exports_download' in tool.parameters['properties']['operation']['enum']
    result = await provider.invoke('wellbeing_records', {'operation': 'exports_preview'})
    assert result.success
    assert json.loads(result.output)['counts']['measurements'] == 1
    result = await provider.invoke('wellbeing_records', {'operation': 'exports_create', 'payload': {'request_id': 'agent'}})
    assert result.success
    row = json.loads(result.output)
    result = await provider.invoke('wellbeing_records', {'operation': 'exports_download', 'id': row['id']})
    assert result.success
    assert base64.b64decode(json.loads(result.output)['content_base64']) == ExportStore(tmp_path).download(row['id'])
    result = await provider.invoke('wellbeing_records', {'operation': 'exports_get', 'id': row['id']})
    assert json.loads(result.output) == row
    result = await provider.invoke('wellbeing_records', {'operation': 'exports_list'})
    assert json.loads(result.output) == [row]
    result = await provider.invoke('wellbeing_records', {'operation': 'exports_preview', 'payload': {'home': str(tmp_path)}})
    assert not result.success
    result = await provider.invoke('wellbeing_records', {'operation': 'exports_invalid'})
    assert not result.success
    other = WellbeingProvider(tmp_path / 'other')
    result = await other.invoke('wellbeing_records', {'operation': 'exports_get', 'id': row['id']})
    assert not result.success


@pytest.mark.asyncio
async def test_actual_http_preview_create_reopen_download_and_home_isolation(tmp_path):
    home, other = tmp_path / 'a', tmp_path / 'b'
    MeasurementStore(home).create(weight())
    app = web.Application()
    register(app, home)
    isolated = web.Application()
    register(isolated, other)
    async with TestClient(TestServer(app)) as client, TestClient(TestServer(isolated)) as second:
        base = '/api/capabilities/wellbeing/exports'
        preview = await (await client.get(base + '/preview')).json()
        assert preview['counts']['measurements'] == 1
        assert await (await client.get(base)).json() == {'exports': []}
        response = await client.post(base, json={'request_id': 'http'})
        assert response.status == 200
        row = await response.json()
        assert await (await client.get(base + '/' + row['id'])).json() == row
        response = await client.get(base + '/' + row['id'] + '/download')
        assert response.status == 200
        assert response.content_type == 'application/json'
        assert 'attachment' in response.headers['Content-Disposition']
        raw = await response.read()
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        assert json.loads(raw)['counts']['measurements'] == 1
        assert await (await client.post(base, json={'request_id': 'http'})).json() == row
        assert (await second.get(base + '/' + row['id'])).status == 404
        assert (await second.get(base + '/' + row['id'] + '/download')).status == 404
        assert await (await second.get(base)).json() == {'exports': []}
        assert (await client.post(base, json={'request_id': 'bad', 'home': str(other)})).status == 400
        assert (await client.post(base, data='{', headers={'Content-Type': 'application/json'})).status == 400
        assert ExportStore(home).download(row['id']) == raw
        assert not (other / 'artifacts').exists()
