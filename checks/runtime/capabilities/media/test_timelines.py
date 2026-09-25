import copy
import io
import json
import math
import os
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

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
from gideon.workspace.capabilities.media.timelines import TimelineStore, render_process
from gideon.workspace.capabilities.media.timelines_http import register_timelines
from gideon.workspace.capabilities.media.tools import MediaToolProvider
from gideon.workspace.capabilities.media.videos import probe_video


@pytest.fixture
def jobs(tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / 'artifacts')
    return MediaJobs(tmp_path / 'jobs.sqlite3', SketchStore(tmp_path / 'sketches.sqlite3', artifacts))


def image(jobs, color):
    raw = io.BytesIO()
    Image.new('RGB', (32, 24), color).save(raw, 'PNG')
    return jobs.sketches.artifacts.create_binary(name=color, data=raw.getvalue(), mime='image/png')


def project(jobs, colors=('red', 'blue')):
    return dict(title='Sequence', width=32, height=24, fps=4, request_id='save', segments=[dict(artifact_id=image(jobs, color).slug, version=1, kind='image', start=0, duration=1) for color in colors], overlays=[], audio=[])


def render(jobs, document, request_id='render'):
    job = jobs.submit(dict(operation='timeline_render', request_id=request_id, input=dict(timeline_id=document['id'], revision=document['revision'])))
    MediaWorker(jobs).run_once(WorkerContext('gideon-media', 'default', WorkerControl()))
    return jobs.get(job['id'])


def output(jobs, job, tmp_path):
    assert job['status'] == 'succeeded', job['error']
    data, mime = jobs.sketches.artifacts.raw_bytes(job['result']['artifact_id'])
    assert mime == 'video/mp4'
    path = tmp_path / 'rendered.mp4'
    path.write_bytes(data)
    return path


def pixel(path, second, x=0, y=0):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(second), '-i', str(path), '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True, timeout=15)
    with Image.open(io.BytesIO(result.stdout)) as frame:
        return frame.convert('RGB').getpixel((x, y))


def test_project_revisions_cas_history_reopen_and_replay(jobs):
    body = project(jobs)
    first = jobs.timelines.save(body)
    assert first['revision'] == 1
    assert first['duration'] == 2
    assert first['updated_at'].endswith('+00:00')
    assert jobs.timelines.save(body) == first
    changed = dict(body, title='Reordered', segments=list(reversed(body['segments'])), revision=1, request_id='second')
    second = jobs.timelines.save(changed, first['id'])
    assert second['revision'] == 2
    assert second['segments'][0] == first['segments'][1]
    assert jobs.timelines.get(first['id'], 1) == first
    assert jobs.timelines.get(first['id']) == second
    assert jobs.timelines.list()['items'] == [second]
    assert jobs.timelines.history(first['id'])['items'] == [second, first]
    reopened = TimelineStore(jobs.timelines.path, jobs.videos)
    assert reopened.get(first['id'], 1) == first
    with pytest.raises(SketchError, match='revision changed'):
        jobs.timelines.save(dict(changed, request_id='stale'), first['id'])
    with pytest.raises(SketchError, match='request ID conflict'):
        jobs.timelines.save(dict(body, title='Conflicting'))
    assert len(jobs.timelines.history(first['id'])['items']) == 2


def test_real_worker_renders_order_timing_progress_and_canonical_provenance(jobs, tmp_path):
    body = project(jobs)
    originals = {entry['artifact_id']: jobs.sketches.artifacts.raw_bytes(entry['artifact_id'])[0] for entry in body['segments']}
    document = jobs.timelines.save(body)
    job = render(jobs, document)
    path = output(jobs, job, tmp_path)
    assert job['progress'] == 1
    assert job['attempt'] == 1
    assert 'child_pid' not in job
    assert job['result']['width'] == 32
    assert job['result']['height'] == 24
    assert abs(job['result']['duration_seconds']-2) < .1
    assert pixel(path, .25)[0] > 240
    assert pixel(path, 1.25)[2] > 240
    artifact = jobs.sketches.artifacts.get(job['result']['artifact_id'])
    metadata = artifact.events[0].metadata
    assert metadata['engine'] == 'FFmpeg'
    assert metadata['timeline_id'] == document['id']
    assert metadata['timeline_revision'] == 1
    assert metadata['media_job_id'] == job['id']
    for artifact_id, raw in originals.items():
        assert jobs.sketches.artifacts.raw_bytes(artifact_id)[0] == raw
        assert jobs.sketches.artifacts.get(artifact_id).version == 1
    assert jobs.timelines.get(document['id']) == document


def test_overlay_actual_position_and_timed_visibility(jobs, tmp_path):
    body = project(jobs, ('blue', 'blue'))
    overlay = image(jobs, 'red')
    body['overlays'] = [dict(artifact_id=overlay.slug, version=1, start=1, duration=1, x=4, y=4, width=8, height=8)]
    document = jobs.timelines.save(body)
    path = output(jobs, render(jobs, document), tmp_path)
    assert pixel(path, .25, 6, 6)[2] > 240
    assert pixel(path, 1.25, 6, 6)[0] > 230
    assert pixel(path, 1.25, 20, 20)[2] > 230
    assert jobs.sketches.artifacts.get(overlay.slug).version == 1


