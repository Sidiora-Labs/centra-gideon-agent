import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import SharedHealth from './SharedHealth'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-shared-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_shared import register
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register as measurements
async def main():
 app = web.Application()
 register(app, Path(os.environ['GIDEON_HOME']))
 measurements(app, Path(os.environ['GIDEON_HOME']))
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
const base = '/api/capabilities/wellbeing/shared'
const source = { schema: 'gideon.wellbeing-shared', version: 1, store_id: '11111111-1111-4111-8111-111111111111', records: [{ id: '22222222-2222-4222-8222-222222222222', revision: 1, kind: 'body_weight', observed_at: '2026-09-25T09:00:00Z', unit: 'kg', values: { weight: 72 }, source: 'native scale', notes: 'first reading' }] }

test('native document import and actual shared file correction preserve canonical history after reload', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/shared?shell=retained')
  let view = render(<SharedHealth />)
  await screen.findByText('State: missing')
  expect(screen.getByRole('button', { name: 'Preview local shared file' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Preview uploaded document' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Native document JSON'), { target: { value: JSON.stringify(source) } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview uploaded document' }))
  await screen.findByText('1 new · 0 corrections · 0 unchanged')
  expect(await (await networkFetch(origin + '/api/capabilities/wellbeing/measurements')).json()).toEqual({ measurements: [] })
  fireEvent.click(screen.getByRole('button', { name: 'Commit shared import' }))
  await screen.findByText('Imported 1 new and 0 corrected records; 0 unchanged.')
  fireEvent.click(screen.getByRole('button', { name: 'Publish canonical measurements' }))
  await screen.findByText('Published 1 canonical measurements.')
  expect(await screen.findByText('State: ready')).toBeInTheDocument()
  const download = screen.getByRole('link', { name: 'Download shared JSON' })
  const response = await networkFetch(origin + download.getAttribute('href'))
  expect(response.status).toBe(200)
  const native = await response.json()
  expect(native.records[0].values.weight).toBe(72)
  const path = join(home, 'shared', 'wellbeing.json')
  expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual(native)
  native.records[0].revision = 2
  native.records[0].values.weight = 73
  native.records[0].notes = 'native client correction'
  writeFileSync(path, JSON.stringify(native))
  fireEvent.click(screen.getByRole('button', { name: 'Preview local shared file' }))
  await screen.findByText('0 new · 1 corrections · 0 unchanged')
  fireEvent.click(screen.getByRole('button', { name: 'Commit shared import' }))
  await screen.findByText('Imported 0 new and 1 corrected records; 0 unchanged.')
  const records = await (await networkFetch(origin + '/api/capabilities/wellbeing/measurements')).json()
  expect(records.measurements).toHaveLength(1)
  expect(records.measurements[0].values.weight).toBe(73)
  const history = await (await networkFetch(origin + '/api/capabilities/wellbeing/measurements/' + records.measurements[0].id + '/history')).json()
  expect(history.history).toHaveLength(2)
  expect(history.history[0].values.weight).toBe(72)
  expect(history.history[1].notes).toBe('native client correction')
  view.unmount()
  view = render(<SharedHealth />)
  await screen.findByText('State: ready')
  fireEvent.click(screen.getByRole('button', { name: 'Preview local shared file' }))
  await screen.findByText('0 new · 0 corrections · 1 unchanged')
  expect(location.hash).toBe('#/capabilities/wellbeing/shared?shell=retained')
  view.unmount()
})

test('corrupt native file disables publication and malformed upload preserves records', async () => {
  const path = join(home, 'shared', 'wellbeing.json')
  writeFileSync(path, '{corrupt native file')
  const view = render(<SharedHealth />)
  await screen.findByText('State: invalid')
  expect(screen.getByRole('button', { name: 'Publish canonical measurements' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Preview local shared file' })).toBeDisabled()
  expect(screen.queryByRole('link', { name: 'Download shared JSON' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Native document JSON'), { target: { value: '{}' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview uploaded document' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Unsupported shared health schema')
  expect(screen.queryByRole('button', { name: 'Commit shared import' })).not.toBeInTheDocument()
  expect(readFileSync(path, 'utf8')).toBe('{corrupt native file')
  const records = await (await networkFetch(origin + '/api/capabilities/wellbeing/measurements')).json()
  expect(records.measurements).toHaveLength(1)
  expect(records.measurements[0].values.weight).toBe(73)
  view.unmount()
})
