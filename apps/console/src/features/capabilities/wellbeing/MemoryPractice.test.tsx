import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import MemoryPractice from './MemoryPractice'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-memory-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_memory import register
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

const base = '/api/capabilities/wellbeing/memory/cards'
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const open = () => { window.history.replaceState(null, '', '#/capabilities/wellbeing/memory?shell=retained'); return render(<MemoryPractice />) }

test('canonical memory card reveal and self-grade persist schedule and reopen', async () => {
  const view = open()
  await screen.findByText('No matching memory cards.')
  change('Memory prompt', 'Capital of France?')
  change('Memory answer', 'Paris')
  change('Memory source', 'personal notes')
  change('Tags (comma separated)', 'geography, practice')
  fireEvent.click(screen.getByRole('button', { name: 'Save memory card' }))
  await screen.findByRole('heading', { name: 'Recall prompt' })
  expect(screen.queryByText('Paris', { exact: true })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'good', exact: true })).not.toBeInTheDocument()
  expect(screen.getByText('Source: personal notes · geography, practice')).toBeInTheDocument()
  expect(screen.getByText('Interval: 0 days · Successful repetitions: 0 · Lapses: 0')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Reveal memory answer' }))
  expect(await screen.findByText('Paris', { exact: true })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'good', exact: true }))
  await screen.findByText('Interval: 1 days · Successful repetitions: 1 · Lapses: 0')
  expect(screen.queryByText('Paris', { exact: true })).not.toBeInTheDocument()
  expect(screen.getByText(/Revision 2: Self-grade good/)).toBeInTheDocument()
  const identity = new URLSearchParams(location.hash.split('?')[1]).get('card')
  expect(identity).toBeTruthy()
  expect(location.hash.split('?')[0]).toBe('#/capabilities/wellbeing/memory')
  expect(new URLSearchParams(location.hash.split('?')[1]).get('shell')).toBe('retained')
  const row = await (await networkFetch(origin + base + '/' + identity)).json()
  expect(row.front).toBe('Capital of France?')
  expect(row.back).toBe('Paris')
  expect(row.source).toBe('personal notes')
  expect(row.schedule.interval_days).toBe(1)
  expect(row.schedule.rules_version).toBe(1)
  expect(row.practice.grade).toBe('good')
  const history = await (await networkFetch(origin + base + '/' + identity + '/history')).json()
  expect(history.history).toHaveLength(2)
  expect(history.history[0].artifact).toEqual(history.history[1].artifact)
  view.unmount()
  render(<MemoryPractice />)
  await screen.findByText('Interval: 1 days · Successful repetitions: 1 · Lapses: 0')
  expect(screen.queryByText('Paris', { exact: true })).not.toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('Due cards only'))
  await screen.findByText('No matching memory cards.')
  expect(screen.getByRole('heading', { name: 'Recall prompt' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Reveal memory answer' }))
  fireEvent.click(await screen.findByRole('button', { name: 'again', exact: true }))
  await screen.findByText('Interval: 0 days · Successful repetitions: 0 · Lapses: 1')
  const lapse = await (await networkFetch(origin + base + '/' + identity)).json()
  expect(new Date(lapse.schedule.due_at).getTime() - new Date(lapse.schedule.last_practiced_at).getTime()).toBe(600000)
  expect(lapse.revision).toBe(3)
})

test('content editing pins original artifact and archive preserves practice history', async () => {
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Capital of France?' }))
  await screen.findByRole('heading', { name: 'Recall prompt' })
  fireEvent.click(screen.getByRole('button', { name: 'Edit selected card' }))
  await screen.findByRole('heading', { name: 'Edit memory card' })
  expect(screen.getByLabelText('Memory source')).toBeDisabled()
  expect(screen.getByLabelText('Memory answer')).toHaveValue('Paris')
  change('Memory answer', 'Paris, France')
  fireEvent.click(screen.getByRole('button', { name: 'Save memory card' }))
  await screen.findByRole('heading', { name: 'Recall prompt' })
  expect(new URLSearchParams(location.hash.split('?')[1]).has('mode')).toBe(false)
  expect(screen.queryByText('Paris, France', { exact: true })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Reveal memory answer' }))
  expect(await screen.findByText('Paris, France', { exact: true })).toBeInTheDocument()
  const identity = new URLSearchParams(location.hash.split('?')[1]).get('card')
  const history = await (await networkFetch(origin + base + '/' + identity + '/history')).json()
  expect(history.history).toHaveLength(4)
  expect(history.history[0].back).toBe('Paris')
  expect(history.history[3].back).toBe('Paris, France')
  expect(history.history[3].artifact.slug).not.toBe(history.history[0].artifact.slug)
  expect(history.history[3].schedule).toEqual(history.history[2].schedule)
  fireEvent.click(screen.getByRole('button', { name: 'Edit selected card' }))
  await screen.findByRole('heading', { name: 'Edit memory card' })
  fireEvent.click(screen.getByLabelText('Archived memory card'))
  fireEvent.click(screen.getByRole('button', { name: 'Save memory card' }))
  await screen.findByText('This card is archived.')
  expect(screen.getByText('No matching memory cards.')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Reveal memory answer' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('Include archived memory cards'))
  await screen.findByRole('button', { name: 'Capital of France? (archived)' })
  const archived = await (await networkFetch(origin + base + '/' + identity)).json()
  expect(archived.archived).toBe(true)
  expect(archived.revision).toBe(5)
  expect(archived.source).toBe('personal notes')
  expect(archived.schedule.lapses).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: 'New memory card' }))
  await screen.findByRole('heading', { name: 'New memory card' })
  expect(new URLSearchParams(location.hash.split('?')[1]).has('card')).toBe(false)
  expect(new URLSearchParams(location.hash.split('?')[1]).get('shell')).toBe('retained')
})
