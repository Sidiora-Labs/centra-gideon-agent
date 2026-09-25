import { describe,expect,it } from 'vitest'
import { Mesh,Vector3 } from 'three'
import { loadGlb } from './glb'
function triangle(options:{translation?:number[];external?:boolean;animation?:boolean}={}){
 const data=new Float32Array([0,0,0,1,0,0,0,1,0,0,1,0,0,0,0,0,1])
 const document={asset:{version:'2.0'},scene:0,scenes:[{nodes:[0]}],nodes:[{mesh:0,translation:options.translation||[0,0,0]}],
  meshes:[{primitives:[{attributes:{POSITION:0}}]}],buffers:[{byteLength:data.byteLength,...(options.external?{uri:'https://external.invalid/mesh.bin'}:{})}],
  bufferViews:[{buffer:0,byteOffset:0,byteLength:36},{buffer:0,byteOffset:36,byteLength:8},{buffer:0,byteOffset:44,byteLength:24}],
  accessors:[{bufferView:0,componentType:5126,count:3,type:'VEC3',min:[0,0,0],max:[1,1,0]},{bufferView:1,componentType:5126,count:2,type:'SCALAR',min:[0],max:[1]},{bufferView:2,componentType:5126,count:2,type:'VEC3'}],
  ...(options.animation?{animations:[{name:'rise',samplers:[{input:1,output:2,interpolation:'LINEAR'}],channels:[{sampler:0,target:{node:0,path:'translation'}}]}]}:{})}
 const encoded=new TextEncoder().encode(JSON.stringify(document));const jsonSize=Math.ceil(encoded.length/4)*4
 const result=new ArrayBuffer(12+8+jsonSize+8+data.byteLength),view=new DataView(result)
 view.setUint32(0,0x46546c67,true);view.setUint32(4,2,true);view.setUint32(8,result.byteLength,true)
 view.setUint32(12,jsonSize,true);view.setUint32(16,0x4e4f534a,true)
 new Uint8Array(result,20,jsonSize).fill(32);new Uint8Array(result,20,encoded.length).set(encoded)
 view.setUint32(20+jsonSize,data.byteLength,true);view.setUint32(24+jsonSize,0x004e4942,true)
 new Uint8Array(result,28+jsonSize).set(new Uint8Array(data.buffer))
 return result
}
describe('actual Three GLTFLoader geometry',()=>{
 it('loads authored binary triangle positions and computes framing bounds',async()=>{
  const result=await loadGlb(triangle())
  const mesh=result.gltf.scene.children[0] as Mesh
  expect(mesh.isMesh).toBe(true)
  const positions=mesh.geometry.getAttribute('position')
  expect(positions.count).toBe(3)
  expect(positions.getX(1)).toBe(1)
  expect(positions.getY(2)).toBe(1)
  expect(positions.getZ(0)).toBe(0)
  expect(result.center.toArray()).toEqual([.5,.5,0])
  expect(result.radius).toBeCloseTo(Math.sqrt(2)/2)
  expect(result.clips).toEqual([])
  mesh.geometry.dispose()
 })
 it('honors actual GLB node transforms when framing geometry',async()=>{
  const result=await loadGlb(triangle({translation:[3,4,5]}))
  expect(result.center.toArray()).toEqual([3.5,4.5,5])
  expect(result.radius).toBeCloseTo(Math.sqrt(2)/2)
  const position=new Vector3();result.gltf.scene.children[0].getWorldPosition(position)
  expect(position.toArray()).toEqual([3,4,5])
 })
 it('extracts actual animation tracks without inventing clip names',async()=>{
  const result=await loadGlb(triangle({animation:true}))
  expect(result.clips).toEqual(['rise'])
  expect(result.gltf.animations).toHaveLength(1)
  expect(result.gltf.animations[0].duration).toBe(1)
  expect(result.gltf.animations[0].tracks).toHaveLength(1)
  expect(Array.from(result.gltf.animations[0].tracks[0].times)).toEqual([0,1])
  expect(result.gltf.animations[0].tracks[0].getValueSize()).toBe(3)
 })
 it('refuses external model buffers before any network fetch',async()=>{
  await expect(loadGlb(triangle({external:true}))).rejects.toThrow('external resource')
 })
 it('rejects malformed GLB bytes rather than displaying substitute geometry',async()=>{
  await expect(loadGlb(new TextEncoder().encode('not a model').buffer)).rejects.toThrow()
  const bytes=triangle();new DataView(bytes).setUint32(4,1,true)
  await expect(loadGlb(bytes)).rejects.toThrow()
 })
})
