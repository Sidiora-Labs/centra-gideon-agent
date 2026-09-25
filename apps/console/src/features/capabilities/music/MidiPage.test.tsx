import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import MidiPage from './MidiPage'
let server:ChildProcess
let apiBase:string,catalogBase:string,selection:string
beforeAll(async()=>{
 const root=resolve(process.cwd(),'../..')
 server=spawn(process.env.GIDEON_TEST_PYTHON||'python3',['checks/runtime/capabilities/music/serve_midi.py'],{cwd:root,env:{...process.env,PYTHONPATH:resolve(root,'runtime')},stdio:['ignore','pipe','pipe']})
 const result=await new Promise<{port:number;selection:string}>((done,reject)=>{
  let output='',errors=''
  server.stderr!.on('data',chunk=>{errors+=String(chunk)})
  server.once('error',reject)
  server.once('exit',code=>reject(new Error(`MIDI HTTP exited ${code}: ${errors}`)))
  server.stdout!.on('data',chunk=>{output+=String(chunk);const line=output.split('\n').find(value=>value.startsWith('{'));if(line)done(JSON.parse(line))})
 })
 apiBase=`http://127.0.0.1:${result.port}/api/capabilities/music/midi`
 catalogBase=`http://127.0.0.1:${result.port}/api/capabilities/music/catalog`
 selection=result.selection
})
afterAll(()=>server?.kill('SIGTERM'))
it('measures actual audio, edits notes through the piano roll, saves and exports a canonical MIDI artifact',async()=>{
 const page=render(<MidiPage apiBase={apiBase} catalogBase={catalogBase}/> )
 await screen.findByRole('option',{name:/Real PCM notes/})
 fireEvent.change(screen.getByLabelText('Transcription title'),{target:{value:'Measured phrase'}})
 fireEvent.change(screen.getByLabelText('Source recording'),{target:{value:selection}})
 fireEvent.click(screen.getByRole('button',{name:'Transcribe recording'}))
 await screen.findByLabelText('Score title')
 expect(screen.getByLabelText('Note 1 pitch')).toHaveValue(69)
 expect(screen.getByLabelText('Note 2 pitch')).toHaveValue(72)
 const first=screen.getByRole('button',{name:'Select note 1, pitch 69'})
 expect(Number(first.getAttribute('width'))).toBeGreaterThan(300)
 expect(first.getAttribute('y')).toBe('270')
 fireEvent.click(first)
 expect(screen.getByLabelText('Note 1 pitch')).toHaveFocus()
 fireEvent.change(screen.getByLabelText('Note 1 pitch'),{target:{value:'67'}})
 expect(screen.getByRole('button',{name:'Select note 1, pitch 67'})).toHaveAttribute('y','290')
 expect(screen.getByRole('button',{name:'Export MIDI'})).toBeDisabled()
 fireEvent.change(screen.getByLabelText('Note 1 velocity'),{target:{value:'100'}})
 fireEvent.click(screen.getByRole('button',{name:'Save notes'}))
 await screen.findByText(/Saved revision 2/)
 fireEvent.click(screen.getByRole('button',{name:'Export MIDI'}))
 const download=await screen.findByRole('link',{name:'Download MIDI'})
 expect(download.getAttribute('href')).toMatch(/^\/api\/artifacts\/midi-.+\/raw\?version=1$/)
 expect(download).toHaveAttribute('download','Measured phrase.mid')
 const items=(await(await fetch(apiBase)).json()).items
 expect(items).toHaveLength(1)
 expect(items[0].notes[0].pitch).toBe(67)
 expect(items[0].notes[0].velocity).toBe(100)
 expect(items[0].source_ref.artifact_ref.version).toBe(1)
 page.unmount()
 render(<MidiPage apiBase={apiBase} catalogBase={catalogBase}/> )
 fireEvent.click(await screen.findByRole('button',{name:'Measured phrase'}))
 await screen.findByLabelText('Score title')
 expect(screen.getByLabelText('Note 1 pitch')).toHaveValue(67)
 expect(screen.getByLabelText('Note 1 velocity')).toHaveValue(100)
})
it('retains invalid timing edits for correction without overwriting saved notes',async()=>{
 render(<MidiPage apiBase={apiBase} catalogBase={catalogBase}/> )
 fireEvent.click(await screen.findByRole('button',{name:'Measured phrase'}))
 await screen.findByLabelText('Note 1 duration_seconds')
 fireEvent.change(screen.getByLabelText('Note 1 duration_seconds'),{target:{value:'20'}})
 fireEvent.click(screen.getByRole('button',{name:'Save notes'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('Note extends beyond source recording')
 expect(screen.getByLabelText('Note 1 duration_seconds')).toHaveValue(20)
 expect(screen.getByRole('button',{name:'Export MIDI'})).toBeDisabled()
 fireEvent.change(screen.getByLabelText('Note 1 duration_seconds'),{target:{value:'0.3'}})
 fireEvent.click(screen.getByRole('button',{name:'Save notes'}))
 await screen.findByText(/Saved revision 3/)
 await waitFor(()=>expect(screen.getByRole('button',{name:'Export MIDI'})).not.toBeDisabled())
 fireEvent.click(screen.getByRole('button',{name:'Delete note 2'}))
 expect(screen.queryByLabelText('Note 2 pitch')).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'Add note'}))
 expect(screen.getByLabelText('Note 2 pitch')).toHaveValue(60)
 fireEvent.keyDown(screen.getByRole('button',{name:'Select note 2, pitch 60'}),{key:'Enter'})
 expect(screen.getByLabelText('Note 2 pitch')).toHaveFocus()
})
