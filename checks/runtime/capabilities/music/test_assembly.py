import copy
import io
import json
import math
import sqlite3
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.assembly import AssemblyStore,validate
from gideon.workspace.capabilities.music.assembly_geometry import compile_model,mesh,source_module
from gideon.workspace.capabilities.music.assembly_tools import AssemblyTools
from gideon.workspace.capabilities.music.image3d import validate_glb
from gideon.workspace.capabilities.music.store import DomainError
from gideon.interfaces.dashboard.handlers.capabilities_music_assemblies import register


def part(part_id='base',**changes):
    return {'id':part_id,'name':part_id,'shape':'box','size':[2,2,2],'position':[0,0,0],'rotation':[0,0,0],'color':'#5599cc','parent_id':None,'joint':None,**changes}


def spec():
    return {'title':'Articulated assembly','parts':[part(),part('arm',shape='cylinder',size=[.3,2,.3],position=[0,2,0],parent_id='base',joint={'axis':'z','min_degrees':-45,'max_degrees':45})],
            'clips':[{'name':'wave','part_id':'arm','axis':'z','from_degrees':-90,'to_degrees':90,'duration_seconds':2}]}


def store_at(home):
    return AssemblyStore(home,NativeArtifactProvider(root=home/'artifacts'))


def decode(raw):
    assert raw[:4]==b'glTF'
    version,length=struct.unpack_from('<II',raw,4)
    assert version==2 and length==len(raw)
    json_length,json_type=struct.unpack_from('<II',raw,12)
    assert json_type==0x4e4f534a
    document=json.loads(raw[20:20+json_length])
    binary_length,kind=struct.unpack_from('<II',raw,20+json_length)
    assert kind==0x004e4942
    binary=raw[28+json_length:]
    assert len(binary)==binary_length
    return document,binary


