import { spawn,type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll,beforeAll,expect,it } from 'vitest'
import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import AssemblyPage from './AssemblyPage'
let server:ChildProcess
let apiBase:string,modelBase:string
beforeAll(async()=>{
 const root=resolve(process.cwd(),'../..')
 server=spawn(process.env.GIDEON_TEST_PYTHON||'python3',['checks/runtime/capabilities/music/serve_assembly.py'],{cwd:root,env:{...process.env,PYTHONPATH:resolve(root,'runtime')},stdio:['ignore','pipe','pipe']})
 const result=await new Promise<{port:number}>((done,reject)=>{
  let output='',errors=''
  server.stderr!.on('data',chunk=>{errors+=String(chunk)})
  server.once('error',reject)
  server.once('exit',code=>reject(new Error(`Assembly HTTP exited ${code}: ${errors}`)))
  server.stdout!.on('data',chunk=>{output+=String(chunk);const line=output.split('\n').find(value=>value.startsWith('{'));if(line)done(JSON.parse(line))})
 })
 apiBase=`http://127.0.0.1:${result.port}/api/capabilities/music/assemblies`
 modelBase=`http://127.0.0.1:${result.port}/api/capabilities/music/models3d/artifacts`
})
afterAll(()=>server?.kill('SIGTERM'))
it('authors actual primitive geometry, refines findings, exports and reopens pinned artifacts',async()=>{
 const page=render(<AssemblyPage apiBase={apiBase} modelBase={modelBase}/> )
 fireEvent.change(screen.getByLabelText('Assembly title'),{target:{value:'Authored cube'}})
 fireEvent.click(screen.getByRole('button',{name:'Create assembly'}))
 await screen.findByText('Assembly revision 1')
 await waitFor(()=>expect(screen.getByRole('button',{name:'Place on ground'})).not.toBeDisabled())
 expect(screen.getByText(/off_ground:/)).toBeInTheDocument()
 expect(screen.getByText('1 parts · 12 triangles · estimated volume 1.000')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'Place on ground'}))
 await screen.findByText('Assembly revision 2')
 await waitFor(()=>expect(screen.getByRole('button',{name:'Export assembly'})).not.toBeDisabled())
 expect(screen.getByText('No current geometry findings.')).toBeInTheDocument()
 expect(JSON.parse((screen.getByLabelText('Parts and clips JSON') as HTMLTextAreaElement).value).parts[0].position).toEqual([0,.5,0])
 fireEvent.click(screen.getByRole('button',{name:'Export assembly'}))
 const download=await screen.findByRole('link',{name:'Download assembly GLB'})
 const response=await fetch(download.getAttribute('href')!)
 expect(response.status).toBe(200)
 const bytes=new Uint8Array(await response.arrayBuffer())
 expect(new TextDecoder().decode(bytes.slice(0,4))).toBe('glTF')
 const source=await fetch(screen.getByRole('link',{name:'Download runnable Three.js source'}).getAttribute('href')!)
 expect(await source.text()).toContain('export function createAssembly')
 expect(screen.getByText('Revision 1: Authored cube · 1 findings')).toBeInTheDocument()
 expect(screen.getByText('Revision 2: Authored cube · 0 findings')).toBeInTheDocument()
 const href=download.getAttribute('href')
 page.unmount()
 render(<AssemblyPage apiBase={apiBase} modelBase={modelBase}/> )
 fireEvent.click(await screen.findByRole('button',{name:'Authored cube'}))
 await screen.findByText('Assembly revision 2')
 expect(screen.getByRole('link',{name:'Download assembly GLB'})).toHaveAttribute('href',href)
 expect(screen.getByRole('button',{name:'Preview exported model'})).toBeInTheDocument()
})
it('retains malformed and invalid edits without overwriting saved geometry',async()=>{
 render(<AssemblyPage apiBase={apiBase} modelBase={modelBase}/> )
 fireEvent.click(await screen.findByRole('button',{name:'Authored cube'}))
 await screen.findByText('Assembly revision 2')
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save assembly'})).not.toBeDisabled())
 const original=(screen.getByLabelText('Parts and clips JSON') as HTMLTextAreaElement).value
 fireEvent.change(screen.getByLabelText('Parts and clips JSON'),{target:{value:'{ broken'}})
 expect(screen.getByRole('button',{name:'Export assembly'})).toBeDisabled()
 fireEvent.click(screen.getByRole('button',{name:'Save assembly'}))
 await screen.findByRole('alert')
 expect(screen.getByLabelText('Parts and clips JSON')).toHaveValue('{ broken')
 const invalid=JSON.parse(original);invalid.parts[0].size=[-1,1,1]
 fireEvent.change(screen.getByLabelText('Parts and clips JSON'),{target:{value:JSON.stringify(invalid)}})
 fireEvent.click(screen.getByRole('button',{name:'Save assembly'}))
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('Finite number outside supported geometry range'))
 const rows=(await(await fetch(apiBase)).json()).items
 expect(rows[0].revision).toBe(2)
 expect(rows[0].parts[0].size).toEqual([1,1,1])
 fireEvent.change(screen.getByLabelText('Parts and clips JSON'),{target:{value:original}})
 fireEvent.click(screen.getByRole('button',{name:'Save assembly'}))
 await screen.findByText('Assembly revision 3')
 expect(screen.queryByRole('link',{name:'Download assembly GLB'})).not.toBeInTheDocument()
})
