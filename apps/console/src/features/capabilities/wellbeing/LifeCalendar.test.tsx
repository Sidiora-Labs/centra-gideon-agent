import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import LifeCalendar from './LifeCalendar'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-life-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_life import register
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_cognition import register as cognition_register
async def main():
 app = web.Application()
 register(app, Path(os.environ['GIDEON_HOME']))
 cognition_register(app, Path(os.environ['GIDEON_HOME']))
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

const base = '/api/capabilities/wellbeing/life'
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const open = () => { window.history.replaceState(null, '', '#/capabilities/wellbeing/life?shell=retained'); return render(<LifeCalendar />) }

test('user declared life projection, canonical reminder and completion suppression', async () => {
  const view = open()
  await screen.findByRole('heading', { name: 'Projection assumptions' })
  expect(screen.queryByLabelText('Life week grid')).not.toBeInTheDocument()
  change('Birth date', '2020-01-01')
  change('Declared horizon in years', '10')
  change('Assumed sleep hours per day', '8')
  change('Life calendar timezone', 'UTC')
  change('Assumption source', 'my declared plan')
  fireEvent.click(screen.getByRole('button', { name: 'Add activity budget' }))
  change('Activity 1', 'Learning')
  change('Weekly hours 1', '7')
  fireEvent.click(screen.getByLabelText('Enable daily completion reminder'))
  change('Reminder local time', '00:00')
  fireEvent.click(screen.getByRole('button', { name: 'Save projection assumptions' }))
  await screen.findByRole('heading', { name: 'Declared horizon: 2030-01-01' })
  const projection = await (await networkFetch(origin + base + '/projection')).json()
  expect(screen.getByLabelText('Life week grid').children).toHaveLength(projection.weeks_total)
  expect(screen.getByText(`${projection.elapsed_days} elapsed days · ${projection.remaining_days} projected remaining days`)).toBeInTheDocument()
  expect(projection.assumption).toBe('user_declared_horizon')
  expect(projection.budgets[0].remaining_hours).toBe(projection.remaining_days)
  fireEvent.click(screen.getByRole('button', { name: 'Check reminder now' }))
  await screen.findByText('Reminder saved to your local inbox.')
  const inbox = JSON.parse(readFileSync(join(home, 'inbox.json'), 'utf8'))
  expect(inbox.items).toHaveLength(1)
  expect(inbox.items[0].refs.href).toBe('#/capabilities/wellbeing/cognition')
  expect(inbox.items[0].can_reply).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Check reminder now' }))
  await screen.findByText('Reminder already exists for this local day.')
  expect(JSON.parse(readFileSync(join(home, 'inbox.json'), 'utf8')).items).toHaveLength(1)
  const cognitive = origin + '/api/capabilities/wellbeing/cognition/sessions'
  const started = await (await networkFetch(cognitive, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'ui-session', kind: 'color_word', planned_trials: 1, time_limit_seconds: 60 }) })).json()
  const complete = await networkFetch(cognitive + '/' + started.id + '/answers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'ui-answer', revision: 1, answer: started.current_trial.stimulus.color }) })
  expect((await complete.json()).status).toBe('completed')
  fireEvent.click(screen.getByRole('button', { name: 'Check reminder now' }))
  await screen.findByText('Practice completed today; reminder suppressed.')
  expect(JSON.parse(readFileSync(join(home, 'inbox.json'), 'utf8')).items).toHaveLength(1)
  expect(screen.getByRole('link', { name: 'Open local inbox' })).toHaveAttribute('href', '#/inbox')
  view.unmount()
  render(<LifeCalendar />)
  await screen.findByRole('heading', { name: 'Declared horizon: 2030-01-01' })
  expect(screen.getByLabelText('Birth date')).toHaveValue('2020-01-01')
  expect(screen.getByLabelText('Activity 1')).toHaveValue('Learning')
  expect(screen.getByLabelText('Enable daily completion reminder')).toBeChecked()
})

test('life events retain source and revisions and impossible budgets are rejected', async () => {
  open()
  await screen.findByRole('heading', { name: 'New life event' })
  change('Life event date', '2026-09-25')
  change('Life event title', 'Finished course')
  change('Life event source', 'personal journal')
  change('Life event notes', 'First milestone')
  fireEvent.click(screen.getByRole('button', { name: 'Save life event' }))
  fireEvent.click(await screen.findByRole('button', { name: '2026-09-25: Finished course · recorded' }))
  await screen.findByRole('heading', { name: 'Edit life event' })
  await screen.findByLabelText('Life event title')
  expect(screen.getByLabelText('Life event source')).toBeDisabled()
  expect(location.hash.split('?')[0]).toBe('#/capabilities/wellbeing/life')
  expect(new URLSearchParams(location.hash.split('?')[1]).get('shell')).toBe('retained')
  const identity = new URLSearchParams(location.hash.split('?')[1]).get('event')
  change('Life event title', 'Course completion')
  change('Life event kind', 'planned')
  fireEvent.click(screen.getByRole('button', { name: 'Save life event' }))
  fireEvent.click(await screen.findByRole('button', { name: '2026-09-25: Course completion · planned' }))
  await screen.findByText('Revision 2: Course completion · 2026-09-25')
  expect(screen.getByText('Revision 1: Finished course · 2026-09-25')).toBeInTheDocument()
  const history = await (await networkFetch(origin + base + '/events/' + identity + '/history')).json()
  expect(history.history).toHaveLength(2)
  expect(history.history[1].source).toBe('personal journal')
  expect(history.history[1].notes).toBe('First milestone')
  fireEvent.click(screen.getByLabelText('Delete life event (keep history)'))
  fireEvent.click(screen.getByRole('button', { name: 'Save life event' }))
  await screen.findByText('No life events.')
  expect(new URLSearchParams(location.hash.split('?')[1]).has('event')).toBe(false)
  const deleted = await (await networkFetch(origin + base + '/events/' + identity + '/history')).json()
  expect(deleted.history).toHaveLength(3)
  expect(deleted.history[2].deleted).toBe(true)
  change('Weekly hours 1', '150')
  fireEvent.click(screen.getByRole('button', { name: 'Save projection assumptions' }))
  expect((await screen.findByRole('alert')).textContent).toContain('exceed declared weekly waking hours')
  const config = await (await networkFetch(origin + base + '/config')).json()
  expect(config.budgets[0].hours_per_week).toBe(7)
  expect(config.revision).toBe(1)
})
