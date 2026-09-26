import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { api } from '../../shared/data/api'
import { IntrospectPanel } from './IntrospectPanel'

let server: ChildProcess
let home = ''
let baseUrl = ''
let measuredId = ''
let emptyId = ''
const nativeFetch = globalThis.fetch

beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-workflow-donor-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, time
from aiohttp import web
from gideon.automation.workflows import journal as J, store
from gideon.automation.workflows.models import WorkflowRun, RunStatus
from gideon.automation.workflows.handlers import api_run_introspect

async def main():
 measured = store.create(WorkflowRun(id='',workflow_name='Proof flow',status=RunStatus.COMPLETE))
 empty = store.create(WorkflowRun(id='',workflow_name='Empty flow',status=RunStatus.RUNNING))
 journal = J.Journal(measured.id)
 journal.write(J.RUN_STARTED)
 journal.write(J.STEP_COMPLETED,instance_path='main.draft',node_id='draft',attempt=2,state='completed',
  tokens=12,cost_usd=0.02,model='model-a',duration_secs=8,detail='Draft recorded')
 journal.write(J.STEP_COMPLETED,instance_path='main.publish',node_id='publish',attempt=1,state='completed',
  tokens=3,cost_usd=0.01,model='model-b',duration_secs=4,detail='Publish recorded')
 journal.write(J.GATE_RESOLVED,instance_path='main.review',node_id='review',approved=False,
  verifies=['draft'],detail='Reviewer rejected draft')
 app=web.Application(); app.router.add_get('/api/workflows/runs/{run_id}/introspect',api_run_introspect)
 runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
 print(site._server.sockets[0].getsockname()[1],measured.id,empty.id,flush=True); await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  const ready = await new Promise<string>((accept, reject) => {
    let output = ''
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.on('data', data => {
      output += String(data)
      if (output.includes('\n')) accept(output.slice(0, output.indexOf('\n')).trim())
    })
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  const [port, measured, empty] = ready.split(' ')
  baseUrl = `http://127.0.0.1:${port}`
  measuredId = measured
  emptyId = empty
  globalThis.fetch = (input, init) => nativeFetch(new URL(String(input), baseUrl), init)
})

afterAll(async () => {
  cleanup()
  globalThis.fetch = nativeFetch
  if (server?.exitCode === null && server.signalCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

test('the live donor timeline retains real journal economics and proof progress', async () => {
  const recorded = await api.workflowRunIntrospect(measuredId)
  expect(recorded.timeline.some(row => row.node_id === 'draft' && row.tokens === 12 && row.cost_usd === 0.02)).toBe(true)
  expect(recorded.proof.verified_steps).toBe(1)
  expect(recorded.proof.total_steps).toBe(2)
  const view = render(<IntrospectPanel runId={measuredId} onClose={cleanup} />)
  fireEvent.click(await screen.findByRole('tab', { name: 'Timeline' }))
  const timeline = await screen.findByLabelText('Workflow events')
  expect(timeline).toHaveAttribute('data-slot', 'timeline')
  expect(timeline).toHaveTextContent('Draft recorded')
  for (const fact of ['step_completed', 'attempt 2', 'model-a', '12 tokens', '~$0.0200', '8s', 'rejected']) {
    expect(timeline).toHaveTextContent(fact)
  }
  expect(within(timeline).getByText('draft · completed')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'Proof' }))
  const progress = screen.getByRole('progressbar', { name: 'Proof flow progress' })
  expect(progress).toHaveAttribute('aria-valuenow', '50')
  expect(screen.getByText('1/2 steps verified')).toBeInTheDocument()
  expect(screen.getByText('50%')).toBeInTheDocument()
  expect(screen.getAllByText(/no evidence files were captured/).length).toBeGreaterThan(0)
  expect(screen.queryByRole('button', { name: 'Cancel the job' })).toBeNull()
  view.unmount()
})

test('a journal-free run does not invent events or measured progress', async () => {
  const view = render(<IntrospectPanel runId={emptyId} onClose={cleanup} />)
  fireEvent.click(await screen.findByRole('tab', { name: 'Timeline' }))
  expect(await screen.findByText('This run has written no journal events yet.')).toBeInTheDocument()
  expect(document.querySelector('[data-slot="timeline"]')).toBeNull()
  fireEvent.click(screen.getByRole('tab', { name: 'Proof' }))
  expect(screen.getByText('0/0 steps verified')).toBeInTheDocument()
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.queryByText('done')).toBeNull()
  view.unmount()
})
