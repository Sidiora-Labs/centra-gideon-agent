import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Exports from './Exports'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-exports-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_exports import register
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
const base = '/api/capabilities/wellbeing/exports'

test('create durable export and download exact immutable JSON after records change and hash reload', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/exports?shell=retained')
  let view = render(<Exports />)
  await screen.findByText('No saved exports.')
  expect(screen.getByText('Schema gideon.wellbeing-export · version 1')).toBeInTheDocument()
  expect(screen.getAllByText('0 history and provenance records')).toHaveLength(9)
  fireEvent.click(screen.getByRole('button', { name: 'Create immutable export' }))
  const link = await screen.findByRole('link', { name: 'Download selected JSON export' })
  const href = link.getAttribute('href')!
  const firstResponse = await networkFetch(origin + href)
  expect(firstResponse.status).toBe(200)
  expect(firstResponse.headers.get('Content-Disposition')).toContain('attachment')
  const firstRaw = await firstResponse.text()
  const first = JSON.parse(firstRaw)
  expect(first.counts.measurements).toBe(0)
  expect(first.schema).toBe('gideon.wellbeing-export')
  expect(first.omitted).toContain('external_trigger_definitions')
  expect(location.hash).toContain('shell=retained')
  expect(location.hash).toContain('export=')
  expect(location.hash.split('?')[0]).toBe('#/capabilities/wellbeing/exports')
  const firstHash = location.hash
  const response = await networkFetch(origin + '/api/capabilities/wellbeing/measurements', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'ui-weight', kind: 'body_weight', observed_at: '2026-09-25T09:00:00Z', unit: 'kg', values: { weight: 72 }, source: 'user scale', notes: '' }) })
  expect(response.status).toBe(200)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh export preview' }))
  await screen.findByText('1 history and provenance records')
  expect(await (await networkFetch(origin + href)).text()).toBe(firstRaw)
  fireEvent.click(screen.getByRole('button', { name: 'Create immutable export' }))
  await screen.findByText(/SHA-256:/)
  const list = await (await networkFetch(origin + base)).json()
  expect(list.exports).toHaveLength(2)
  const newest = JSON.parse(await (await networkFetch(origin + base + '/' + list.exports[0].id + '/download')).text())
  expect(newest.counts.measurements).toBe(1)
  expect(newest.sections.measurements.history[0].values.weight).toBe(72)
  view.unmount()
  window.history.replaceState(null, '', firstHash)
  view = render(<Exports />)
  expect((await screen.findByRole('link', { name: 'Download selected JSON export' })).getAttribute('href')).toBe(href)
  expect(await (await networkFetch(origin + href)).text()).toBe(firstRaw)
  view.unmount()
})

test('external hash navigation selects prior snapshots and unknown archive exposes real error', async () => {
  const list = await (await networkFetch(origin + base)).json()
  window.history.replaceState(null, '', '#/capabilities/wellbeing/exports?shell=retained')
  const view = render(<Exports />)
  await screen.findByRole('heading', { name: 'Current snapshot preview' })
  expect(screen.queryByRole('link', { name: 'Download selected JSON export' })).not.toBeInTheDocument()
  window.location.hash = '#/capabilities/wellbeing/exports?shell=retained&export=' + list.exports[0].id
  const link = await screen.findByRole('link', { name: 'Download selected JSON export' })
  expect(link.getAttribute('href')).toBe(base + '/' + list.exports[0].id + '/download')
  window.location.hash = '#/capabilities/wellbeing/exports?shell=retained&export=missing'
  expect(await screen.findByRole('alert')).toHaveTextContent('not found')
  expect(screen.queryByRole('link', { name: 'Download selected JSON export' })).not.toBeInTheDocument()
  view.unmount()
})
