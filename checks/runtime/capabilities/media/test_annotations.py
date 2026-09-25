import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.annotations import AnnotationStore
from gideon.workspace.capabilities.media.annotations_http import register_annotations
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def store(tmp_path):
    return AnnotationStore(tmp_path / 'annotations.sqlite3', NativeArtifactProvider(tmp_path / 'artifacts'))


def image(store):
    buffer = io.BytesIO()
    Image.new('RGB', (20, 20), 'blue').save(buffer, 'PNG')
    return store.artifacts.create_binary(name='Source', data=buffer.getvalue(), mime='image/png', source='import', event_metadata={'original_filename': 'source.png'})


def body(revision=0, request='first', entries=None):
    return dict(revision=revision, request_id=request,
                annotations=entries if entries is not None else [dict(id='note', text='Original observation', region=[0.1, 0.2, 0.3, 0.4])],
                attribution=dict(creator='Creator', license='CC BY 4.0', source_url='https://example.com/image'))


def test_blank_projection_never_changes_original_or_creates_revision(store):
    artifact = image(store)
    raw = store.artifacts.raw_bytes(artifact.slug)[0]
    before = store.artifacts.get(artifact.slug).to_dict()
    record = store.get(artifact.slug, 1)
    assert record['revision'] == 0
    assert record['annotations'] == []
    assert record['attribution'] == dict(creator='', license='', source_url='')
    assert record['updated_at'] is None
    assert record['source_available'] is True
    assert record['source_kind'] == 'image'
    assert store.history(artifact.slug, 1)['items'] == []
    assert store.artifacts.get(artifact.slug).to_dict() == before
    assert store.artifacts.raw_bytes(artifact.slug)[0] == raw


def test_save_edit_restore_preserves_all_revisions_and_import_attribution(store):
    artifact = image(store)
    raw = store.artifacts.raw_bytes(artifact.slug)[0]
    initial = store.save(artifact.slug, 1, body())
    assert initial['revision'] == 1
    assert initial['annotations'][0]['region'] == [0.1, 0.2, 0.3, 0.4]
    edited = body(1, 'second', [dict(id='note', text='Corrected\nmultiline\tobservation')])
    edited['attribution']['creator'] = 'Corrected creator'
    second = store.save(artifact.slug, 1, edited)
    assert second['revision'] == 2
    reopened = AnnotationStore(store.path, NativeArtifactProvider(store.artifacts.root))
    assert reopened.get(artifact.slug, 1) == second
    assert reopened.get(artifact.slug, 1, 1) == initial
    restored = dict(revision=2, request_id='restore', annotations=initial['annotations'], attribution=initial['attribution'])
    third = reopened.save(artifact.slug, 1, restored)
    assert third['revision'] == 3
    assert third['annotations'] == initial['annotations']
    assert reopened.get(artifact.slug, 1, 2)['annotations'][0]['text'] == 'Corrected\nmultiline\tobservation'
    history = reopened.history(artifact.slug, 1, limit=2)
    assert history['total'] == 3
    assert [item['revision'] for item in history['items']] == [3, 2]
    assert reopened.history(artifact.slug, 1, offset=2, limit=2)['items'][0]['revision'] == 1
    assert reopened.artifacts.raw_bytes(artifact.slug)[0] == raw
    assert reopened.artifacts.get(artifact.slug).events[0].metadata == {'original_filename': 'source.png'}


def test_retry_returns_original_accepted_revision_and_conflicts_on_changed_payload(store):
    artifact = image(store)
    first = store.save(artifact.slug, 1, body())
    second = store.save(artifact.slug, 1, body(1, 'second', []))
    assert store.save(artifact.slug, 1, body()) == first
    assert store.get(artifact.slug, 1) == second
    with pytest.raises(SketchError) as conflict:
        store.save(artifact.slug, 1, body(entries=[]))
    assert conflict.value.status == 409
    assert store.history(artifact.slug, 1)['total'] == 2


def test_concurrent_annotation_writers_have_one_winner(store):
    artifact = image(store)
    other = AnnotationStore(store.path, NativeArtifactProvider(store.artifacts.root))
    def save(pair):
        target, request = pair
        try:
            return target.save(artifact.slug, 1, body(request=request))
        except SketchError as exc:
            return exc.status
    with ThreadPoolExecutor(2) as pool:
        values = list(pool.map(save, [(store, 'one'), (other, 'two')]))
    assert sum(isinstance(value, dict) for value in values) == 1
    assert 409 in values
    assert store.history(artifact.slug, 1)['total'] == 1


@pytest.mark.parametrize('entry', [
    {'id': 'n', 'text': ''}, {'id': 'n', 'text': 'x'*4001}, {'id': '', 'text': 'note'},
    {'id': 'n', 'text': '\x00'}, {'id': 'n', 'text': 'note', 'region': [0, 0, 0, 1]},
    {'id': 'n', 'text': 'note', 'region': [0.5, 0, 0.6, 1]},
    {'id': 'n', 'text': 'note', 'region': [0, 0, 1]},
    {'id': 'n', 'text': 'note', 'region': [True, 0, 1, 1]},
    {'id': 'n', 'text': 'note', 'region': [float('nan'), 0, 1, 1]},
    {'id': 'n', 'text': 'note', 'time_seconds': 2},
    {'id': 'n', 'text': 'note', 'path': '/tmp/private'},
])
def test_invalid_image_annotations_do_not_persist(store, entry):
    artifact = image(store)
    with pytest.raises(SketchError):
        store.save(artifact.slug, 1, body(entries=[entry]))
    assert store.get(artifact.slug, 1)['revision'] == 0


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///tmp/private', 'https://user:secret@example.com/', 'https://', 'https://[invalid', 'data:text/plain,hello', 'https://example.com/\nsecret'])
def test_attribution_urls_reject_unsafe_or_credential_forms(store, url):
    artifact = image(store)
    request = body()
    request['attribution']['source_url'] = url
    with pytest.raises(SketchError):
        store.save(artifact.slug, 1, request)
    assert store.history(artifact.slug, 1)['total'] == 0


