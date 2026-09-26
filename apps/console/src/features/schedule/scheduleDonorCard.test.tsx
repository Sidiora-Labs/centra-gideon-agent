import { afterAll, beforeAll, expect, test } from 'vitest'
import { fireEvent, render, screen, waitFor, within, cleanup } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { useState } from 'react'
import { api, type ScheduleJob } from '../../shared/data/api'
import { RunHistory, ScheduleDetail } from './ScheduleDetail'

let server: ChildProcess
let home = ''
let baseUrl = ''
let job: ScheduleJob
const nativeFetch = globalThis.fetch

beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-schedule-card-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, time
from pathlib import Path
from aiohttp import web
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
from gideon.interfaces.dashboard.handlers.triggers import register_trigger_routes
from gideon.interfaces.dashboard.state import ConsoleState

async def main():
 home = Path(__import__('os').environ['GIDEON_HOME'])
 TriggerStore(base_dir=home).upsert(Trigger(id='daily-review', name='Daily review', kind='clock', enabled=True,
  spec={'kind':'interval','interval_secs':3600},
  workflow={'inline':{'provider':'invoke-agent','config':{'task_template':'Review recent work'}}}))
 journal = ExecutionJournal(home)
 now = time.time()
 await journal.append(ExecutionRecord(run_id='run-good',job_id='daily-review',trigger='scheduled',started_at=now-60,
  finished_at=now-55,duration_ms=5000,status='success',summary='Review finished'))
 await journal.append(ExecutionRecord(run_id='run-bad',job_id='daily-review',trigger='scheduled',started_at=now-120,
  finished_at=now-115,duration_ms=5000,status='failure',error='Provider unavailable'))
 app=web.Application(); app['state']=ConsoleState(None,now); register_trigger_routes(app)
 runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
 print(site._server.sockets[0].getsockname()[1],flush=True); await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  baseUrl = await new Promise<string>((accept, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => accept(`http://127.0.0.1:${String(data).trim()}`))
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  globalThis.fetch = (input, init) => nativeFetch(new URL(String(input), baseUrl), init)
  const schedules = await api.schedules()
  job = schedules.jobs.find(row => row.id === 'daily-review')!
  expect(job).toBeDefined()
})

afterAll(async () => {
  cleanup()
  globalThis.fetch = nativeFetch
  if (server?.exitCode === null && server.signalCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

function LiveDetail({ initial }: { initial: ScheduleJob }) {
  const [current, setCurrent] = useState(initial)
  const [editing, setEditing] = useState(false)
  const [deleted, setDeleted] = useState(false)
  async function refresh() {
    const schedules = await api.schedules()
    setCurrent(schedules.jobs.find(row => row.id === initial.id)!)
  }
  return deleted ? <p>Deleted</p> : <ScheduleDetail job={current} editing={editing} onEditingChange={setEditing}
    onSaved={() => { void refresh() }} onChanged={() => { void refresh() }} onDeleted={() => setDeleted(true)} />
}

test('live detail shows persisted run outcomes in one donor card and keeps its native actions', async () => {
  const view = render(<LiveDetail initial={job} />)
  const card = await screen.findByLabelText('Schedule daily-review')
  expect(card.querySelector('[data-slot="schedule-card"]')).toBeInTheDocument()
  expect(within(card).getByText(job.schedule)).toBeInTheDocument()
  expect(within(card).getByText('ok')).toBeInTheDocument()
  expect(within(card).getByText('failed')).toBeInTheDocument()
  expect(screen.getByText('History · 2')).toBeInTheDocument()
  expect(screen.getByText('Provider unavailable')).toBeInTheDocument()
  expect(screen.queryByRole('list', { name: 'Schedule run errors' })).toBeNull()
  expect(screen.getAllByRole('switch')).toHaveLength(1)
  expect(screen.getByRole('switch', { name: 'Toggle enabled' })).toHaveAttribute('aria-checked', 'true')
  for (const name of ['Run now', 'Dry run', 'Edit', 'Delete']) expect(screen.getByRole('button', { name })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('switch', { name: 'Toggle enabled' }))
  await waitFor(() => expect(screen.getByRole('switch', { name: 'Toggle enabled' })).toHaveAttribute('aria-checked', 'false'))
  const persisted = await api.schedules()
  expect(persisted.jobs.find(row => row.id === job.id)?.enabled).toBe(false)
  expect(within(card).getByText('Paused')).toBeInTheDocument()
  view.unmount()
})

test('a real history transport failure is reported as unavailable', async () => {
  await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  render(<RunHistory triggerId="schedule:daily-review" />)
  expect(await screen.findByText('Run history unavailable.')).toBeInTheDocument()
  expect(screen.queryByText('No runs recorded yet.')).toBeNull()
})
