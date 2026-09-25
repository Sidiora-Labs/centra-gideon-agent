import asyncio
import copy
import hashlib
import io
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchStore, SketchError
from gideon.workspace.capabilities.media.sprites import SpriteService
from gideon.workspace.capabilities.media.sprites_http import register_sprites
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def jobs(tmp_path):
    return MediaJobs(tmp_path / 'jobs.sqlite3', SketchStore(tmp_path / 'sketches.sqlite3', NativeArtifactProvider(tmp_path / 'artifacts')))


def frame(jobs, name, color=(200, 20, 40, 150), transparent=False):
    image = Image.new('RGBA', (8, 8))
    if not transparent:
        for y in range(2, 6):
            for x in range(1, 4):
                image.putpixel((x, y), color)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    artifact = jobs.sketches.artifacts.create_binary(name=name, data=buffer.getvalue(), mime='image/png')
    info = jobs.sprites.inspect(dict(artifact_id=artifact.slug, version=1))
    return dict(name=name, artifact_id=artifact.slug, version=1, sha256=info['sha256'], approved=True, animation='walk', direction='down'), buffer.getvalue()


def request(frames, **patch):
    return dict(dict(title='Sprites', frames=frames, cell_width=4, cell_height=4, columns=2, padding=1, trim=True, fps=8), **patch)


def queued(jobs, body, request_id='compile'):
    return jobs.submit(dict(operation='sprite_compile', request_id=request_id, input=body))


def run(jobs, job):
    MediaWorker(jobs).run_once(WorkerContext('gideon-media', 'default', WorkerControl()))
    result = jobs.get(job['id'])
    assert result['status'] == 'succeeded', result['error']
    return result


def test_inspection_binds_actual_pixels_and_requires_approval(jobs):
    item, raw = frame(jobs, 'first')
    inspected = jobs.sprites.inspect(dict(artifact_id=item['artifact_id'], version=1))
    assert inspected['width'] == 8
    assert inspected['height'] == 8
    assert inspected['sha256'] == hashlib.sha256(raw).hexdigest()
    assert inspected['approved'] is False
    assert inspected['artifact_id'] == item['artifact_id']
    assert inspected['version'] == 1
    assert 'path' not in inspected
    assert jobs.sketches.artifacts.raw_bytes(item['artifact_id'])[0] == raw


def test_real_worker_compiles_trimmed_alpha_atlas_and_exact_json_layout(jobs):
    first, raw1 = frame(jobs, 'walk-1')
    second, raw2 = frame(jobs, 'walk-2', (10, 220, 30, 255))
    body = request([first, second])
    job = queued(jobs, body)
    assert queued(jobs, body)['id'] == job['id']
    finished = run(jobs, job)
    assert finished['progress'] == 1
    result = finished['result']
    atlas = jobs.sketches.artifacts.get(result['artifact_id'])
    layout = jobs.sketches.artifacts.get(result['manifest_id'])
    assert atlas.kind == 'image'
    assert layout.kind == 'json'
    assert atlas.version == 1
    assert layout.version == 1
    raw, mime = jobs.sketches.artifacts.raw_bytes(atlas.slug)
    assert mime == 'image/png'
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (12, 6)
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((1, 1)) == (200, 20, 40, 150)
        assert image.getpixel((7, 1)) == (10, 220, 30, 255)
        assert image.getpixel((4, 1))[3] == 0
        assert image.getpixel((6, 1))[3] == 0
    manifest = json.loads(layout.content)
    assert jobs.sprites.manifest(result) == manifest
    assert manifest['schema_version'] == 1
    assert manifest['fps'] == 8
    assert manifest['atlas']['width'] == 12
    assert manifest['atlas']['height'] == 6
    assert manifest['atlas']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert manifest['atlas']['artifact_id'] == atlas.slug
    entries = manifest['frames']
    assert [entry['name'] for entry in entries] == ['walk-1', 'walk-2']
    assert entries[0]['frame'] == dict(x=1, y=1, w=3, h=4)
    assert entries[1]['frame'] == dict(x=7, y=1, w=3, h=4)
    assert entries[0]['sourceSize'] == dict(w=8, h=8)
    assert entries[0]['spriteSourceSize'] == dict(x=1, y=2, w=3, h=4)
    assert entries[0]['animation'] == 'walk'
    assert entries[0]['direction'] == 'down'
    assert entries[0]['sha256'] == first['sha256']
    assert entries[0]['artifact_id'] == first['artifact_id']
    metadata = atlas.events[0].metadata
    assert metadata['engine'] == 'Pillow'
    assert metadata['media_job_id'] == job['id']
    assert metadata['sprite_request_sha256'] == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    assert layout.events[0].metadata == metadata
    assert jobs.sketches.artifacts.raw_bytes(first['artifact_id'])[0] == raw1
    assert jobs.sketches.artifacts.raw_bytes(second['artifact_id'])[0] == raw2
    assert jobs.sprites.compile(body, job['id'], lambda: False, lambda value: None) == result
    assert len(jobs.sketches.artifacts.list()) == 4


def test_untrimmed_frame_preserves_full_geometry_and_transparency(jobs):
    item, raw = frame(jobs, 'whole')
    body = request([item], cell_width=8, cell_height=8, columns=1, padding=0, trim=False)
    result = jobs.sprites.compile(body, 'whole', lambda: False, lambda value: None)
    manifest = jobs.sprites.manifest(result)
    entry = manifest['frames'][0]
    assert entry['frame'] == dict(x=0, y=0, w=8, h=8)
    assert entry['spriteSourceSize'] == dict(x=0, y=0, w=8, h=8)
    with Image.open(io.BytesIO(jobs.sketches.artifacts.raw_bytes(result['artifact_id'])[0])) as image:
        assert image.tobytes() == Image.open(io.BytesIO(raw)).tobytes()


