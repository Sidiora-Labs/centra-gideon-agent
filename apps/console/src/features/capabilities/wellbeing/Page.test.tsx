import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { HashRouter } from 'react-router-dom'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Page from './Page'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-wellbeing-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register
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

test('body entry and correction retain original history after reopening selection', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing')
  const view = render(<HashRouter><Page /></HashRouter>)
  await screen.findByText('No measurements in this date range.')
  fireEvent.change(screen.getByLabelText('Weight (kg)'), { target: { value: '74.2' } })
  fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'scale' } })
  fireEvent.change(screen.getByLabelText('Observed at'), { target: { value: '2026-09-25T09:00:00+02:00' } })
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Original entry' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save measurement' }))
  await screen.findByText('Correction history')
  expect(screen.getByText('Revision 1: 74.2 kg')).toBeInTheDocument()
  expect(screen.getByLabelText('Source')).toBeDisabled()
  expect(location.hash).toContain('?id=')
  fireEvent.change(screen.getByLabelText('Weight (kg)'), { target: { value: '74.8' } })
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Corrected transcription' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save correction' }))
  await screen.findByText('Revision 2: 74.8 kg')
  expect(screen.getByText('Original entry')).toBeInTheDocument()
  expect(screen.getByText('Corrected transcription')).toBeInTheDocument()
  const id = new URLSearchParams(location.hash.split('?')[1]).get('id')
  const stored = await (await networkFetch(`${origin}/api/capabilities/wellbeing/measurements/${id}`)).json()
  expect(stored.values).toEqual({ weight: 74.8 })
  expect(stored.source).toBe('scale')
  view.unmount()
  render(<HashRouter><Page /></HashRouter>)
  await screen.findByText('Revision 2: 74.8 kg')
  expect(screen.getByLabelText('Weight (kg)')).toHaveValue('74.8')
  const exported = await (await networkFetch(`${origin}/api/capabilities/wellbeing/export`)).json()
  expect(exported.measurements).toEqual([stored])
  expect(exported.history).toHaveLength(2)
  expect(exported.history[0].values.weight).toBe(74.2)
  fireEvent.change(screen.getByLabelText('From (timestamp with offset)'), { target: { value: '2099-01-01T00:00:00Z' } })
  fireEvent.click(screen.getByRole('button', { name: 'Filter dates' }))
  await screen.findByText('No measurements in this date range.')
  fireEvent.change(screen.getByLabelText('From (timestamp with offset)'), { target: { value: 'bad-date' } })
  fireEvent.click(screen.getByRole('button', { name: 'Filter dates' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Timestamp')
})

test('pressure controls reject invalid correction without losing persisted provenance', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing')
  render(<HashRouter><Page /></HashRouter>)
  await waitFor(() => expect(screen.queryByText('Loading measurements…')).not.toBeInTheDocument())
  fireEvent.change(screen.getByLabelText('Measurement kind'), { target: { value: 'blood_pressure' } })
  fireEvent.change(screen.getByLabelText('Systolic (mmHg)'), { target: { value: '124' } })
  fireEvent.change(screen.getByLabelText('Diastolic (mmHg)'), { target: { value: '82' } })
  fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'home cuff' } })
  fireEvent.change(screen.getByLabelText('Observed at'), { target: { value: '2026-09-24T21:00:00-04:00' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save measurement' }))
  await screen.findByText('Revision 1: 124/82 mmHg')
  expect(screen.getByLabelText('Source')).toBeDisabled()
  expect(screen.queryByLabelText('Measurement kind')).not.toBeInTheDocument()
  const id = new URLSearchParams(location.hash.split('?')[1]).get('id')
  const response = await networkFetch(`${origin}/api/capabilities/wellbeing/measurements/${id}`)
  expect(response.status).toBe(200)
  const original = await response.json()
  expect(original.source).toBe('home cuff')
  expect(original.values).toEqual({ systolic: 124, diastolic: 82 })
  expect(original.observed_at).toBe('2026-09-24T21:00:00-04:00')
  fireEvent.change(screen.getByLabelText('Diastolic (mmHg)'), { target: { value: '130' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save correction' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Diastolic')
  const history = await (await networkFetch(`${origin}/api/capabilities/wellbeing/measurements/${id}/history`)).json()
  expect(history.history).toEqual([original])
  fireEvent.change(screen.getByLabelText('Diastolic (mmHg)'), { target: { value: '79' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save correction' }))
  await screen.findByText('Revision 2: 124/79 mmHg')
  fireEvent.click(screen.getByRole('button', { name: 'New measurement' }))
  await screen.findByRole('button', { name: 'Save measurement' })
  expect(screen.getByLabelText('Source')).toBeEnabled()
  expect(location.hash).not.toContain('?id=')
  expect(screen.queryByText('Correction history')).not.toBeInTheDocument()
})
