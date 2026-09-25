import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'
import { beforeAll,expect,it } from 'vitest'
import * as THREE from 'three'
import { loadGlb } from './glb'
let bytes:ArrayBuffer,source:string
beforeAll(()=>{
 const root=resolve(process.cwd(),'../..')
 const script="import json,base64;from test_assembly import spec;from gideon.workspace.capabilities.music.assembly_geometry import compile_model,source_module;data=spec();data['clips'][0].update(from_degrees=0,to_degrees=360);raw,_=compile_model(data);print(json.dumps({'glb':base64.b64encode(raw).decode(),'source':source_module(data)}))"
 const compiled=spawnSync(globalThis.process.env.GIDEON_TEST_PYTHON||'python3',['-c',script],{cwd:root,env:{...globalThis.process.env,PYTHONPATH:resolve(root,'runtime')+':'+resolve(root,'checks/runtime/capabilities/music')},encoding:'utf8'})
 if(compiled.status!==0)throw new Error(compiled.stderr)
 const result=JSON.parse(compiled.stdout)
 const buffer=Buffer.from(result.glb,'base64');const browserBytes=new Uint8Array(buffer.length);browserBytes.set(buffer);bytes=browserBytes.buffer
 source=result.source
})
it('loads actually compiled primitive GLB hierarchy, materials and complete rotation samples',async()=>{
 const result=await loadGlb(bytes)
 expect(result.clips).toEqual(['wave'])
 const base=result.gltf.scene.getObjectByName('base') as THREE.Mesh
 const arm=result.gltf.scene.getObjectByName('arm') as THREE.Mesh
 expect(base.isMesh).toBe(true)
 expect(arm.parent).toBe(base)
 expect(base.geometry.getAttribute('position').count).toBe(36)
 expect(arm.geometry.getAttribute('position').count).toBe(144)
 expect(arm.position.toArray()).toEqual([0,2,0])
 const material=base.material as THREE.MeshStandardMaterial
 expect(material.roughness).toBe(.7)
 expect(material.metalness).toBe(0)
 expect(result.gltf.animations[0].tracks[0].times).toHaveLength(5)
 const mixer=new THREE.AnimationMixer(result.gltf.scene)
 mixer.clipAction(result.gltf.animations[0]).setLoop(THREE.LoopOnce,1).play()
 mixer.setTime(.5)
 const direction=new THREE.Vector3(1,0,0).applyQuaternion(arm.quaternion)
 expect(direction.x).toBeCloseTo(0)
 expect(direction.y).toBeCloseTo(1)
 mixer.setTime(1)
 expect(new THREE.Vector3(1,0,0).applyQuaternion(arm.quaternion).x).toBeCloseTo(-1)
 mixer.setTime(1.5)
 expect(new THREE.Vector3(1,0,0).applyQuaternion(arm.quaternion).y).toBeCloseTo(-1)
 mixer.stopAllAction()
})
it('executes the exported module with real Three and preserves authored hierarchy and motion',()=>{
 const runnable=source.replace("import * as THREE from 'three';",'').replace('export function createAssembly','function createAssembly')+'\nreturn createAssembly();'
 const assembly=new Function('THREE',runnable)(THREE) as {scene:THREE.Group;clips:THREE.AnimationClip[]}
 expect(assembly.scene.isGroup).toBe(true)
 const base=assembly.scene.getObjectByName('base') as THREE.Mesh
 const arm=assembly.scene.getObjectByName('arm') as THREE.Mesh
 expect(base.geometry.type).toBe('BoxGeometry')
 expect(arm.geometry.type).toBe('CylinderGeometry')
 expect(arm.parent).toBe(base)
 expect(assembly.clips.map(clip=>clip.name)).toEqual(['wave'])
 expect(assembly.clips[0].tracks[0].times).toHaveLength(5)
 const mixer=new THREE.AnimationMixer(assembly.scene)
 mixer.clipAction(assembly.clips[0]).play()
 mixer.setTime(.5)
 const direction=new THREE.Vector3(1,0,0).applyQuaternion(arm.quaternion)
 expect(direction.x).toBeCloseTo(0)
 expect(direction.y).toBeCloseTo(1)
 mixer.stopAllAction()
})
