import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import VideoPage from './VideoPage'
let server:ChildProcess
let apiBase:string,catalogBase:string,selection:string,slugs:string[]
beforeAll(async()=>{
 const root=resolve(process.cwd(),'../..')
 server=spawn(process.env.GIDEON_TEST_PYTHON||'python3',['checks/runtime/capabilities/music/serve_video.py'],{cwd:root,env:{...process.env,PYTHONPATH:resolve(root,'runtime')},stdio:['ignore','pipe','pipe']})
 const result=await new Promise<{port:number;selection:string;slugs:string[]}>((done,reject)=>{
  let output='',errors=''
  server.stderr!.on('data',chunk=>{errors+=String(chunk)})
  server.once('error',reject)
  server.once('exit',code=>reject(new Error(`Video HTTP exited ${code}: ${errors}`)))
  server.stdout!.on('data',chunk=>{output+=String(chunk);const line=output.split('\n').find(value=>value.startsWith('{'));if(line)done(JSON.parse(line))})
 })
 apiBase=`http://127.0.0.1:${result.port}/api/capabilities/music/videos`
 catalogBase=`http://127.0.0.1:${result.port}/api/capabilities/music/catalog`
 selection=result.selection;slugs=result.slugs
})
afterAll(()=>server?.kill('SIGTERM'))
it('authors a beat-cut project and renders actual audio/images to a playable canonical video',async()=>{
 const page=render(<VideoPage apiBase={apiBase} catalogBase={catalogBase}/> )
 await screen.findByRole('option',{name:/Real PCM notes/})
 fireEvent.change(screen.getByLabelText('Video title'),{target:{value:'Red and blue beats'}})
 fireEvent.change(screen.getByLabelText('Audio recording'),{target:{value:selection}})
 fireEvent.change(screen.getByLabelText('Scene 1 image slug'),{target:{value:slugs[0]}})
 fireEvent.change(screen.getByLabelText('Scene 1 beats'),{target:{value:'1'}})
 fireEvent.click(screen.getByRole('button',{name:'Add image scene'}))
 fireEvent.change(screen.getByLabelText('Scene 2 image slug'),{target:{value:slugs[1]}})
 fireEvent.change(screen.getByLabelText('Scene 2 beats'),{target:{value:'1'}})
 expect(screen.getByText('Total video 1.00 seconds')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'Create video project'}))
 await screen.findByText('Saved video revision 1')
 fireEvent.click(screen.getByRole('button',{name:'Render music video'}))
 await screen.findByText('Render completed · revision 1',{}, {timeout:15000})
 const player=screen.getByLabelText('Rendered music video')
 expect(player).toHaveAttribute('controls')
 expect(player.getAttribute('src')).toMatch(/^\/api\/artifacts\/.+\/raw\?version=1$/)
 expect(screen.getByRole('link',{name:'Download music video'})).toHaveAttribute('href',player.getAttribute('src'))
 const jobs=(await(await fetch(apiBase+'/jobs')).json()).jobs
 expect(jobs).toHaveLength(1)
 expect(jobs[0].status).toBe('completed')
 expect(jobs[0].snapshot.scenes[1].start_seconds).toBe(.5)
 expect(jobs[0].snapshot.audio_ref.version).toBe(1)
 fireEvent.change(screen.getByLabelText('Grid tempo BPM'),{target:{value:'100'}})
 expect(screen.getByRole('button',{name:'Render music video'})).toBeDisabled()
 fireEvent.click(screen.getByRole('button',{name:'Save video project'}))
 await screen.findByText('Saved video revision 2')
 expect(screen.getByText('Total video 1.20 seconds')).toBeInTheDocument()
 page.unmount()
 render(<VideoPage apiBase={apiBase} catalogBase={catalogBase}/> )
 fireEvent.click(await screen.findByRole('button',{name:'Red and blue beats'}))
 await screen.findByText('Saved video revision 2')
 expect(screen.getByLabelText('Grid tempo BPM')).toHaveValue(100)
 await screen.findByText('Render completed · revision 1')
},20000)
it('preserves an invalid authored grid for correction and leaves the saved project intact',async()=>{
 render(<VideoPage apiBase={apiBase} catalogBase={catalogBase}/> )
 fireEvent.click(await screen.findByRole('button',{name:'Red and blue beats'}))
 await screen.findByText('Saved video revision 2')
 fireEvent.change(screen.getByLabelText('Scene 1 beats'),{target:{value:'64'}})
 fireEvent.click(screen.getByRole('button',{name:'Save video project'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('Beat arrangement exceeds recording or sixty seconds')
 expect(screen.getByLabelText('Scene 1 beats')).toHaveValue(64)
 expect(screen.getByRole('button',{name:'Render music video'})).toBeDisabled()
 const items=(await(await fetch(apiBase)).json()).items
 expect(items[0].revision).toBe(2)
 expect(items[0].scenes[0].beats).toBe(1)
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save video project'})).not.toBeDisabled())
})
