import { spawn,type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll,beforeAll,expect,it } from 'vitest'
import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import DeckAssist from './DeckAssist'
let server:ChildProcess,apiBase:string,deck:any,universe:any
beforeAll(async()=>{
 const root=resolve(process.cwd(),'../..')
 server=spawn(process.env.GIDEON_TEST_PYTHON||'python3',['checks/runtime/capabilities/music/serve_deck_assist.py'],{cwd:root,env:{...process.env,PYTHONPATH:resolve(root,'runtime')},stdio:['ignore','pipe','pipe']})
 const result=await new Promise<any>((done,reject)=>{
  let output='',errors=''
  server.stderr!.on('data',chunk=>{errors+=String(chunk)})
  server.once('error',reject)
  server.once('exit',code=>reject(new Error(`Deck assist HTTP exited ${code}: ${errors}`)))
  server.stdout!.on('data',chunk=>{output+=String(chunk);const line=output.split('\n').find(value=>value.startsWith('{'));if(line)done(JSON.parse(line))})
 })
 apiBase=`http://127.0.0.1:${result.port}/api/capabilities/music/decks`;deck=result.deck;universe=result.universe
})
afterAll(()=>server?.kill('SIGTERM'))
it('shows persisted actual provider refusal without presenting generated prompts or permitting adoption',async()=>{
 render(<DeckAssist apiBase={apiBase} item={deck} onApplied={()=>{throw new Error('Unavailable proposals cannot apply')}}/>)
 await screen.findByText('prompts · failed')
 expect(screen.getByText('Configured chat model unavailable')).toBeInTheDocument()
 expect(screen.getByText('No configured model is available for design assistance.')).toBeInTheDocument()
 expect(screen.getByRole('button',{name:'Generate card prompts and casting'})).toBeDisabled()
 expect(screen.getByRole('button',{name:'Analyze card sample'})).toBeDisabled()
 expect(screen.getByRole('button',{name:'Apply proposal'})).toBeDisabled()
 expect(screen.getByText(/Available keys: spades-A/)).toBeInTheDocument()
 fireEvent.change(screen.getByLabelText('Universe ID'),{target:{value:universe.id}})
 fireEvent.change(screen.getByLabelText('Universe revision'),{target:{value:1}})
 fireEvent.change(screen.getByLabelText('Card keys separated by commas'),{target:{value:'spades-A,back'}})
 expect(screen.getByLabelText('Universe ID')).toHaveValue(universe.id)
 expect(screen.getByLabelText('Card keys separated by commas')).toHaveValue('spades-A,back')
 fireEvent.click(screen.getByRole('button',{name:'Refresh design proposals'}))
 await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh design proposals'})).not.toBeDisabled())
 const actual=(await(await fetch(apiBase+'/'+deck.id)).json()).item
 expect(actual.revision).toBe(1)
 expect(actual.cards[0].prompt).toBe('')
 const proposals=(await(await fetch(apiBase+'/'+deck.id+'/assist')).json()).items
 expect(proposals).toHaveLength(1)
 expect(proposals[0].universe_ref).toEqual({id:universe.id,revision:1})
 expect(proposals[0].universe_snapshot.canon[0].id).toBe('hero')
 expect(proposals[0].result).toBeNull()
})
it('reopens failed proposal history independently of editable image reference fields',async()=>{
 const view=render(<DeckAssist apiBase={apiBase} item={deck} onApplied={()=>{throw new Error('Unexpected apply')}}/>)
 await screen.findByText('prompts · failed')
 fireEvent.change(screen.getByLabelText('Analysis image slug'),{target:{value:'not-selected'}})
 fireEvent.change(screen.getByLabelText('Analysis image version'),{target:{value:3}})
 expect(screen.getByLabelText('Analysis image version')).toHaveValue(3)
 expect(screen.getByRole('button',{name:'Analyze card sample'})).toBeDisabled()
 view.unmount()
 render(<DeckAssist apiBase={apiBase} item={deck} onApplied={()=>{throw new Error('Unexpected apply')}}/>)
 await screen.findByText('prompts · failed')
 expect(screen.getByLabelText('Analysis image slug')).toHaveValue('')
 expect(screen.getByRole('button',{name:'Apply proposal'})).toBeDisabled()
 const result=await fetch(apiBase+'/'+deck.id+'/assist')
 expect(result.status).toBe(200)
 const rows=(await result.json()).items
 expect(rows[0].applied_revision).toBeNull()
 expect(rows[0].model).toBe('')
})
