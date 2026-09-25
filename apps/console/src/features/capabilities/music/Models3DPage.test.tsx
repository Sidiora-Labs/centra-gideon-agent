import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll,beforeAll,expect,it } from 'vitest'
import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import Models3DPage from './Models3DPage'
let server:ChildProcess
let apiBase:string,slug:string
beforeAll(async()=>{
 const root=resolve(process.cwd(),'../..')
 server=spawn(process.env.GIDEON_TEST_PYTHON||'python3',['checks/runtime/capabilities/music/serve_image3d.py'],{cwd:root,env:{...process.env,PYTHONPATH:resolve(root,'runtime')},stdio:['ignore','pipe','pipe']})
 const result=await new Promise<{port:number;slug:string}>((done,reject)=>{
  let output='',errors=''
  server.stderr!.on('data',chunk=>{errors+=String(chunk)})
  server.once('error',reject)
  server.once('exit',code=>reject(new Error(`3D HTTP exited ${code}: ${errors}`)))
  server.stdout!.on('data',chunk=>{output+=String(chunk);const line=output.split('\n').find(value=>value.startsWith('{'));if(line)done(JSON.parse(line))})
 })
 apiBase=`http://127.0.0.1:${result.port}/api/capabilities/music/models3d`;slug=result.slug
})
afterAll(()=>server?.kill('SIGTERM'))
it('persists explicit engine settings and keeps generation unavailable without an actual credential',async()=>{
 const page=render(<Models3DPage apiBase={apiBase}/> )
 await screen.findByLabelText('Named 3D credential')
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save 3D settings'})).not.toBeDisabled())
 expect(screen.getByLabelText('Enable 3D engine')).not.toBeChecked()
 expect(screen.getByLabelText('3D model')).toHaveValue('meshy-6')
 expect(screen.getByRole('button',{name:'Generate 3D asset'})).toBeDisabled()
 fireEvent.click(screen.getByLabelText('Enable 3D engine'))
 fireEvent.change(screen.getByLabelText('Named 3D credential'),{target:{value:'missing-meshy-key'}})
 fireEvent.change(screen.getByLabelText('3D model'),{target:{value:'meshy-7.1'}})
 fireEvent.click(screen.getByRole('button',{name:'Save 3D settings'}))
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save 3D settings'})).not.toBeDisabled())
 const config=(await(await fetch(apiBase+'/config')).json()).config
 expect(config.enabled).toBe(true)
 expect(config.credential_name).toBe('missing-meshy-key')
 expect(config.model).toBe('meshy-7.1')
 expect(config.revision).toBe(1)
 fireEvent.change(screen.getByLabelText('Asset title'),{target:{value:'A real object'}})
 fireEvent.change(screen.getByLabelText('Image artifact slug'),{target:{value:slug}})
 fireEvent.change(screen.getByLabelText('License or rights statement'),{target:{value:'Review account license'}})
 fireEvent.change(screen.getByLabelText('Target faces'),{target:{value:'10000'}})
 fireEvent.click(screen.getByLabelText('Generate texture'))
 expect(screen.getByLabelText('Generate texture')).not.toBeChecked()
 expect(screen.getByRole('button',{name:'Generate 3D asset'})).toBeDisabled()
 expect(screen.queryByRole('link',{name:'Download GLB'})).not.toBeInTheDocument()
 expect(screen.queryByLabelText('3D model viewer')).not.toBeInTheDocument()
 expect((await(await fetch(apiBase+'/jobs')).json()).jobs).toEqual([])
 page.unmount()
 render(<Models3DPage apiBase={apiBase}/> )
 await screen.findByLabelText('Named 3D credential')
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save 3D settings'})).not.toBeDisabled())
 expect(screen.getByLabelText('Named 3D credential')).toHaveValue('missing-meshy-key')
 expect(screen.getByLabelText('3D model')).toHaveValue('meshy-7.1')
 expect(screen.getByLabelText('Enable 3D engine')).toBeChecked()
})
it('preserves unsaved provider settings after a real revision conflict',async()=>{
 render(<Models3DPage apiBase={apiBase}/> )
 await screen.findByLabelText('Named 3D credential')
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save 3D settings'})).not.toBeDisabled())
 const current=(await(await fetch(apiBase+'/config')).json()).config
 const response=await fetch(apiBase+'/config',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...current,model:'meshy-6'})})
 expect(response.status).toBe(200)
 fireEvent.change(screen.getByLabelText('Named 3D credential'),{target:{value:'unsaved-local-alias'}})
 fireEvent.click(screen.getByRole('button',{name:'Save 3D settings'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('3D engine configuration changed')
 expect(screen.getByLabelText('Named 3D credential')).toHaveValue('unsaved-local-alias')
 expect(screen.getByRole('button',{name:'Generate 3D asset'})).toBeDisabled()
 const persisted=(await(await fetch(apiBase+'/config')).json()).config
 expect(persisted.credential_name).toBe(current.credential_name)
 expect(persisted.model).toBe('meshy-6')
 fireEvent.click(screen.getByRole('button',{name:'Refresh local jobs'}))
 await waitFor(()=>expect(screen.getByLabelText('Named 3D credential')).toHaveValue(current.credential_name))
})