def test_video_trim_uses_requested_source_interval(jobs, tmp_path):
    first = jobs.timelines.save(project(jobs))
    rendered = render(jobs, first)
    source_id = rendered['result']['artifact_id']
    body = dict(title='Trimmed', width=32, height=24, fps=4, request_id='trim', segments=[dict(artifact_id=source_id, version=1, kind='video', start=1, duration=.5)], overlays=[], audio=[])
    document = jobs.timelines.save(body)
    path = output(jobs, render(jobs, document, 'trim-render'), tmp_path)
    assert pixel(path, .1)[2] > 230
    assert abs(probe_video(path)['duration_seconds']-.5) < .1
    assert jobs.timelines.get(first['id'])['duration'] == 2


def tone(jobs):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b''.join(struct.pack('<h', round(16000*math.sin(2*math.pi*440*i/48000))) for i in range(48000)))
    return jobs.sketches.artifacts.create_binary(name='Tone', data=buffer.getvalue(), mime='audio/wav', kind='audio')


def test_explicit_audio_placement_volume_fades_and_silence(jobs, tmp_path):
    audio = tone(jobs)
    body = project(jobs)
    body['audio'] = [dict(artifact_id=audio.slug, version=1, start=.5, trim=0, duration=1, volume=.5, fade_in=.25, fade_out=.25)]
    path = output(jobs, render(jobs, jobs.timelines.save(body)), tmp_path)
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 's16le', '-ac', '1', '-ar', '48000', '-'], capture_output=True, check=True, timeout=15)
    samples = struct.unpack('<'+'h'*(len(decoded.stdout)//2), decoded.stdout)
    def rms(start, end):
        window = samples[int(start*48000):int(end*48000)]
        return math.sqrt(sum(value*value for value in window)/len(window))
    assert rms(.05, .3) < 20
    assert 3800 < rms(.85, 1.1) < 4200
    assert rms(.52, .58) < rms(.85, 1.1)*.5
    assert rms(1.42, 1.48) < rms(.85, 1.1)*.5
    assert rms(1.7, 1.9) < 20
    assert jobs.sketches.artifacts.get(audio.slug).version == 1


@pytest.mark.parametrize('change', [
    {'width': 3}, {'height': 1922}, {'fps': True}, {'fps': 61}, {'title': ''},
    {'segments': []}, {'segments': [None]}, {'overlays': 'bad'}, {'audio': {}},
    {'provider': 'other'}, {'request_id': []}, {'revision': True},
])
def test_invalid_project_does_not_persist(jobs, change):
    body = project(jobs, ('red',))
    with pytest.raises(SketchError):
        jobs.timelines.save(dict(body, **change))
    assert jobs.timelines.list()['items'] == []


def test_invalid_trims_placements_and_paths_are_rejected(jobs):
    body = project(jobs, ('red',))
    cases = []
    for patch in ({'kind': 'audio'}, {'start': 1}, {'duration': float('nan')}, {'duration': 301}, {'version': True}, {'artifact_id': []}, {'path': '/tmp/a'}):
        changed = copy.deepcopy(body)
        changed['segments'][0].update(patch)
        cases.append(changed)
    for patch in ({'x': -1}, {'width': 40}, {'start': 2}, {'duration': -1}):
        changed = copy.deepcopy(body)
        changed['overlays'] = [dict(artifact_id=body['segments'][0]['artifact_id'], version=1, start=0, duration=1, x=0, y=0, width=8, height=8)]
        changed['overlays'][0].update(patch)
        cases.append(changed)
    for changed in cases:
        with pytest.raises(SketchError):
            jobs.timelines.save(changed)
    assert jobs.timelines.list()['items'] == []


def test_audio_bounds_and_wrong_kinds_fail_before_render(jobs):
    body = project(jobs, ('red',))
    audio = tone(jobs)
    track = dict(artifact_id=audio.slug, version=1, start=0, trim=0, duration=1, volume=1, fade_in=0, fade_out=0)
    for patch in ({'trim': .5}, {'duration': 2}, {'volume': 3}, {'fade_in': 2}, {'start': 1}, {'version': 99}, {'artifact_id': body['segments'][0]['artifact_id']}):
        with pytest.raises(SketchError):
            jobs.timelines.save(dict(body, audio=[dict(track, **patch)]))
    assert jobs.timelines.list()['items'] == []


def test_uploaded_playlist_is_rejected_without_following_references(jobs, tmp_path):
    raw = b'#EXTM3U\nhttp://127.0.0.1:9/private\n'
    artifact = jobs.sketches.artifacts.create_binary(name='Mislabelled', data=raw, mime='audio/wav', kind='audio')
    body = project(jobs, ('red',))
    body['audio'] = [dict(artifact_id=artifact.slug, version=1, start=0, trim=0, duration=1, volume=1, fade_in=0, fade_out=0)]
    with pytest.raises(SketchError, match='container'):
        jobs.timelines.save(body)
    path = tmp_path / 'playlist.mp4'
    path.write_bytes(raw)
    with pytest.raises(SketchError, match='container'):
        probe_video(path)
    assert jobs.timelines.list()['items'] == []


def test_real_process_cancellation_and_error_are_observed(tmp_path):
    pids = []
    start = time.monotonic()
    with pytest.raises(SketchError, match='interrupted'):
        render_process([sys.executable, '-c', 'import time; time.sleep(20)'], tmp_path / 'log', lambda: True, pids.append)
    assert time.monotonic()-start < 5
    assert len(pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)
    with pytest.raises(SketchError, match='failed'):
        render_process([sys.executable, '-c', 'raise SystemExit(2)'], tmp_path / 'log', lambda: False, pids.append)


@pytest.mark.asyncio
async def test_real_http_tools_share_immutable_projects_and_jobs(jobs):
    app = web.Application()
    register_jobs(app, jobs)
    register_timelines(app)
    body = project(jobs, ('red',))
    async with TestClient(TestServer(app)) as client:
        created = await client.post('/api/capabilities/media/timelines', json=body)
        assert created.status == 201
        document = await created.json()
        tool = MediaToolProvider(jobs.sketches, MediaLibrary(jobs.sketches.artifacts))
        read = await tool.invoke('media_timelines_get', {'timeline_id': document['id']})
        assert read.success
        assert json.loads(read.output) == document
        history = await tool.invoke('media_timelines_history', {'timeline_id': document['id']})
        assert json.loads(history.output)['items'] == [document]
        updated = await client.put('/api/capabilities/media/timelines/'+document['id'], json=dict(body, request_id='update', revision=1, title='Updated'))
        assert updated.status == 200
        assert (await updated.json())['revision'] == 2
        stale = await client.put('/api/capabilities/media/timelines/'+document['id'], json=dict(body, request_id='stale', revision=1))
        assert stale.status == 409
        queued = await tool.invoke('media_timeline_render', {'request_id': 'render', 'input': {'timeline_id': document['id'], 'revision': 1}})
        assert queued.success
        assert jobs.get(json.loads(queued.output)['id'])['input']['revision'] == 1
        denied = await client.get('/api/capabilities/media/timelines?home=other')
        assert denied.status == 400
        assert (await client.get('/api/capabilities/media/timelines/missing')).status == 404
        response = await client.get('/api/capabilities/media/timelines/'+document['id']+'/history')
        assert [row['revision'] for row in (await response.json())['items']] == [2, 1]


def test_concurrent_real_connections_allow_only_one_revision_update(jobs):
    from concurrent.futures import ThreadPoolExecutor
    body = project(jobs, ('red',))
    first = jobs.timelines.save(body)
    stores = [TimelineStore(jobs.timelines.path, jobs.videos), TimelineStore(jobs.timelines.path, jobs.videos)]
    def save(index):
        try:
            return stores[index].save(dict(body, revision=1, request_id='race-'+str(index), title='Writer '+str(index)), first['id'])['revision']
        except SketchError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, (0, 1)))
    assert sorted(results) == [2, 409]
    assert len(jobs.timelines.history(first['id'])['items']) == 2
    assert jobs.timelines.get(first['id'], 1) == first


