import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Page from './Page'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
let ownerToken = ''
function ownerFetch(input: RequestInfo | URL, init?: RequestInit) {
  const target = input instanceof Request ? input : new URL(String(input), origin)
  const url = target instanceof Request ? target.url : String(target)
  const headers = new Headers(target instanceof Request ? target.headers : undefined)
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${ownerToken}`)
  return networkFetch(target, { ...init, headers })
}
afterEach(cleanup)
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-wellbeing-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os, json
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register
async def main():
 app = web.Application(middlewares=[token_auth_middleware()])
 register(app, Path(os.environ['GIDEON_HOME']))
 runner = web.AppRunner(app)
 await runner.setup()
 site = web.TCPSite(runner, '127.0.0.1', 0)
 await site.start()
 print(json.dumps({'port':site._server.sockets[0].getsockname()[1],'token':generate_token('wellbeing-ui-owner')}),flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => { const ready = JSON.parse(String(data)); ownerToken = ready.token; resolveOrigin(`http://127.0.0.1:${ready.port}`) })
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  const refused = await networkFetch(origin + '/api/capabilities/wellbeing/exports')
  expect(refused.status).toBe(403)
  expect(ownerToken).toBeTruthy()
  globalThis.fetch = ownerFetch
})
afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  if (server?.exitCode === null) await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

test('body entry and correction retain original history after reopening selection', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing?shell=retained')
  const view = render(<Page />)
  await screen.findByText('No measurements')
  fireEvent.change(screen.getByLabelText('Body weight'), { target: { value: '74.2' } })
  fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'scale' } })
  fireEvent.change(screen.getByLabelText('Observed at (with timezone)'), { target: { value: '2026-09-25T09:00:00+02:00' } })
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Original entry' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await screen.findByText('History')
  expect(screen.getByText(/Revision 1 · .*74.2 kg/)).toBeInTheDocument()
  expectReadonlySource(screen.getByLabelText('Source'))
  expect(new URLSearchParams(location.hash.split('?')[1]).get('id')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Body weight'), { target: { value: '74.8' } })
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Corrected transcription' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await screen.findByText(/Revision 2 · .*74.8 kg/)
  expect(screen.getByText(/Revision 1 · .*Original entry/)).toBeInTheDocument()
  expect(screen.getByText(/Revision 2 · .*Corrected transcription/)).toBeInTheDocument()
  const id = new URLSearchParams(location.hash.split('?')[1]).get('id')
  const stored = await (await ownerFetch(`${origin}/api/capabilities/wellbeing/measurements/${id}`)).json()
  expect(stored.values).toEqual({ weight: 74.8 })
  expect(stored.source).toBe('scale')
  view.unmount()
  render(<Page />)
  await screen.findByText(/Revision 2 · .*74.8 kg/)
  expect(screen.getByLabelText('Body weight')).toHaveValue(74.8)
  expect(location.hash.split('?')[0]).toBe('#/capabilities/wellbeing')
  expect(new URLSearchParams(location.hash.split('?')[1]).get('shell')).toBe('retained')
  const exported = await (await ownerFetch(`${origin}/api/capabilities/wellbeing/export`)).json()
  expect(exported.measurements).toEqual([stored])
  expect(exported.history).toHaveLength(2)
  expect(exported.history[0].values.weight).toBe(74.2)
  fireEvent.click(screen.getByRole('button', { name: 'New measurement' }))
  await screen.findByRole('combobox', { name: 'Measurements' })
  expect(new URLSearchParams(location.hash.split('?')[1]).has('id')).toBe(false)
  expect(new URLSearchParams(location.hash.split('?')[1]).get('shell')).toBe('retained')
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2099-01-01T00:00:00Z' } })
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
  await screen.findByText('No measurements')
  fireEvent.change(screen.getByLabelText('From'), { target: { value: 'bad-date' } })
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Timestamp')
})

test('pressure controls reject invalid correction without losing persisted provenance', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing?shell=retained')
  render(<Page />)
  await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
  fireEvent.change(screen.getByRole('combobox', { name: 'Measurements' }), { target: { value: 'blood_pressure' } })
  fireEvent.change(screen.getByLabelText('Systolic'), { target: { value: '124' } })
  fireEvent.change(screen.getByLabelText('Diastolic'), { target: { value: '82' } })
  fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'home cuff' } })
  fireEvent.change(screen.getByLabelText('Observed at (with timezone)'), { target: { value: '2026-09-24T21:00:00-04:00' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await screen.findByText(/Revision 1 · .*124\/82 mmHg/)
  expectReadonlySource(screen.getByLabelText('Source'))
  const kind = screen.getByRole('combobox', { name: 'Measurements' })
  expect(kind).toHaveAttribute('aria-readonly', 'true')
  expect(kind).not.toBeDisabled()
  kind.focus()
  expect(document.activeElement).toBe(kind)
  const kindReason = kind.getAttribute('aria-describedby')!.split(' ').map(id => document.getElementById(id)?.textContent).join(' ')
  expect(kindReason).toContain('Create a new measurement to change its kind')
  expect(fireEvent.keyDown(kind, { key: 'ArrowDown' })).toBe(false)
  fireEvent.change(kind, { target: { value: 'body_weight' } })
  expect(kind).toHaveValue('blood_pressure')
  const id = new URLSearchParams(location.hash.split('?')[1]).get('id')
  const response = await ownerFetch(`${origin}/api/capabilities/wellbeing/measurements/${id}`)
  expect(response.status).toBe(200)
  const original = await response.json()
  expect(original.source).toBe('home cuff')
  expect(original.values).toEqual({ systolic: 124, diastolic: 82 })
  expect(original.observed_at).toBe('2026-09-24T21:00:00-04:00')
  fireEvent.change(screen.getByLabelText('Diastolic'), { target: { value: '130' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Diastolic')
  const history = await (await ownerFetch(`${origin}/api/capabilities/wellbeing/measurements/${id}/history`)).json()
  expect(history.history).toEqual([original])
  fireEvent.change(screen.getByLabelText('Diastolic'), { target: { value: '79' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await screen.findByText(/Revision 2 · .*124\/79 mmHg/)
  fireEvent.click(screen.getByRole('button', { name: 'New measurement' }))
  await screen.findByRole('button', { name: 'Save' })
  expect(screen.getByLabelText('Source')).toBeEnabled()
  expect(location.hash).not.toContain('?id=')
  expect(screen.queryByText('History')).not.toBeInTheDocument()
})

test('opens the shared privacy surface that contains broker provider workflows', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing')
  render(<Page />)
  await screen.findByRole('button', { name: 'Privacy' })
  fireEvent.click(screen.getByRole('button', { name: 'Privacy' }))
  expect(await screen.findByRole('heading', { name: 'Private identity facts' })).toBeInTheDocument()
  expect(location.hash).toContain('view=privacy')
})

function expectReadonlySource(control: HTMLElement) {
  expect(control).toHaveAttribute('readonly')
  expect(control).not.toBeDisabled()
  control.focus()
  expect(document.activeElement).toBe(control)
  const descriptionIds = control.getAttribute('aria-describedby')?.split(' ') ?? []
  expect(descriptionIds.length).toBeGreaterThan(0)
  for (const id of descriptionIds) expect(document.getElementById(id)?.textContent?.trim()).toBeTruthy()
  const original = (control as HTMLInputElement).value
  fireEvent.change(control, { target: { value: 'Attempt to replace immutable provenance' } })
  expect(control).toHaveValue(original)
}
