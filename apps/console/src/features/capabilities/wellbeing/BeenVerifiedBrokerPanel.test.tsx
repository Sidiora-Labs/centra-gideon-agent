import { afterAll, beforeAll, expect, it } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { BeenVerifiedBrokerPanel } from './BeenVerifiedBrokerPanel'

type Case={id:string;revision:number;state:string}
let server:ChildProcess,origin:string,home:string,initial:Case
const originalFetch=globalThis.fetch

beforeAll(async()=>{
  home=mkdtempSync(resolve(tmpdir(),'gideon-beenverified-ui-'));const root=resolve(process.cwd(),'../..')
  server=spawn(process.env.GIDEON_TEST_PYTHON||'python3',['checks/runtime/capabilities/wellbeing/beenverified_ui_server.py'],{cwd:root,env:{...process.env,GIDEON_HOME:home,PYTHONPATH:resolve(root,'runtime')},stdio:['ignore','pipe','pipe']})
  origin=await new Promise<string>((done,reject)=>{let output='',errors='';server.stderr?.on('data',chunk=>{errors+=String(chunk)});server.stdout?.on('data',chunk=>{output+=String(chunk);const line=output.split('\n').find(value=>value.startsWith('{"port":'));if(line)done(`http://127.0.0.1:${JSON.parse(line).port}`)});server.on('error',reject);server.on('exit',code=>reject(new Error(`server exited ${code}: ${errors}`)))})
  globalThis.fetch=(input,init)=>originalFetch(typeof input==='string'&&input.startsWith('/')?origin+input:input,init)
  initial=(await originalFetch(origin+'/contract/config').then(response=>response.json())).case
})
afterAll(()=>{cleanup();globalThis.fetch=originalFetch;server?.kill();rmSync(home,{recursive:true,force:true})})

function Harness(){const[row,setRow]=useState(initial);return <BeenVerifiedBrokerPanel brokerCase={row} onChanged={setRow}/>}

it('prepares exact content without sending, then requires approval and a separate dispatch confirmation',async()=>{
  render(<Harness/>);await screen.findByRole('option',{name:'Owner email — owner@example.com'})
  fireEvent.change(screen.getByLabelText('BeenVerified full name'),{target:{value:'Jane Doe'}})
  fireEvent.change(screen.getByLabelText('BeenVerified listing URL'),{target:{value:'https://www.beenverified.com/name/Jane-Doe/Oakland-CA/abc'}})
  fireEvent.change(screen.getByLabelText('Jurisdiction'),{target:{value:'US-CA'}})
  expect(screen.getByText(/does not complete BeenVerified's primary suppression form or affiliate rescans/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'Prepare BeenVerified deletion email'}))
  await screen.findByText('To: privacy@beenverified.com')
  expect(screen.getByText(/CCPA section 1798.105/)).toBeInTheDocument()
  expect(screen.getByText(/Draft state: draft; provider acceptance: not_submitted; delivery: not_submitted/)).toBeInTheDocument()
  expect((await originalFetch(origin+'/contract/messages').then(response=>response.json())).messages).toHaveLength(0)
  expect(screen.queryByRole('button',{name:'Send approved BeenVerified deletion email'})).not.toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('I approve this exact BeenVerified deletion recipient and content'))
  await waitFor(()=>expect(screen.getByRole('button',{name:'Approve exact BeenVerified deletion email'})).toBeEnabled())
  fireEvent.click(screen.getByRole('button',{name:'Approve exact BeenVerified deletion email'}))
  await screen.findByLabelText('Dispatch this approved BeenVerified deletion email')
  expect((await originalFetch(origin+'/contract/messages').then(response=>response.json())).messages).toHaveLength(0)
  fireEvent.click(screen.getByLabelText('Dispatch this approved BeenVerified deletion email'))
  await waitFor(()=>expect(screen.getByRole('button',{name:'Send approved BeenVerified deletion email'})).toBeEnabled())
  fireEvent.click(screen.getByRole('button',{name:'Send approved BeenVerified deletion email'}))
  await screen.findByText(/provider acceptance: accepted; delivery: uncertain/)
  const contract=await originalFetch(origin+'/contract/messages').then(response=>response.json())
  expect(contract.messages).toHaveLength(1);expect(contract.messages[0].sender).toBe('owner@example.com');expect(contract.messages[0].recipients).toEqual(['privacy@beenverified.com'])
})

it('correlates an ingested reply and manually matches its code without opening its link or claiming removal',async()=>{
  initial=(await originalFetch(origin+'/contract/config').then(response=>response.json())).case;render(<Harness/>);await screen.findByText(/Draft state: accepted; provider acceptance: accepted; delivery: uncertain/)
  await originalFetch(origin+'/contract/reply',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({})})
  fireEvent.change(screen.getByLabelText('Manual verification code (optional)'),{target:{value:'482913'}})
  fireEvent.click(screen.getByRole('button',{name:'Correlate ingested reply'}))
  await screen.findByText('Reply: <ui-beenverified-reply@example.com>')
  expect(screen.getByText(/Unopened link: https:\/\/untrusted.example\/verify \(not opened\)/)).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('manual code matched: true')
  expect(screen.getByRole('status')).toHaveTextContent('removal confirmed: false')
  expect(screen.getByRole('status')).toHaveTextContent('links opened: false')
  expect(screen.queryByRole('link',{name:/untrusted/})).not.toBeInTheDocument()
})

it('retains the accepted receipt without offering a duplicate send control',async()=>{
  cleanup();initial=(await originalFetch(origin+'/contract/config').then(response=>response.json())).case;render(<Harness/>);await screen.findByText(/Draft state: verified; provider acceptance: accepted; delivery: uncertain/)
  expect(screen.queryByRole('button',{name:'Send approved BeenVerified deletion email'})).not.toBeInTheDocument()
  const contract=await originalFetch(origin+'/contract/messages').then(response=>response.json());expect(contract.messages).toHaveLength(1)
})
