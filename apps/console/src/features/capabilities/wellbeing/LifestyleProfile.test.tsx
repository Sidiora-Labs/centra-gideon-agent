import '@testing-library/jest-dom/vitest'
import { afterAll,beforeAll,expect,it } from 'vitest'
import { cleanup,fireEvent,render,screen,waitFor } from '@testing-library/react'
import { spawn,type ChildProcess } from 'node:child_process'
import { mkdtempSync,rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join,resolve } from 'node:path'
import LifestyleProfile from './LifestyleProfile'

let server:ChildProcess,origin:string,home:string
const nativeFetch=globalThis.fetch
beforeAll(async()=>{
  home=mkdtempSync(join(tmpdir(),'gideon-lifestyle-ui-'));const root=resolve('../..')
  server=spawn(process.env.GIDEON_TEST_PYTHON||'python3',['checks/runtime/capabilities/wellbeing/lifestyle_profile_ui_server.py'],{cwd:root,env:{...process.env,GIDEON_HOME:home,PYTHONPATH:resolve(root,'runtime')},stdio:['ignore','pipe','pipe']})
  origin=await new Promise<string>((done,reject)=>{let output='',errors='';server.stdout?.on('data',chunk=>{output+=String(chunk);const line=output.split('\n').find(value=>value.startsWith('{"port"'));if(line)done(`http://127.0.0.1:${JSON.parse(line).port}`)});server.stderr?.on('data',chunk=>{errors+=String(chunk)});server.on('error',reject);server.on('exit',code=>reject(new Error(`server exited ${code}: ${errors}`)))})
  globalThis.fetch=(input,init)=>nativeFetch(typeof input==='string'&&input.startsWith('/')?origin+input:input,init)
})
afterAll(()=>{cleanup();globalThis.fetch=nativeFetch;server?.kill();rmSync(home,{recursive:true,force:true})})

function fill(){
  fireEvent.change(screen.getByLabelText('Observed at'),{target:{value:'2026-09-25T08:30:00+00:00'}})
  fireEvent.change(screen.getByLabelText('Observation source'),{target:{value:'Owner-authored intake'}})
  fireEvent.change(screen.getByLabelText('Reported sex'),{target:{value:'female'}})
  fireEvent.change(screen.getByLabelText('Sex report source'),{target:{value:'Owner report'}})
  fireEvent.change(screen.getByLabelText('Diet quality value'),{target:{value:'8'}})
  fireEvent.change(screen.getByLabelText('Stress value'),{target:{value:'3'}})
  fireEvent.change(screen.getByLabelText('Reported BMI'),{target:{value:'23.4'}})
  fireEvent.change(screen.getByLabelText('Chronic condition labels (comma separated)'),{target:{value:'Asthma, Migraine'}})
  fireEvent.change(screen.getByLabelText('Reported daily alcohol'),{target:{value:'0'}})
}

it('authors, reloads, corrects, histories and exports the actual lifestyle store',async()=>{
  const mounted=render(<LifestyleProfile/>);await screen.findByText('No lifestyle observations');fill()
  fireEvent.click(screen.getByRole('button',{name:'Record observation'}));await waitFor(()=>expect(screen.getByText((_,node)=>node?.tagName==='LI'&&node.textContent==='v1: diet 8/10; stress 3/10; BMI 23.4')).toBeInTheDocument())
  expect(screen.getByText('female · never · BMI 23.4')).toBeInTheDocument()
  const stored=await nativeFetch(origin+'/api/capabilities/wellbeing/lifestyle-profiles').then(value=>value.json());expect(stored.records).toHaveLength(1)
  expect(stored.records[0].reported_daily_alcohol).toEqual({value:0,unit:'standard_drinks_per_day'})
  fireEvent.change(screen.getByLabelText('Stress value'),{target:{value:'4'}});fireEvent.change(screen.getByLabelText('Reported BMI'),{target:{value:'23.1'}})
  fireEvent.click(screen.getByRole('button',{name:'Save correction'}));await waitFor(()=>expect(screen.getByText((_,node)=>node?.tagName==='LI'&&node.textContent==='v2: diet 8/10; stress 4/10; BMI 23.1')).toBeInTheDocument())
  expect(screen.getByText((_,node)=>node?.tagName==='LI'&&node.textContent==='v1: diet 8/10; stress 3/10; BMI 23.4')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'Export canonical profile'}));await screen.findByText('Exported 1 current and 2 historical observations')
  mounted.unmount();render(<LifestyleProfile/>);await waitFor(()=>expect(screen.queryByText('No lifestyle observations')).not.toBeInTheDocument())
  fireEvent.click(await screen.findByRole('button',{name:/Owner-authored intake · BMI 23.1/}));await waitFor(()=>expect(screen.getByText((_,node)=>node?.tagName==='LI'&&node.textContent==='v2: diet 8/10; stress 4/10; BMI 23.1')).toBeInTheDocument())
})

it('surfaces declared-scale validation without persisting a diagnosis or partial record',async()=>{
  cleanup();render(<LifestyleProfile/>);await screen.findByRole('button',{name:'Save correction'})
  fireEvent.change(screen.getByLabelText('Diet quality value'),{target:{value:'11'}});fireEvent.click(screen.getByRole('button',{name:'Save correction'}))
  await screen.findByRole('alert');expect(screen.getByRole('alert')).toHaveTextContent('diet_quality.value')
  const stored=await nativeFetch(origin+'/api/capabilities/wellbeing/lifestyle-profiles').then(value=>value.json());expect(stored.records).toHaveLength(1);expect(stored.records[0].revision).toBe(2)
  expect(JSON.stringify(stored)).not.toContain('diagnosis')
})