def values(document,binary,index):
    accessor=document['accessors'][index];view=document['bufferViews'][accessor['bufferView']]
    start=view['byteOffset'];length=view['byteLength']
    return struct.unpack('<'+'f'*(length//4),binary[start:start+length])


def test_actual_primitive_geometry_bounds_triangles_and_normals():
    for shape,triangles in (('box',12),('sphere',168),('cylinder',48)):
        current=part(shape=shape)
        positions,normals=mesh(current)
        assert len(positions)==triangles*9
        assert len(normals)==len(positions)
        for axis in range(3):
            assert min(positions[axis::3])==pytest.approx(-1)
            assert max(positions[axis::3])==pytest.approx(1)
        for index in range(0,len(normals),3):
            assert sum(value*value for value in normals[index:index+3])==pytest.approx(1)


def test_compiled_glb_has_hierarchy_materials_and_real_rotation_channels():
    raw,diagnostics=compile_model(spec())
    assert validate_glb(raw)=={'meshes':2,'animations':['wave']}
    document,binary=decode(raw)
    assert document['nodes'][0]['children']==[1]
    assert document['scenes'][0]['nodes']==[0]
    assert document['nodes'][1]['translation']==[0,2,0]
    assert document['nodes'][1]['extras']['joint']['axis']=='z'
    assert document['materials'][0]['pbrMetallicRoughness']['baseColorFactor']==pytest.approx([85/255,153/255,204/255,1])
    animation=document['animations'][0]
    assert animation['name']=='wave'
    assert animation['channels'][0]['target']=={'node':1,'path':'rotation'}
    assert values(document,binary,animation['samplers'][0]['input'])==(0,1,2)
    rotations=values(document,binary,animation['samplers'][0]['output'])
    assert rotations[2]==pytest.approx(-math.sqrt(.5))
    assert rotations[6]==pytest.approx(0)
    assert rotations[10]==pytest.approx(math.sqrt(.5))
    assert diagnostics['triangle_count']==60
    assert diagnostics['mesh_count']==2
    assert diagnostics['volume_estimate']==pytest.approx(8+math.pi*.15*.15*2)
    assert diagnostics['bounds']=={'min':[-1,-1,-1],'max':[1,3,1]}
    assert {row['code'] for row in diagnostics['findings']}=={'clip_outside_joint','off_ground'}


def test_bounds_include_actual_parent_rotation_and_translation():
    schema={'title':'Transforms','parts':[part(position=[3,0,0],rotation=[0,0,math.pi/2]),part('child',position=[2,0,0],parent_id='base')],'clips':[]}
    _,diagnostics=compile_model(schema)
    assert diagnostics['bounds']['min']==pytest.approx([2,-1,-1])
    assert diagnostics['bounds']['max']==pytest.approx([4,3,1])
    assert diagnostics['mesh_count']==2


def test_thin_and_overlap_findings_are_explicit_geometric_approximations():
    schema={'title':'Thin overlap','parts':[part(),part('thin',size=[.01,2,2])],'clips':[]}
    _,diagnostics=compile_model(schema)
    findings={row['code']:row for row in diagnostics['findings']}
    assert findings['thin_part']['part_ids']==['thin']
    assert findings['aabb_overlap']['part_ids']==['base','thin']
    assert 'bounding boxes' in findings['aabb_overlap']['message']
    assert diagnostics['volume_estimate']==pytest.approx(8.04)


def test_store_schema_diagnostics_and_revision_history_survive_reopen(tmp_path):
    store=store_at(tmp_path);schema=spec();item=store.create(schema)
    assert item['revision']==1
    assert item['parts']==schema['parts']
    assert item['exports']==[]
    assert store_at(tmp_path).get(item['id'])==item
    assert store.history(item['id'])==[item]
    assert store.list()==[item]
    assert store.list(offset=1)==[]
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==1


def test_refinement_grounds_roots_clamps_clip_limits_and_keeps_original(tmp_path):
    store=store_at(tmp_path);item=store.create(spec())
    refined=store.refine(item['id'],{'revision':1,'operations':['ground','clamp_clips']})
    assert refined['revision']==2
    assert refined['parts'][0]['position']==[0,1,0]
    assert refined['parts'][1]['position']==[0,2,0]
    assert refined['clips'][0]['from_degrees']==-45
    assert refined['clips'][0]['to_degrees']==45
    assert refined['diagnostics']['bounds']['min'][1]==pytest.approx(0)
    assert not refined['diagnostics']['findings']
    assert store.history(item['id'])[0]==item
    assert store.history(item['id'])[1]==refined
    assert store_at(tmp_path).get(item['id'])==refined
    with pytest.raises(DomainError):
        store.refine(item['id'],{'revision':1,'operations':['ground']})


def test_export_receipt_is_durable_and_old_geometry_survives_edit(tmp_path):
    store=store_at(tmp_path);item=store.create(spec())
    result=store.export(item['id'],{'revision':1})
    model=result['model_ref'];source=result['source_ref']
    binary,mime=store.artifacts.raw_bytes(model['slug'],version=model['version'])
    assert mime=='model/gltf-binary'
    assert validate_glb(binary)['animations']==['wave']
    module=store.artifacts.get(source['slug'],version=source['version'])
    assert module.kind=='text'
    assert "import * as THREE from 'three'" in module.content
    assert 'export function createAssembly' in module.content
    assert 'QuaternionKeyframeTrack' in module.content
    assert store_at(tmp_path).export(item['id'],{'revision':1})==result
    assert store.get(item['id'])['exports']==[result]
    changed=store.update(item['id'],{**spec(),'title':'Edited assembly','revision':1})
    assert changed['exports']==[result]
    second=store.export(item['id'],{'revision':2})
    assert second['model_ref']!=model
    assert store.artifacts.raw_bytes(model['slug'],version=1)[0]==binary
    assert store_at(tmp_path).get(item['id'])['exports']==[result,second]


def test_concurrent_export_requests_share_persisted_receipt(tmp_path):
    store=store_at(tmp_path);item=store.create(spec())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:store_at(tmp_path).export(item['id'],{'revision':1}),range(2)))
    assert results[0]==results[1]
    assert store.get(item['id'])['exports']==[results[0]]


@pytest.mark.parametrize('changes',[{'shape':'mesh'},{'size':[0,1,1]},{'size':[1,2]},{'size':[float('nan'),1,1]},{'position':[0,float('inf'),0]},{'rotation':[0,0,10]},{'color':'javascript:alert(1)'},{'id':'bad.name'},{'parent_id':'missing'},{'parent_id':'base'},{'joint':{'axis':'x','min_degrees':30,'max_degrees':-30}},{'path':'/tmp'}])
def test_invalid_parts_refused_without_persistence(tmp_path,changes):
    store=store_at(tmp_path);schema={'title':'Bad part','parts':[part(**changes)],'clips':[]}
    with pytest.raises(DomainError):store.create(schema)
    assert store.list()==[]


