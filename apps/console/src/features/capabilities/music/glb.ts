import { Box3, Vector3, LoadingManager } from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
export async function loadGlb(bytes:ArrayBuffer){
 const manager=new LoadingManager()
 manager.setURLModifier(url=>{if(url.startsWith('blob:')||url.startsWith('data:'))return url;throw new Error('Model contains an external resource')})
 const gltf=await new GLTFLoader(manager).parseAsync(bytes,'')
 const bounds=new Box3().setFromObject(gltf.scene)
 if(bounds.isEmpty())throw new Error('Model has no visible geometry')
 const center=bounds.getCenter(new Vector3()),size=bounds.getSize(new Vector3())
 if(![...center.toArray(),...size.toArray()].every(Number.isFinite))throw new Error('Model bounds are invalid')
 return {gltf,center,radius:Math.max(size.length()/2,.01),clips:gltf.animations.map(clip=>clip.name)}
}
