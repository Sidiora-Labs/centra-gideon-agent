import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Interventions from './Interventions'
import { HashRouter } from 'react-router-dom'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-intervention-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_intervention import register
async def main():
 app = web.Application()
 register(app, Path(os.environ['GIDEON_HOME']))
 runner = web.AppRunner(app)
 await runner.setup()
 site = web.TCPSite(runner, '127.0.0.1', 0)
 await site.start()
 print(site._server.sockets[0].getsockname()[1], flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => resolveOrigin(`http://127.0.0.1:${String(data).trim()}`))
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  globalThis.fetch = (input, init) => networkFetch(new URL(String(input), origin), init)
})
afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  if (server?.exitCode === null) await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const base = '/api/capabilities/wellbeing/interventions'
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const open = () => { window.location.hash = ''; return render(<HashRouter><Interventions /></HashRouter>) }
const today = new Date().toISOString().slice(0, 10)
const start = new Date(Date.now() - 2 * 86400000).toISOString().slice(0, 10)

test('actual plan creation, explicit adherence and correction history', async () => {
  const view = open()
  await screen.findByText('No interventions.')
  change('Intervention name', 'Daily walk')
  change('User supplied instructions', 'My own walking plan')
  change('Intervention source', 'personal plan')
  change('Intervention timezone', 'UTC')
  change('Start date', start)
  fireEvent.click(screen.getByRole('button', { name: 'Save intervention' }))
  await screen.findByRole('heading', { name: 'Adherence for Daily walk' })
  expect(screen.getByText('No adherence records.')).toBeInTheDocument()
  expect(screen.getByText('Completed: 0 · Skipped: 0 · Unrecorded: 3')).toBeInTheDocument()
  expect(screen.getByText('Completion among recorded days: Not recorded')).toBeInTheDocument()
  change('Scheduled date', today)
  change('Adherence notes', 'Finished my planned activity')
  fireEvent.click(screen.getByRole('button', { name: 'Save adherence record' }))
  const row = await screen.findByRole('button', { name: `${today}: completed` })
  expect(screen.getByText('Completed: 1 · Skipped: 0 · Unrecorded: 2')).toBeInTheDocument()
  expect(screen.getByText('Completion among recorded days: 100%')).toBeInTheDocument()
  expect(screen.getByText('Recorded schedule coverage: 33%')).toBeInTheDocument()
  fireEvent.click(row)
  await screen.findByRole('heading', { name: 'Correct adherence record' })
  expect(screen.getByLabelText('Scheduled date')).toBeDisabled()
  change('Recorded status', 'skipped')
  change('Adherence notes', 'Corrected my mistaken observation')
  fireEvent.click(screen.getByRole('button', { name: 'Save adherence record' }))
  await screen.findByText('Revision 2: skipped · Corrected my mistaken observation')
  expect(screen.getByText('Revision 1: completed · Finished my planned activity')).toBeInTheDocument()
  expect(screen.getByText('Completion among recorded days: 0%')).toBeInTheDocument()
  expect(screen.getByText('Completed: 0 · Skipped: 1 · Unrecorded: 2')).toBeInTheDocument()
  const catalog = await (await networkFetch(origin + base + '/plans')).json()
  expect(catalog.plans).toHaveLength(1)
  expect(catalog.plans[0].source).toBe('personal plan')
  const records = await (await networkFetch(origin + base + '/plans/' + catalog.plans[0].id + '/records')).json()
  expect(records.records[0].revision).toBe(2)
  expect(records.records[0].plan_revision).toBe(1)
  expect(records.records[0].source).toBe('personal plan')
  view.unmount()
  render(<HashRouter><Interventions /></HashRouter>)
  await screen.findByText('Revision 2: skipped · Corrected my mistaken observation')
  expect(screen.getByLabelText('Adherence notes')).toHaveValue('Corrected my mistaken observation')
})

test('archive keeps historical observations and enforces unique scheduled days', async () => {
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Daily walk' }))
  await screen.findByRole('heading', { name: 'Record adherence' })
  change('Scheduled date', today)
  fireEvent.click(screen.getByRole('button', { name: 'Save adherence record' }))
  expect((await screen.findByRole('alert')).textContent).toContain('already has a record')
  change('Scheduled date', '1900-01-01')
  fireEvent.click(screen.getByRole('button', { name: 'Save adherence record' }))
  expect((await screen.findByRole('alert')).textContent).toContain('outside the intervention schedule')
  change('Intervention name', 'Archived walking plan')
  fireEvent.click(screen.getByLabelText('Archived intervention'))
  fireEvent.click(screen.getByRole('button', { name: 'Save intervention' }))
  await screen.findByText('Archived interventions accept corrections to existing observations.')
  expect(screen.getByText('No interventions.')).toBeInTheDocument()
  expect(screen.getByText('Plan revision 2: Archived walking plan · archived')).toBeInTheDocument()
  expect(screen.getByText('Plan revision 1: Daily walk · active')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save adherence record' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('Include archived interventions'))
  await screen.findByRole('button', { name: 'Archived walking plan (archived)' })
  fireEvent.click(screen.getByRole('button', { name: `${today}: skipped` }))
  await screen.findByRole('heading', { name: 'Correct adherence record' })
  change('Recorded status', 'completed')
  change('Adherence notes', 'Final correction while archived')
  fireEvent.click(screen.getByRole('button', { name: 'Save adherence record' }))
  await screen.findByText('Revision 3: completed · Final correction while archived')
  expect(screen.getByText('Revision 2: skipped · Corrected my mistaken observation')).toBeInTheDocument()
  expect(screen.getByText('Completed: 1 · Skipped: 0 · Unrecorded: 2')).toBeInTheDocument()
  const catalog = await (await networkFetch(origin + base + '/plans?include_archived=true')).json()
  expect(catalog.plans[0].archived).toBe(true)
  expect(catalog.plans[0].revision).toBe(2)
  expect(catalog.plans[0].start_date).toBe(start)
  const records = await (await networkFetch(origin + base + '/plans/' + catalog.plans[0].id + '/records')).json()
  expect(records.records).toHaveLength(1)
  expect(records.records[0].status).toBe('completed')
  expect(records.records[0].plan_revision).toBe(1)
  expect(records.records[0].revision).toBe(3)
})
