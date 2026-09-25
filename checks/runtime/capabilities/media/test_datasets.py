import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.datasets import DatasetStore
from gideon.workspace.capabilities.media.datasets_http import register_datasets
from gideon.workspace.capabilities.media.images import ImageService
from gideon.workspace.capabilities.media.jobs import MediaJobs
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchStore, SketchError
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def datasets(tmp_path):
    return DatasetStore(tmp_path / 'datasets.sqlite3', ImageService(NativeArtifactProvider(tmp_path / 'artifacts')))


def body(datasets, request='first', caption='A red image'):
    output = io.BytesIO()
    Image.new('RGB', (8, 10), 'red').save(output, 'PNG')
    artifact = datasets.images.artifacts.create_binary(name='Source', data=output.getvalue(), mime='image/png')
    return dict(title='Dataset', base_model='owner/model', request_id=request, entries=[dict(artifact_id=artifact.slug, version=1, caption=caption)])


def test_dataset_save_replay_revisions_history_and_reopen(datasets):
    first_body = body(datasets)
    first = datasets.save(first_body)
    assert first['revision'] == 1
    assert first['title'] == 'Dataset'
    assert first['base_model'] == 'owner/model'
    assert first['entries'] == first_body['entries']
    assert first['updated_at']
    assert datasets.save(first_body) == first
    second_body = dict(first_body, revision=1, request_id='second', title='Updated')
    second_body['entries'] = [dict(first_body['entries'][0], caption='New caption\nwith detail')]
    second = datasets.save(second_body, first['id'])
    assert second['revision'] == 2
    assert second['id'] == first['id']
    assert datasets.get(first['id']) == second
    assert datasets.get(first['id'], 1) == first
    assert datasets.history(first['id'])['items'] == [second, first]
    assert datasets.list()['items'] == [second]
    reopened = DatasetStore(datasets.path, datasets.images)
    assert reopened.get(first['id']) == second
    assert reopened.get(first['id'], 1) == first
    assert reopened.save(second_body, first['id']) == second
    with pytest.raises(SketchError) as stale:
        reopened.save(dict(second_body, request_id='stale'), first['id'])
    assert stale.value.status == 409
    with pytest.raises(SketchError) as conflict:
        reopened.save(dict(first_body, title='Conflicting'))
    assert conflict.value.status == 409


def test_real_zip_export_uses_pinned_pixels_captions_and_no_host_paths(datasets):
    payload = body(datasets, caption='صورة حمراء\n红色图像')
    saved = datasets.save(payload)
    source_id = payload['entries'][0]['artifact_id']
    original = datasets.images.artifacts.raw_bytes(source_id)[0]
    archive_bytes = datasets.export(saved['id'], 1)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert sorted(archive.namelist()) == ['00000.png', 'manifest.json', 'metadata.jsonl']
        image = Image.open(io.BytesIO(archive.read('00000.png')))
        assert image.size == (8, 10)
        assert image.getpixel((2, 2)) == (255, 0, 0)
        metadata = json.loads(archive.read('metadata.jsonl'))
        assert metadata == {'file_name': '00000.png', 'text': payload['entries'][0]['caption']}
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest == saved
        assert str(datasets.path.parent) not in json.dumps(manifest)
        assert all('/' not in name for name in archive.namelist())
    datasets.save(dict(payload, request_id='edit', revision=1, entries=[dict(payload['entries'][0], caption='Different')]), saved['id'])
    with zipfile.ZipFile(io.BytesIO(datasets.export(saved['id'], 1))) as archive:
        assert json.loads(archive.read('metadata.jsonl'))['text'] == payload['entries'][0]['caption']
    assert datasets.images.artifacts.raw_bytes(source_id)[0] == original
    assert datasets.images.artifacts.get(source_id).version == 1