def test_cycles_duplicate_ids_and_clips_without_joint_refused(tmp_path):
    store=store_at(tmp_path)
    schemas=[{'title':'Cycle','parts':[part(parent_id='two'),part('two',parent_id='base')],'clips':[]},
             {'title':'Duplicate','parts':[part(),part()],'clips':[]},
             {**spec(),'clips':[spec()['clips'][0]]*2}]
    no_joint=spec();no_joint['parts'][1]['joint']=None;schemas.append(no_joint)
    wrong_axis=spec();wrong_axis['clips'][0]['axis']='x';schemas.append(wrong_axis)
    for schema in schemas:
        with pytest.raises(DomainError):store.create(schema)
    assert store.list()==[]


def test_untrusted_model_paths_and_unknown_refinements_refused(tmp_path):
    store=store_at(tmp_path);item=store.create(spec())
    with pytest.raises(DomainError):store.create({**spec(),'home':'/tmp'})
    for data in ({'revision':1,'operations':[]},{'revision':1,'operations':['run_script']},{'revision':1,'operations':['ground'],'provider':'remote'}):
        with pytest.raises(DomainError):store.refine(item['id'],data)
    assert store.get(item['id'])==item
    assert len(store.history(item['id']))==1


def test_foreign_home_cannot_read_refine_or_export(tmp_path):
    one,two=store_at(tmp_path/'one'),store_at(tmp_path/'two');item=one.create(spec())
    for action in (lambda:two.get(item['id']),lambda:two.refine(item['id'],{'revision':1,'operations':['ground']}),lambda:two.export(item['id'],{'revision':1})):
        with pytest.raises(DomainError) as err:action()
        assert err.value.status==404
    assert two.list()==[]
    assert one.get(item['id'])==item


@pytest.mark.asyncio
async def test_http_real_author_refine_history_export_and_pinned_source(tmp_path):
    store=store_at(tmp_path);app=web.Application();register(app,store)
    prefix='/api/capabilities/music/assemblies'
    async with TestClient(TestServer(app)) as client:
        response=await client.post(prefix,json=spec());assert response.status==201
        item=(await response.json())['item']
        refined=await client.post(prefix+'/'+item['id']+'/refine',json={'revision':1,'operations':['ground','clamp_clips']})
        assert refined.status==200
        assert (await refined.json())['item']['revision']==2
        exported=await client.post(prefix+'/'+item['id']+'/export',json={'revision':2})
        assert exported.status==200
        ref=(await exported.json())['source_ref']
        source=await client.get(prefix+f"/artifacts/{ref['slug']}/{ref['version']}/source")
        assert source.status==200
        assert 'createAssembly' in await source.text()
        history=await client.get(prefix+'/'+item['id']+'/history')
        assert [row['revision'] for row in (await history.json())['items']]==[1,2]
        stale=await client.patch(prefix+'/'+item['id'],json={**spec(),'revision':1})
        assert stale.status==409
        listed=await client.get(prefix)
        assert len((await listed.json())['items'])==1
    assert len(store_at(tmp_path).get(item['id'])['exports'])==1


@pytest.mark.asyncio
async def test_native_all_operations_use_real_assembly_store(tmp_path):
    store=store_at(tmp_path);tools=AssemblyTools(store)
    assert len(await tools.list_tools())==7
    result=await tools.invoke('music_assemblies_create',{'data':spec()});assert result.success
    item=json.loads(result.output)
    fetched=await tools.invoke('music_assemblies_get',{'id':item['id']});assert json.loads(fetched.output)==item
    updated=await tools.invoke('music_assemblies_update',{'id':item['id'],'data':{**spec(),'title':'Native updated','revision':1}});assert updated.success
    refined=await tools.invoke('music_assemblies_refine',{'id':item['id'],'data':{'revision':2,'operations':['ground']}});assert refined.success
    exported=await tools.invoke('music_assemblies_export',{'id':item['id'],'data':{'revision':3}});assert exported.success
    history=await tools.invoke('music_assemblies_history',{'id':item['id']});assert len(json.loads(history.output))==3
    listing=await tools.invoke('music_assemblies_list',{});assert len(json.loads(listing.output))==1
    denied=await tools.invoke('music_assemblies_get',{'id':item['id'],'home':'/tmp'});assert not denied.success
    assert store_at(tmp_path).get(item['id'])['title']=='Native updated'
