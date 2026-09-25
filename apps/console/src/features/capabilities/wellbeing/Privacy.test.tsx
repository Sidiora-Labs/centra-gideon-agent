import { afterAll, beforeAll, expect, test } from 'vitest'
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
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-privacy-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_privacy import register
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
const base = '/api/capabilities/wellbeing/privacy'
const password = 'private UI passphrase not stored'
const secret = 'ui-person-81276@example.test'
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

test('owner grants scoped consent, creates encrypted fact and explicitly reveals, corrects, revokes and reloads', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/privacy?shell=retained')
  let view = render(<Privacy />)
  await waitFor(() => expect(screen.queryByText('Loading private records…')).not.toBeInTheDocument())
  change('Subject alias', 'Self alias')
  change('Subject source', 'owner supplied')
  fireEvent.click(screen.getByRole('button', { name: 'Create privacy subject' }))
  await screen.findByRole('heading', { name: 'Explicit scoped consent' })
  expect(location.hash).toContain('subject=')
  expect(location.hash).toContain('shell=retained')
  change('Consent method', 'Owner confirmed at console')
  fireEvent.click(screen.getByRole('button', { name: 'Record consent decision' }))
  await screen.findByText('vault revision 1: granted · Owner confirmed at console')
  change('Fact label', 'Personal email')
  change('Fact source', 'owner supplied')
  change('Private value', secret)
  change('Encryption passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Save encrypted fact' }))
  await screen.findByRole('heading', { name: 'Correct selected private fact' })
  expect(screen.getByRole('button', { name: 'Personal email · •••• · revision 1' })).toBeInTheDocument()
  expect(screen.getByLabelText('Private value')).toHaveValue('')
  expect(screen.getByLabelText('Encryption passphrase')).toHaveValue('')
  expect(screen.queryByText(secret)).not.toBeInTheDocument()
  const identity = new URLSearchParams(location.hash.split('?')[1]).get('fact')!
  const subjectId = new URLSearchParams(location.hash.split('?')[1]).get('subject')!
  const metadata = await (await networkFetch(origin + base + '/facts/' + identity)).text()
  expect(metadata).not.toContain(secret)
  expect(metadata).not.toContain(password)
  const bytes = readFileSync(join(home, 'capabilities', 'privacy.sqlite3'))
  expect(bytes.includes(Buffer.from(secret))).toBe(false)
  expect(bytes.includes(Buffer.from(password))).toBe(false)
  change('Reveal passphrase', password)
  change('Reveal reason', 'Verify saved contact')
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('consent required')
  expect(screen.getByLabelText('Reveal passphrase')).toHaveValue('')
  change('Consent scope', 'reveal')
  fireEvent.click(screen.getByRole('button', { name: 'Record consent decision' }))
  await screen.findByText('reveal revision 1: granted · Owner confirmed at console')
  change('Reveal passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByText(secret)).toBeInTheDocument()
  await waitFor(() => expect(screen.getByLabelText('Reveal passphrase')).toHaveValue(''))
  fireEvent.click(screen.getByRole('button', { name: 'Hide revealed value' }))
  expect(screen.queryByText(secret)).not.toBeInTheDocument()
  change('Private value', 'revised-private-712@example.test')
  change('Encryption passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Save encrypted fact' }))
  await screen.findByRole('button', { name: 'Personal email · •••• · revision 2' })
  expect(screen.getByText('Revision 1: Personal email · ••••')).toBeInTheDocument()
  expect(screen.getByText('Revision 2: Personal email · ••••')).toBeInTheDocument()
  change('Reveal passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByText('revised-private-712@example.test')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Record consent decision' })).toBeEnabled())
  fireEvent.click(screen.getByLabelText('Grant consent'))
  fireEvent.click(screen.getByRole('button', { name: 'Record consent decision' }))
  await screen.findByText('reveal revision 2: revoked · Owner confirmed at console')
  expect(screen.queryByText('revised-private-712@example.test')).not.toBeInTheDocument()
  change('Reveal passphrase', password)
  fireEvent.click(screen.getByRole('button', { name: 'Reveal selected fact' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('consent required')
  view.unmount()
  view = render(<Privacy />)
  await screen.findByRole('heading', { name: 'Correct selected private fact' })
  expect(screen.queryByText('revised-private-712@example.test')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Reveal passphrase')).toHaveValue('')
  const events = await (await networkFetch(origin + base + '/subjects/' + subjectId + '/audit')).json()
  expect(events.audit.filter((event: { operation: string }) => event.operation === 'fact_reveal')).toHaveLength(4)
  expect(JSON.stringify(events)).not.toContain(secret)
  expect(JSON.stringify(events)).not.toContain(password)
  view.unmount()
})

test('wrong key refuses correction and clears secret input without persisting or revealing replacement', async () => {
  const view = render(<Privacy />)
  await screen.findByRole('heading', { name: 'Correct selected private fact' })
  change('Private value', 'should-never-persist@example.test')
  change('Encryption passphrase', 'wrong but sufficiently long password')
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
