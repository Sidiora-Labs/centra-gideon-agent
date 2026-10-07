import { spawn,type ChildProcess } from 'node:child_process'
import { mkdtemp,rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join,resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { afterAll,beforeAll,expect,test } from 'vitest'
import { fireEvent,render,screen,waitFor,within } from '@testing-library/react'
import CreativeDirection from './CreativeDirection'
let server:ChildProcess,origin='',home='',workId='',revision=0
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(join(tmpdir(), 'gideon-creative-ui-'))
  const root = resolve(process.cwd(), '../..')
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [resolve(root, 'checks/runtime/capabilities/creative/direction_ui_server.py')], { cwd: root, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  const ready = await new Promise<{ url: string; token: string; work_id: string; revision: number }>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      try {
        const value = JSON.parse(line)
        if (typeof value.url !== 'string' || typeof value.token !== 'string') return
        accept(value); lines.close()
      } catch { /* Read the native child readiness record. */ }
    })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
  const baseUrl = ready.url
  origin = baseUrl; workId = ready.work_id; revision = ready.revision
  expect((await nativeFetch(`${baseUrl}/api/capabilities/creative/direction`)).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), baseUrl)
    if (url.origin !== baseUrl) return nativeFetch(input, init)
    const headers = new Headers(init?.headers)
    headers.set('Authorization', `Bearer ${ready.token}`)
    return nativeFetch(url, { ...init, headers })
  }
})
afterAll(async()=>{globalThis.fetch = nativeFetch;if(server?.exitCode===null)await new Promise<void>(done=>{server.once('exit',()=>done());server.kill('SIGTERM')});await rm(home,{recursive:true,force:true})})
const change = (name: string, value: string) => {
  const field = screen.getByLabelText(name)
  fireEvent.change(field, { target: { value } })
  if (field instanceof HTMLInputElement && field.type === 'number') fireEvent.blur(field)
}
test('directs a real canonical manuscript through pause plan edit restart and durable output',async()=>{
  render(<CreativeDirection baseUrl={origin}/>);expect(screen.getByRole('region',{name:'Creative direction'})).toHaveTextContent('exact canonical manuscript revisions');expect(screen.getByRole('button',{name:'Create production plan'})).toHaveAttribute('aria-disabled','true')
  change('Direction project name','Harbor trailer');change('Creative treatment','Cold blue light crosses the harbor.');change('Direction source ID',workId);change('Direction source revision',String(revision));await waitFor(() => { const button = screen.getByRole('button',{name:'Create production plan'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button',{name:'Create production plan'}))
  let card=await screen.findByRole('article',{name:'Direction project Harbor trailer'});expect(card).toHaveTextContent(`work ${workId} revision ${revision}`);expect(card).toHaveTextContent('Verify canonical sources: pending');expect(card).toHaveTextContent('Review treatment against sources: pending')
  await waitFor(() => { const button = within(card).getByRole('button',{name:'Start production for Harbor trailer'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(within(card).getByRole('button',{name:'Start production for Harbor trailer'}));await waitFor(()=>expect(screen.getByText(/running · revision 2/)).toBeInTheDocument());card=screen.getByRole('article',{name:'Direction project Harbor trailer'});await waitFor(() => { const button = within(card).getByRole('button',{name:'Complete next step for Harbor trailer'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(within(card).getByRole('button',{name:'Complete next step for Harbor trailer'}));await waitFor(()=>expect(screen.getByText('Verify canonical sources: done')).toBeInTheDocument())
  card=screen.getByRole('article',{name:'Direction project Harbor trailer'});await waitFor(() => { const button = within(card).getByRole('button',{name:'Pause production for Harbor trailer'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(within(card).getByRole('button',{name:'Pause production for Harbor trailer'}));await waitFor(()=>expect(screen.getByText(/paused · revision 4/)).toBeInTheDocument());card=screen.getByRole('article',{name:'Direction project Harbor trailer'});await waitFor(() => { const button = within(card).getByRole('button',{name:'Add treatment output for Harbor trailer'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(within(card).getByRole('button',{name:'Add treatment output for Harbor trailer'}));await waitFor(()=>expect(screen.getByText('Publish treatment snapshot: pending')).toBeInTheDocument());expect(screen.getByText('Verify canonical sources: done')).toBeInTheDocument()
  card=screen.getByRole('article',{name:'Direction project Harbor trailer'});await waitFor(() => { const button = within(card).getByRole('button',{name:'Resume production for Harbor trailer'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(within(card).getByRole('button',{name:'Resume production for Harbor trailer'}));await waitFor(()=>expect(screen.getByText(/running · revision 6/)).toBeInTheDocument());card=screen.getByRole('article',{name:'Direction project Harbor trailer'});await waitFor(() => { const button = within(card).getByRole('button',{name:'Complete next step for Harbor trailer'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(within(card).getByRole('button',{name:'Complete next step for Harbor trailer'}));await waitFor(()=>expect(screen.getByText('Review treatment against sources: done')).toBeInTheDocument());card=screen.getByRole('article',{name:'Direction project Harbor trailer'});await waitFor(() => { const button = within(card).getByRole('button',{name:'Complete next step for Harbor trailer'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(within(card).getByRole('button',{name:'Complete next step for Harbor trailer'}));await waitFor(()=>expect(screen.getByText(/completed · revision 8/)).toBeInTheDocument());expect(screen.getByRole('link',{name:'Open output'}).getAttribute('href')).toMatch(new RegExp(`^${origin}/api/artifacts/creative-direction-.+\?version=1$`))
  const saved=await (await fetch(origin+'/api/capabilities/creative/direction')).json();expect(saved.items).toHaveLength(1);expect(saved.items[0].steps.map((step:{status:string})=>step.status)).toEqual(['done','done','done']);expect(saved.items[0].sources[0].chapters[0].content_hash).toMatch(/^[a-f0-9]{64}$/)
})
test('shows real missing source failure without a placeholder project',async()=>{
  const before=await (await fetch(origin+'/api/capabilities/creative/direction')).json();render(<CreativeDirection baseUrl={origin}/>);await waitFor(()=>expect(screen.queryAllByRole('article')).toHaveLength(before.items.length));change('Direction project name','Missing');change('Creative treatment','No substitute.');change('Direction source ID','missing-work');await waitFor(() => { const button = screen.getByRole('button',{name:'Create production plan'}); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button',{name:'Create production plan'}));expect(await screen.findByRole('alert')).toHaveTextContent('Work not found');const after=await (await fetch(origin+'/api/capabilities/creative/direction')).json();expect(after.items).toHaveLength(before.items.length)
})