def test_fully_transparent_frame_remains_transparent_and_has_valid_rect(jobs):
    item, _ = frame(jobs, 'blank', transparent=True)
    result = jobs.sprites.compile(request([item]), 'blank', lambda: False, lambda value: None)
    manifest = jobs.sprites.manifest(result)
    assert manifest['frames'][0]['frame'] == dict(x=1, y=1, w=1, h=1)
    with Image.open(io.BytesIO(jobs.sketches.artifacts.raw_bytes(result['artifact_id'])[0])) as image:
        assert image.getchannel('A').getbbox() is None


@pytest.mark.parametrize('patch', [
    {'approved': False}, {'approved': 1}, {'sha256': 'a'}, {'sha256': '0'*64},
    {'sha256': []}, {'version': True}, {'version': 99}, {'artifact_id': []},
    {'name': ''}, {'direction': 'a'*65}, {'path': '/tmp/outside'},
])
def test_approval_hash_pin_and_labels_fail_closed(jobs, patch):
    item, _ = frame(jobs, 'sprite')
    with pytest.raises(SketchError):
        queued(jobs, request([dict(item, **patch)]))
    assert jobs.list()['items'] == []
    assert len(jobs.sketches.artifacts.list()) == 1


@pytest.mark.parametrize('patch', [
    {'cell_width': 0}, {'cell_height': 3}, {'columns': 0}, {'padding': 33},
    {'trim': 'true'}, {'fps': True}, {'fps': 61}, {'title': ''},
    {'frames': []}, {'frames': [None]}, {'home': '/tmp/other'},
    {'columns': 64, 'cell_width': 2048},
])
def test_atlas_geometry_and_input_bounds_are_enforced(jobs, patch):
    item, _ = frame(jobs, 'sprite')
    with pytest.raises(SketchError):
        jobs.sprites.prepare_compile(dict(request([item]), **patch))
    assert jobs.list()['items'] == []


def test_duplicate_names_and_mismatched_output_identity_are_rejected(jobs):
    item, _ = frame(jobs, 'sprite')
    with pytest.raises(SketchError, match='unique'):
        jobs.sprites.prepare_compile(request([item, item]))
    first = request([item])
    jobs.sprites.compile(first, 'same', lambda: False, lambda value: None)
    with pytest.raises(SketchError, match='identity conflict'):
        jobs.sprites.compile(dict(first, fps=9), 'same', lambda: False, lambda value: None)
    assert len(jobs.sketches.artifacts.list()) == 3


def test_cancellation_before_compile_leaves_original_and_no_output(jobs):
    item, raw = frame(jobs, 'sprite')
    with pytest.raises(SketchError, match='cancelled'):
        jobs.sprites.compile(request([item]), 'cancel', lambda: True, lambda value: None)
    assert len(jobs.sketches.artifacts.list()) == 1
    assert jobs.sketches.artifacts.raw_bytes(item['artifact_id'])[0] == raw


def test_actual_unconfigured_image_generation_fails_without_approved_frames(jobs):
    body = dict(title='Generate', frames=[dict(name='idle-down', animation='idle', direction='down', prompt='Pixel character facing down')])
    queued_job = jobs.submit(dict(operation='sprite_generate', request_id='generate', input=body))
    assert queued_job['input']['frames'][0]['input']['prompt'] == body['frames'][0]['prompt']
    assert queued_job['input']['frames'][0]['direction'] == 'down'
    MediaWorker(jobs).run_once(WorkerContext('gideon-media', 'default', WorkerControl()))
    failed = jobs.get(queued_job['id'])
    assert failed['status'] == 'failed'
    assert failed['result'] is None
    assert failed['error']
    assert jobs.sprites.frames(queued_job['id'])['items'] == []
    assert jobs.sketches.artifacts.list() == []
    reopened = SpriteService(jobs.sprites.path, jobs.images)
    assert reopened.frames(queued_job['id'])['items'] == []


@pytest.mark.asyncio
async def test_real_http_inspection_native_compile_and_pinned_manifest(jobs):
    app = web.Application()
    register_jobs(app, jobs)
    register_sprites(app)
    item, _ = frame(jobs, 'http')
    async with TestClient(TestServer(app)) as client:
        response = await client.post('/api/capabilities/media/sprites/inspect', json=dict(artifact_id=item['artifact_id'], version=1))
        assert response.status == 200
        inspected = await response.json()
        assert inspected['approved'] is False
        assert inspected['sha256'] == item['sha256']
        tool = MediaToolProvider(jobs.sketches, MediaLibrary(jobs.sketches.artifacts))
        result = await tool.invoke('media_sprite_compile', {'request_id': 'native', 'input': request([item])})
        assert result.success
        job = json.loads(result.output)
        finished = run(jobs, job)
        atlas = await client.get('/api/capabilities/media/jobs/'+job['id']+'/atlas')
        assert atlas.status == 200
        assert (await atlas.json())['atlas']['artifact_id'] == finished['result']['artifact_id']
        incorrect = await client.get('/api/capabilities/media/jobs/'+job['id']+'/frames')
        assert incorrect.status == 400
        denied = await client.post('/api/capabilities/media/sprites/inspect?home=other', json=dict(artifact_id=item['artifact_id'], version=1))
        assert denied.status == 400
        native = await tool.invoke('media_sprite_inspect', {'artifact_id': item['artifact_id'], 'version': 1})
        assert native.success
        assert json.loads(native.output)['approved'] is False
        missing = await client.get('/api/capabilities/media/jobs/missing/atlas')
        assert missing.status == 404