def test_saved_old_revision_render_survives_later_reordering(jobs, tmp_path):
    body = project(jobs)
    first = jobs.timelines.save(body)
    second = jobs.timelines.save(dict(body, revision=1, request_id='newer', segments=list(reversed(body['segments']))), first['id'])
    assert second['revision'] == 2
    job = render(jobs, first)
    path = output(jobs, job, tmp_path)
    assert pixel(path, .25)[0] > 230
    assert pixel(path, 1.25)[2] > 230
    metadata = jobs.sketches.artifacts.get(job['result']['artifact_id']).events[0].metadata
    assert metadata['timeline_revision'] == 1
    assert jobs.timelines.get(first['id'])['revision'] == 2


def test_queued_cancel_never_publishes_rendered_artifact(jobs):
    document = jobs.timelines.save(project(jobs, ('red',)))
    queued = jobs.submit(dict(operation='timeline_render', request_id='cancel', input=dict(timeline_id=document['id'], revision=1)))
    cancelled = jobs.cancel(queued['id'], {'state_revision': queued['state_revision']})
    assert cancelled['status'] == 'cancelled'
    MediaWorker(jobs).run_once(WorkerContext('gideon-media', 'default', WorkerControl()))
    assert jobs.get(queued['id'])['result'] is None
    assert len(jobs.sketches.artifacts.list()) == 1
    assert jobs.timelines.get(document['id'])['revision'] == 1


def test_real_render_scales_and_letterboxes_without_distortion(jobs, tmp_path):
    body = project(jobs, ('red',))
    body.update(width=32, height=32)
    path = output(jobs, render(jobs, jobs.timelines.save(body)), tmp_path)
    assert probe_video(path)['height'] == 32
    assert max(pixel(path, .25, 16, 1)) < 10
    assert pixel(path, .25, 16, 16)[0] > 230
    assert max(pixel(path, .25, 16, 30)) < 10