def test_duplicate_ids_empty_attribution_and_explicit_clears(store):
    artifact = image(store)
    with pytest.raises(SketchError):
        store.save(artifact.slug, 1, body(entries=[dict(id='same', text='one'), dict(id='same', text='two')]))
    first = store.save(artifact.slug, 1, body())
    request = body(1, 'clear', [])
    request['attribution'] = dict(creator='', license='', source_url='')
    cleared = store.save(artifact.slug, 1, request)
    assert cleared['annotations'] == []
    assert cleared['attribution'] == request['attribution']
    assert store.get(artifact.slug, 1, 1) == first


def test_versions_are_isolated_and_deleted_sources_retain_notes(store):
    artifact = image(store)
    first = store.save(artifact.slug, 1, body())
    store.artifacts.update_binary(artifact.slug, data=store.artifacts.raw_bytes(artifact.slug)[0], mime='image/png')
    assert store.get(artifact.slug, 2)['revision'] == 0
    assert store.get(artifact.slug, 1) == first
    store.artifacts.delete(artifact.slug)
    retained = store.get(artifact.slug, 1)
    assert retained['source_available'] is False
    assert retained['annotations'] == first['annotations']
    assert store.history(artifact.slug, 1)['total'] == 1
    with pytest.raises(SketchError) as missing:
        store.save(artifact.slug, 1, body(1, 'missing'))
    assert missing.value.status == 404


def test_video_time_marks_are_explicitly_not_duration_verified(store):
    video = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.1', '-an', '-c:v', 'libx264', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'], check=True, capture_output=True).stdout
    artifact = store.artifacts.create_binary(name='Clip', data=video, mime='video/mp4', kind='video')
    saved = store.save(artifact.slug, 1, body(entries=[dict(id='time', text='At opening', time_seconds=0)]))
    assert saved['source_kind'] == 'video'
    assert saved['annotations'][0]['time_seconds'] == 0
    assert saved['duration_verified'] is False
    for value in (-1, float('inf'), True, 604801):
        with pytest.raises(SketchError):
            store.save(artifact.slug, 1, body(1, 'bad', [dict(id='time', text='Invalid', time_seconds=value)]))
    with pytest.raises(SketchError):
        store.save(artifact.slug, 1, body(1, 'region'))


def test_unknown_versions_and_isolated_homes(store, tmp_path):
    artifact = image(store)
    other = AnnotationStore(tmp_path / 'other/annotations.sqlite3', NativeArtifactProvider(tmp_path / 'other/artifacts'))
    with pytest.raises(SketchError):
        other.get(artifact.slug, 1)
    with pytest.raises(SketchError):
        store.get(artifact.slug, 999)
    with pytest.raises(SketchError):
        store.get(artifact.slug, 1, 999)
    with pytest.raises(SketchError):
        store.history(artifact.slug, 1, limit=101)


@pytest.mark.asyncio
async def test_real_http_annotation_save_history_and_stale_rejection(store):
    artifact = image(store)
    app = web.Application()
    register_annotations(app, store)
    async with TestClient(TestServer(app)) as client:
        base = f'/api/capabilities/media/library/{artifact.slug}/versions/1/annotations'
        response = await client.get(base)
        assert (await response.json())['revision'] == 0
        response = await client.put(base, json=body())
        assert response.status == 200
        first = await response.json()
        response = await client.put(base, json=body(1, 'second', []))
        assert response.status == 200
        response = await client.get(base+'/history?offset=1&limit=1')
        history = await response.json()
        assert history['total'] == 2
        assert history['items'][0]['revision'] == 1
        response = await client.get(base+'/history/1')
        assert await response.json() == first
        response = await client.put(base, json=body(0, 'stale'))
        assert response.status == 409
        response = await client.get(base+'?home=other')
        assert response.status == 400
        response = await client.put(base, json={**body(), 'provider': 'other'})
        assert response.status == 400
        response = await client.put(base, data='malformed')
        assert response.status == 400


@pytest.mark.asyncio
async def test_real_annotation_tools_share_http_store_and_history(store):
    artifact = image(store)
    provider = MediaToolProvider(SketchStore(store.path.parent/'sketches.sqlite3', store.artifacts), MediaLibrary(store.artifacts), store)
    arguments = dict(artifact_id=artifact.slug, version=1)
    result = await provider.invoke('media_annotations_save', {**arguments, **body()})
    assert result.success, result.error
    saved = json.loads(result.output)
    assert saved == store.get(artifact.slug, 1)
    result = await provider.invoke('media_annotations_get', {**arguments, 'annotation_revision': 1})
    assert result.success
    assert json.loads(result.output) == saved
    result = await provider.invoke('media_annotations_history', {**arguments, 'limit': 1})
    assert result.success
    assert json.loads(result.output)['total'] == 1
    result = await provider.invoke('media_annotations_save', {**arguments, **body(0, 'stale')})
    assert not result.success
    assert result.metadata['status'] == 409
    result = await provider.invoke('media_annotations_get', {**arguments, 'home': 'other'})
    assert not result.success
