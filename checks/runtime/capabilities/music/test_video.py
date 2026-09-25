import asyncio
import io
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.capabilities.music.video import VideoStore
from gideon.workspace.capabilities.music.video_tools import VideoTools
from gideon.interfaces.dashboard.handlers.capabilities_music_video import register
from gideon.workspace.capabilities.music.store import DomainError
from test_midi import seeded as audio_seeded, recording
from test_catalog import catalog_at


def store_at(home):
    return VideoStore(home/'music',catalog_at(home))


def seeded(home):
    audio,data=audio_seeded(home,recording(((69,2),)))
    refs=[]
    for color in ('red','blue'):
        out=io.BytesIO();Image.new('RGB',(96,64),color).save(out,format='PNG')
        artifact=audio.catalog.artifacts.create_binary(name=color+' scene',data=out.getvalue(),mime='image/png',kind='image',source='manual')
        refs.append({'slug':artifact.slug,'version':artifact.version})
    payload={'title':'Beat-cut video','track_id':data['track_id'],'render_id':data['render_id'],'tempo_bpm':120,'offset_seconds':.25,
             'scenes':[{'id':'red','image_ref':refs[0],'beats':1},{'id':'blue','image_ref':refs[1],'beats':1}]}
    return store_at(home),payload


def edit(item,**changes):
    return {key:item[key] for key in ('title','track_id','render_id','tempo_bpm','offset_seconds','revision')}|{'scenes':[{key:row[key] for key in ('id','image_ref','beats')} for row in item['scenes']]}|changes


def render_request(item,request_id='render-one'):
    return {'request_id':request_id,'project_id':item['id'],'revision':item['revision']}


async def finish(store,job):
    task=store.tasks.get(job['id'])
    if task:
        await asyncio.wait_for(task,30)
    return store.get_job(job['id'])


def test_project_quantizes_authored_beats_and_preserves_canonical_refs(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    assert item['revision']==1
    assert item['duration_seconds']==1
    assert item['offset_seconds']==.25
    assert [row['start_seconds'] for row in item['scenes']]==[0,.5]
    assert [row['duration_seconds'] for row in item['scenes']]==[.5,.5]
    assert [row['image_ref'] for row in item['scenes']]==[row['image_ref'] for row in payload['scenes']]
    assert item['audio_ref']['version']==1
    assert store_at(tmp_path).get(item['id'])==item
    assert store.list()==[item]
    assert store.list(offset=1)==[]
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==1


def test_edit_retimes_scenes_and_refuses_stale_revision(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    updated=store.update(item['id'],edit(item,tempo_bpm=100))
    assert updated['revision']==2
    assert updated['duration_seconds']==pytest.approx(1.2)
    assert updated['scenes'][1]['start_seconds']==pytest.approx(.6)
    with pytest.raises(DomainError) as err:
        store.update(item['id'],edit(item,title='Stale'))
    assert err.value.code=='revision_conflict'
    assert store.get(item['id'])==updated


@pytest.mark.parametrize('changes',[{'tempo_bpm':19},{'tempo_bpm':301},{'tempo_bpm':True},{'offset_seconds':-1},{'offset_seconds':float('inf')},{'offset_seconds':1.5},{'scenes':[]},{'title':''},{'render_id':'foreign'},{'home':'/tmp'},{'engine':'other'}])
def test_invalid_projects_do_not_persist(tmp_path,changes):
    store,payload=seeded(tmp_path)
    with pytest.raises(DomainError):
        store.create({**payload,**changes})
    assert store.list()==[]
    assert store.jobs()==[]


@pytest.mark.parametrize('changes',[{'beats':0},{'beats':65},{'beats':True},{'id':''},{'image_ref':{'slug':'missing','version':1}},{'path':'/tmp/image.png'}])
def test_invalid_scene_refs_or_timing_refused(tmp_path,changes):
    store,payload=seeded(tmp_path)
    payload['scenes'][0].update(changes)
    with pytest.raises(DomainError):
        store.create(payload)
    assert store.list()==[]


def test_duplicate_scenes_and_foreign_home_audio_refused(tmp_path):
    store,payload=seeded(tmp_path/'one')
    other=store_at(tmp_path/'two')
    with pytest.raises(DomainError):
        other.create(payload)
    with pytest.raises(DomainError):
        store.create({**payload,'scenes':[payload['scenes'][0]]*2})
    assert store.list()==[]
    assert other.list()==[]


@pytest.mark.asyncio
async def test_actual_ffmpeg_render_has_audio_video_and_correct_scene_colors(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    job=await store.submit(render_request(item))
    assert job['status']=='queued'
    result=await finish(store,job)
    assert result['status']=='completed',result
    assert result['error'] is None
    assert result['snapshot']==item
    ref=result['artifact_ref']
    raw,mime=store.catalog.artifacts.raw_bytes(ref['slug'],version=ref['version'])
    assert mime=='video/mp4'
    assert len(raw)>1000
    video=tmp_path/'render.mp4';video.write_bytes(raw)
    probe=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)],capture_output=True,check=True)
    metadata=json.loads(probe.stdout)
    assert {row['codec_type'] for row in metadata['streams']}=={'audio','video'}
    visual=next(row for row in metadata['streams'] if row['codec_type']=='video')
    assert visual['width']==640 and visual['height']==360
    assert visual['r_frame_rate']=='30/1'
    assert float(metadata['format']['duration'])==pytest.approx(1,abs=.08)
    for timestamp,color in ((.2,0),(.8,2)):
        frame=subprocess.run(['ffmpeg','-v','error','-threads','1','-ss',str(timestamp),'-i',str(video),'-frames:v','1','-f','image2pipe','-vcodec','png','-threads','1','-'],capture_output=True,check=True)
        with Image.open(io.BytesIO(frame.stdout)) as image:
            pixel=image.convert('RGB').getpixel((320,180))
        assert pixel[color]>230
        assert max(value for index,value in enumerate(pixel) if index!=color)<25
    audio=subprocess.run(['ffmpeg','-v','error','-i',str(video),'-map','0:a:0','-f','s16le','-acodec','pcm_s16le','-'],capture_output=True,check=True)
    assert any(audio.stdout)
    assert not list(store.root.glob('render-*'))
    assert store_at(tmp_path).get_job(job['id'])==result
    assert await store.submit(render_request(item))==result


