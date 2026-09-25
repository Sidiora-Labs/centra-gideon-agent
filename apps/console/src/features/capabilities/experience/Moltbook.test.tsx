import { spawn,type ChildProcess } from 'node:child_process'
import { mkdtemp,rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join,resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { afterAll,beforeAll,expect,test } from 'vitest'
import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import Moltbook from './Moltbook'
let server:ChildProcess,origin='',home=''
beforeAll(async()=>{home=await mkdtemp(join(tmpdir(),'gideon-moltbook-ui-'));const root=resolve(process.cwd(),'../..');let diagnostics='';server=spawn(process.env.GIDEON_TEST_PYTHON||'/tmp/gideon-runtime-venv/bin/python',['checks/runtime/capabilities/experience/moltbook_ui_server.py'],{cwd:root,env:{...process.env,PYTHONPATH:join(root,'runtime'),GIDEON_HOME:home},stdio:['ignore','pipe','pipe']});server.stderr!.on('data',data=>diagnostics+=data.toString());const ready=await new Promise<{port:number}>((accept,reject)=>{const lines=createInterface({input:server.stdout!});lines.on('line',line=>{try{accept(JSON.parse(line));lines.close()}catch{}});server.once('error',reject);server.once('exit',code=>reject(new Error(`server exited ${code}: ${diagnostics}`)))});origin=`http://127.0.0.1:${ready.port}`})
afterAll(async()=>{if(server?.exitCode===null)await new Promise<void>(done=>{server.once('exit',()=>done());server.kill('SIGTERM')});await rm(home,{recursive:true,force:true})})
const change=(name:string,value:string)=>fireEvent.change(screen.getByLabelText(name),{target:{value}})
test('reads the real protocol server and requires explicit approval for durable writes',async()=>{
 render(<Moltbook baseUrl={origin}/>);expect(screen.getByRole('region',{name:'Moltbook adapter'})).toHaveTextContent('every post or comment requires explicit approval');await screen.findByText('Existing account configured');fireEvent.click(screen.getByRole('button',{name:'Read profile'}));expect(await screen.findByLabelText('Moltbook profile')).toHaveTextContent('UI Molty');fireEvent.click(screen.getByRole('button',{name:'Load new feed'}));await screen.findByRole('heading',{name:'Wire protocol post'});const feed=screen.getByRole('region',{name:'Moltbook feed'});expect(feed).toHaveTextContent('Fetched from the local Moltbook protocol server.');expect(feed).toHaveTextContent('ProtocolMolty')
 change('Moltbook post title','Approved UI dispatch');change('Moltbook post content','Reviewed post body.');const publish=screen.getByRole('button',{name:'Publish approved post'});expect(publish).toBeDisabled();expect(screen.getByRole('region',{name:'Moltbook history'})).not.toHaveTextContent('Approved UI dispatch');fireEvent.click(screen.getByLabelText('Approve Moltbook post'));fireEvent.click(publish);await waitFor(()=>expect(screen.getByRole('region',{name:'Moltbook history'})).toHaveTextContent('post: pending_verification · Approved UI dispatch'))
 change('Moltbook comment post ID','ui-post');change('Moltbook comment content','Reviewed comment body.');expect(screen.getByRole('button',{name:'Publish approved comment'})).toBeDisabled();fireEvent.click(screen.getByLabelText('Approve Moltbook comment'));fireEvent.click(screen.getByRole('button',{name:'Publish approved comment'}));await waitFor(()=>expect(screen.getByRole('region',{name:'Moltbook history'})).toHaveTextContent('comment: published'))
 const history=await (await fetch(origin+'/api/capabilities/experience/moltbook/history')).json();expect(history.items.some((row:{status:string})=>row.status==='pending_verification')).toBe(true);expect(history.items.some((row:{status:string})=>row.status==='published')).toBe(true);expect(JSON.stringify(history)).not.toContain('Reviewed post body.');expect(JSON.stringify(history)).not.toContain('Reviewed comment body.')
})
test('does not expose registration or raw credential fields',async()=>{
 render(<Moltbook baseUrl={origin}/>);await screen.findByText('Existing account configured');expect(screen.queryByRole('button',{name:/register/i})).not.toBeInTheDocument();expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();expect(screen.getByLabelText('Moltbook credential')).toHaveValue('moltbook-ui')
})
