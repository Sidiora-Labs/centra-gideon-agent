import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Privacy from './Privacy'

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
  home = mkdtempSync(join(tmpdir(), 'gideon-privacy-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os, json
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_privacy import register
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register as measurements
async def main():
 app = web.Application(middlewares=[token_auth_middleware()])
 register(app, Path(os.environ['GIDEON_HOME']))
 measurements(app, Path(os.environ['GIDEON_HOME']))
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
const base = '/api/capabilities/wellbeing/privacy'
const password = 'private UI passphrase not stored'
const secret = 'ui-person-81276@example.test'
const change = async (label: string, value: string) => fireEvent.change(await screen.findByLabelText(label), { target: { value } })

test('owner grants scoped consent, creates encrypted fact and explicitly reveals, corrects, revokes and reloads', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/privacy?shell=retained')
  let view = render(<Privacy />)
  await waitFor(() => expect(screen.queryByText('Loading private records…')).not.toBeInTheDocument())
  await change('Subject alias', 'Self alias')
  await change('Subject source', 'owner supplied')
  fireEvent.click(screen.getByRole('button', { name: 'Create privacy subject' }))
  await screen.findByRole('heading', { name: 'Explicit scoped consent' })
  expect(location.hash).toContain('subject=')
  expect(location.hash).toContain('shell=retained')
  await change('Consent method', 'Owner confirmed at console')
  fireEvent.click(screen.getByRole('button', { name: 'Record consent decision' }))
  await screen.findByText('vault revision 1: granted · Owner confirmed at console')
  await change('Fact label', 'Personal email')
  await change('Fact source', 'owner supplied')
  await change('Private value', secret)
  await change('Encryption passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Save encrypted fact' }))
  await screen.findByRole('heading', { name: 'Correct selected private fact' })
  expect(screen.getByRole('button', { name: 'Personal email · •••• · revision 1' })).toBeInTheDocument()
  expect(screen.getByLabelText('Private value')).toHaveValue('')
  expect(screen.getByLabelText('Encryption passphrase')).toHaveValue('')
  expect(screen.queryByText(secret)).not.toBeInTheDocument()
  const identity = new URLSearchParams(location.hash.split('?')[1]).get('fact')!
  const subjectId = new URLSearchParams(location.hash.split('?')[1]).get('subject')!
  const metadata = await (await ownerFetch(origin + base + '/facts/' + identity)).text()
  expect(metadata).not.toContain(secret)
  expect(metadata).not.toContain(password)
  const bytes = readFileSync(join(home, 'capabilities', 'privacy.sqlite3'))
  expect(bytes.includes(Buffer.from(secret))).toBe(false)
  expect(bytes.includes(Buffer.from(password))).toBe(false)
  await change('Reveal passphrase', password)
  await change('Reveal reason', 'Verify saved contact')
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('consent required')
  expect(screen.getByLabelText('Reveal passphrase')).toHaveValue('')
  await change('Consent scope', 'reveal')
  fireEvent.click(screen.getByRole('button', { name: 'Record consent decision' }))
  await screen.findByText('reveal revision 1: granted · Owner confirmed at console')
  await change('Reveal passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByText(secret)).toBeInTheDocument()
  await waitFor(() => expect(screen.getByLabelText('Reveal passphrase')).toHaveValue(''))
  fireEvent.click(screen.getByRole('button', { name: 'Hide revealed value' }))
  expect(screen.queryByText(secret)).not.toBeInTheDocument()
  await change('Private value', 'revised-private-712@example.test')
  await change('Encryption passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Save encrypted fact' }))
  await screen.findByRole('button', { name: 'Personal email · •••• · revision 2' })
  expect(screen.getByText('Revision 1: Personal email · ••••')).toBeInTheDocument()
  expect(screen.getByText('Revision 2: Personal email · ••••')).toBeInTheDocument()
  await change('Reveal passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByText('revised-private-712@example.test')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Record consent decision' })).toBeEnabled())
  fireEvent.click(screen.getByLabelText('Grant consent'))
  fireEvent.click(screen.getByRole('button', { name: 'Record consent decision' }))
  await screen.findByText('reveal revision 2: revoked · Owner confirmed at console')
  expect(screen.queryByText('revised-private-712@example.test')).not.toBeInTheDocument()
  await change('Reveal passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('consent required')
  view.unmount()
  view = render(<Privacy />)
  await screen.findByRole('heading', { name: 'Correct selected private fact' })
  expect(screen.queryByText('revised-private-712@example.test')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Reveal passphrase')).toHaveValue('')
  const events = await (await ownerFetch(origin + base + '/subjects/' + subjectId + '/audit')).json()
  expect(events.audit.filter((event: { operation: string }) => event.operation === 'fact_reveal')).toHaveLength(4)
  expect(JSON.stringify(events)).not.toContain(secret)
  expect(JSON.stringify(events)).not.toContain(password)
  view.unmount()
})

test('wrong key refuses correction and clears secret input without persisting or revealing replacement', async () => {
  const view = render(<Privacy />)
  await screen.findByRole('heading', { name: 'Correct selected private fact' })
  await change('Private value', 'should-never-persist@example.test')
  await change('Encryption passphrase', 'wrong but sufficiently long password')
  fireEvent.click(screen.getByRole('button', { name: 'Save encrypted fact' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('authenticated')
  expect(screen.getByLabelText('Private value')).toHaveValue('')
  expect(screen.getByLabelText('Encryption passphrase')).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Personal email · •••• · revision 2' })).toBeInTheDocument()
  const bytes = readFileSync(join(home, 'capabilities', 'privacy.sqlite3'))
  expect(bytes.includes(Buffer.from('should-never-persist@example.test'))).toBe(false)
  expect(bytes.includes(Buffer.from('wrong but sufficiently long password'))).toBe(false)
  expect(screen.queryByLabelText('Revealed private value')).not.toBeInTheDocument()
  window.location.hash = '#/capabilities/wellbeing/privacy?shell=retained'
  await waitFor(() => expect(screen.queryByRole('heading', { name: 'Correct selected private fact' })).not.toBeInTheDocument())
  expect(screen.queryByRole('button', { name: 'Reveal selected fact' })).not.toBeInTheDocument()
  view.unmount()
})