@pytest.mark.asyncio
async def test_render_snapshot_survives_edit_and_request_conflict(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    job=await store.submit(render_request(item))
    changed=store.update(item['id'],edit(item,title='After dispatch'))
    assert changed['revision']==2
    with pytest.raises(DomainError) as err:
        await store.submit(render_request(changed))
    assert err.value.code=='request_conflict'
    result=await finish(store,job)
    assert result['snapshot']['title']=='Beat-cut video'
    assert result['revision']==1
    assert result['status']=='completed'
    assert store.get(item['id'])['title']=='After dispatch'


@pytest.mark.asyncio
async def test_cancel_before_start_persists_and_does_not_fabricate_artifact(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    job=await store.submit(render_request(item))
    cancelled=await store.cancel(job['id'])
    assert cancelled['status']=='cancelled'
    assert cancelled['artifact_ref'] is None
    assert store_at(tmp_path).get_job(job['id'])==cancelled
    assert await store.cancel(job['id'])==cancelled
    assert await store.submit(render_request(item))==cancelled


@pytest.mark.asyncio
async def test_http_and_native_share_owned_process_cancellation(tmp_path):
    store,payload=seeded(tmp_path)
    tools=VideoTools(store)
    app=web.Application();register(app,store)
    async with TestClient(TestServer(app)) as client:
        created=await client.post('/api/capabilities/music/videos',json=payload)
        item=(await created.json())['item']
        response=await client.post('/api/capabilities/music/videos/jobs',json=render_request(item))
        assert response.status==202
        job=(await response.json())['job']
        result=await tools.invoke('music_video_cancel',{'id':job['id']})
        assert result.success
        finished=json.loads(result.output)
        assert finished['status'] in ('cancelled','completed')
        fetched=await client.get('/api/capabilities/music/videos/jobs/'+job['id'])
        assert (await fetched.json())['job']==finished
        assert job['id'] not in store.tasks
        bad=await client.post('/api/capabilities/music/videos/jobs/'+job['id']+'/cancel',json={'home':'/tmp'})
        assert bad.status==400
    assert not list(store.root.glob('render-*'))


@pytest.mark.asyncio
async def test_render_failure_from_deleted_source_is_persisted(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    ref=item['scenes'][0]['image_ref']
    assert store.catalog.artifacts.delete(ref['slug'])
    job=await store.submit(render_request(item))
    result=await finish(store,job)
    assert result['status']=='failed'
    assert result['artifact_ref'] is None
    assert 'image' in result['error'].lower()
    assert await store.submit(render_request(item))==result
    assert not list(store.root.glob('render-*'))


def test_explicit_restart_recovery_only_interrupts_unfinished_jobs(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    job={'id':'crash','status':'running','project_id':item['id'],'revision':1,'snapshot':item,'artifact_ref':None,'error':None}
    with sqlite3.connect(store.path) as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?)',('crash','{}',json.dumps(job)))
    reopened=store_at(tmp_path)
    assert reopened.get_job('crash')['status']=='running'
    reopened.recover()
    interrupted=reopened.get_job('crash')
    assert interrupted['status']=='interrupted'
    assert interrupted['artifact_ref'] is None
    reopened.recover()
    assert reopened.get_job('crash')==interrupted


@pytest.mark.asyncio
async def test_native_real_create_get_update_list_job_and_invalid_arguments(tmp_path):
    store,payload=seeded(tmp_path)
    tools=VideoTools(store)
    assert len(await tools.list_tools())==8
    result=await tools.invoke('music_video_create',{'data':payload})
    assert result.success
    item=json.loads(result.output)
    fetched=await tools.invoke('music_video_get',{'id':item['id']})
    assert json.loads(fetched.output)==item
    changed=await tools.invoke('music_video_update',{'id':item['id'],'data':edit(item,title='Native video')})
    assert changed.success
    current=json.loads(changed.output)
    listed=await tools.invoke('music_video_list',{})
    assert json.loads(listed.output)==[current]
    rendered=await tools.invoke('music_video_render',{'data':render_request(current)})
    assert rendered.success
    job=json.loads(rendered.output)
    await store.cancel(job['id'])
    found=await tools.invoke('music_video_job',{'id':job['id']})
    assert json.loads(found.output)['status']=='cancelled'
    jobs=await tools.invoke('music_video_jobs',{})
    assert len(json.loads(jobs.output))==1
    denied=await tools.invoke('music_video_render',{'data':render_request(current),'home':'/tmp'})
    assert not denied.success
    assert store_at(tmp_path).get(current['id'])['title']=='Native video'


@pytest.mark.asyncio
async def test_capacity_limit_and_wrong_owner_cannot_interrupt_active_job(tmp_path):
    store,payload=seeded(tmp_path)
    item=store.create(payload)
    one=await store.submit(render_request(item,'one'))
    two=await store.submit(render_request(item,'two'))
    with pytest.raises(DomainError) as err:
        await store.submit(render_request(item,'three'))
    assert err.value.status==429
    other=store_at(tmp_path)
    with pytest.raises(DomainError) as err:
        await other.cancel(one['id'])
    assert err.value.code=='render_owner_unavailable'
    assert store.get_job(one['id'])['status']=='queued'
    await store.close()
    assert store.get_job(one['id'])['status']=='cancelled'
    assert store.get_job(two['id'])['status']=='cancelled'
    assert len(store.jobs())==2


def test_default_native_factory_shares_captured_home_service(tmp_path):
    import os
    import sys
    script="""
from gideon.workspace.capabilities.music.video import default_store
from gideon.workspace.capabilities.music.video_tools import create_provider
one=default_store()
two=create_provider(home='/untrusted', model='untrusted').store
assert one is two
assert str(one.root).startswith(__import__('os').environ['GIDEON_HOME'])
assert one.tasks == {}
print(one.path)
"""
    response=subprocess.run([sys.executable,'-c',script],env={**os.environ,'GIDEON_HOME':str(tmp_path/'native-home')},capture_output=True,text=True,check=True)
    assert 'native-home' in response.stdout
    assert 'music_video.sqlite3' in response.stdout