@pytest.mark.parametrize('override', [
    {'title': ''}, {'title': 'a'*121}, {'base_model': '/tmp/private'}, {'base_model': '../model'},
    {'base_model': 'model'}, {'entries': []}, {'entries': {}}, {'request_id': ''}, {'revision': 2},
    {'home': '/tmp'}, {'provider': 'other'},
])
def test_invalid_dataset_shapes_do_not_create_revisions(datasets, override):
    payload = body(datasets)
    with pytest.raises(SketchError):
        datasets.save(dict(payload, **override))
    assert datasets.list()['items'] == []


@pytest.mark.parametrize('override', [{'caption': ''}, {'caption': 'a'*2001}, {'caption': 'bad\x00caption'}, {'version': True}, {'version': 2}, {'artifact_id': 'missing'}, {'path': '/tmp/image'}])
def test_invalid_or_missing_samples_are_not_accepted(datasets, override):
    payload = body(datasets)
    payload['entries'] = [dict(payload['entries'][0], **override)]
    with pytest.raises(SketchError):
        datasets.save(payload)
    assert datasets.list()['items'] == []


def test_duplicate_images_and_missing_revision_fail(datasets):
    payload = body(datasets)
    with pytest.raises(SketchError):
        datasets.save(dict(payload, entries=payload['entries']*2))
    first = datasets.save(payload)
    with pytest.raises(SketchError):
        datasets.save(dict(payload, request_id='withoutrevision'), first['id'])
    with pytest.raises(SketchError) as missing:
        datasets.get(first['id'], 50)
    assert missing.value.status == 404
    with pytest.raises(SketchError):
        datasets.history('missing')
    assert datasets.get(first['id']) == first


def test_competing_revision_writers_preserve_one_winner(datasets):
    payload = body(datasets)
    first = datasets.save(payload)
    def write(index):
        try:
            return datasets.save(dict(payload, revision=1, title=f'Writer {index}', request_id=f'writer-{index}'), first['id'])
        except SketchError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [1, 2]))
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert 409 in results
    assert len(datasets.history(first['id'])['items']) == 2
    assert datasets.get(first['id'])['revision'] == 2
    assert datasets.get(first['id'], 1) == first


@pytest.mark.asyncio
async def test_http_dataset_export_history_and_native_tool_same_store(datasets):
    sketches = SketchStore(datasets.path.parent / 'sketches.sqlite3', datasets.images.artifacts)
    jobs = MediaJobs(datasets.path.parent / 'jobs.sqlite3', sketches)
    app = web.Application()
    register_jobs(app, jobs)
    register_datasets(app)
    payload = body(datasets)
    async with TestClient(TestServer(app)) as client:
        response = await client.post('/api/capabilities/media/datasets', json=payload)
        assert response.status == 201
        saved = await response.json()
        assert saved['entries'] == payload['entries']
        response = await client.get('/api/capabilities/media/datasets/'+saved['id'])
        assert await response.json() == saved
        response = await client.get('/api/capabilities/media/datasets/'+saved['id']+'/history')
        assert (await response.json())['items'] == [saved]
        response = await client.get('/api/capabilities/media/datasets/'+saved['id']+'/revisions/1/export')
        assert response.status == 200
        assert response.content_type == 'application/zip'
        assert response.headers['Content-Disposition'] == 'attachment; filename="dataset.zip"'
        with zipfile.ZipFile(io.BytesIO(await response.read())) as archive:
            assert json.loads(archive.read('manifest.json')) == saved
        for query in ('home=other', 'runtime=other', 'provider=other'):
            response = await client.get('/api/capabilities/media/datasets?'+query)
            assert response.status == 400
        response = await client.get('/api/capabilities/media/datasets/missing')
        assert response.status == 404
        provider = MediaToolProvider(sketches, MediaLibrary(datasets.images.artifacts))
        result = await provider.invoke('media_datasets_get', {'dataset_id': saved['id'], 'revision': 1})
        assert result.success
        assert json.loads(result.output) == saved
        result = await provider.invoke('media_datasets_list', {})
        assert json.loads(result.output)['items'] == [saved]
        result = await provider.invoke('media_datasets_get', {'dataset_id': saved['id'], 'home': 'other'})
        assert not result.success
