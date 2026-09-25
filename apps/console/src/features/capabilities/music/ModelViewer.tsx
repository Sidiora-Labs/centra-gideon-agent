import { useEffect, useRef, useState } from 'react'
import { AmbientLight, AnimationMixer, Clock, DirectionalLight, PerspectiveCamera, Scene, WebGLRenderer, Mesh, Material, Texture, AnimationAction } from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { loadGlb } from './glb'
type Props={artifactRef:{slug:string;version:number};artifactBase?:string;animationName?:string;reducedMotion?:boolean;onClips?:(names:string[])=>void}
export default function ModelViewer({artifactRef,artifactBase='/api/capabilities/music/models3d/artifacts',animationName,reducedMotion=false,onClips}:Props){
 const host=useRef<HTMLDivElement>(null),mixer=useRef<AnimationMixer|null>(null),clips=useRef<Awaited<ReturnType<typeof loadGlb>>['gltf']['animations']>([]),motion=useRef(reducedMotion),callback=useRef(onClips)
 const action=useRef<AnimationAction|null>(null)
 const [error,setError]=useState(''),[loaded,setLoaded]=useState(false)
 callback.current=onClips;motion.current=reducedMotion
 useEffect(()=>{
  let disposed=false,frame=0,renderer:WebGLRenderer|undefined,controls:OrbitControls|undefined,root:Awaited<ReturnType<typeof loadGlb>>|undefined
  const abort=new AbortController();setError('');setLoaded(false)
  const container=host.current!
  async function start(){
   try{
    const response=await fetch(`${artifactBase}/${encodeURIComponent(artifactRef.slug)}/${artifactRef.version}/raw`,{credentials:'same-origin',signal:abort.signal})
    if(!response.ok)throw new Error('Model version unavailable')
    root=await loadGlb(await response.arrayBuffer());if(disposed)return
    renderer=new WebGLRenderer({antialias:true,alpha:true});renderer.setSize(640,400);renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.domElement.style.width='100%';renderer.domElement.style.height='auto';container.appendChild(renderer.domElement)
    const scene=new Scene();scene.add(root.gltf.scene,new AmbientLight(0xffffff,2))
    const light=new DirectionalLight(0xffffff,3);light.position.set(3,4,5);scene.add(light)
    const camera=new PerspectiveCamera(40,1.6,root.radius/100,root.radius*100);camera.position.copy(root.center).addScalar(root.radius*2)
    controls=new OrbitControls(camera,renderer.domElement);controls.target.copy(root.center);controls.update()
    mixer.current=new AnimationMixer(root.gltf.scene);clips.current=root.gltf.animations;callback.current?.(root.clips);setLoaded(true)
    const clock=new Clock()
    const draw=()=>{if(disposed)return;const delta=Math.min(clock.getDelta(),.1);if(!motion.current)mixer.current?.update(delta);controls!.update();renderer!.render(scene,camera);frame=requestAnimationFrame(draw)};draw()
   }catch(err){if(!disposed)setError((err as Error).message)}
  }
  void start()
  return()=>{disposed=true;abort.abort();cancelAnimationFrame(frame);controls?.dispose();mixer.current?.stopAllAction();mixer.current=null;root?.gltf.scene.traverse(object=>{if(object instanceof Mesh){object.geometry.dispose();const materials=Array.isArray(object.material)?object.material:[object.material];materials.forEach((material:Material)=>{for(const value of Object.values(material))if(value instanceof Texture)value.dispose();material.dispose()})}});renderer?.dispose();renderer?.domElement.remove()}
 },[artifactRef.slug,artifactRef.version,artifactBase])
 useEffect(()=>{const current=mixer.current;if(!current)return;if(reducedMotion||!animationName){current.stopAllAction();action.current=null;return}const clip=clips.current.find(row=>row.name===animationName);if(clip){action.current?.fadeOut(.15);action.current=current.clipAction(clip).reset().fadeIn(.15).play()}},[animationName,reducedMotion,loaded])
 return <div><div ref={host} aria-label="3D model viewer" className="min-h-64 w-full"/>{error?<p role="alert">{error}</p>:!loaded?<p role="status">Loading 3D geometry…</p>:<p>Drag to orbit. Scroll to zoom.</p>}</div>
}
