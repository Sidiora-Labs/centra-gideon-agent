import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import PrivacyOrganizations, { type PrivateFactMetadata } from './PrivacyOrganizations'

let server: ChildProcess
let origin = ''
let home = ''
let subject = ''
let firstFact: PrivateFactMetadata
let currentFact: PrivateFactMetadata
const networkFetch = globalThis.fetch
const password = 'organization UI passphrase retained nowhere'
const oldSecret = 'old-ui-private-331@example.test'
const newSecret = 'new-ui-private-332@example.test'
const base = '/api/capabilities/wellbeing/privacy'

async function json(path: string, method = 'GET', body?: unknown) {
  const response = await networkFetch(origin + path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
  return response.json()
}

beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-holdings-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_privacy import register
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
  origin = await new Promise<string>((accept, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => accept(`http://127.0.0.1:${String(data).trim()}`))
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  globalThis.fetch = (input, init) => networkFetch(new URL(String(input), origin), init)
  const person = await json(base + '/subjects', 'POST', { request_id: 'subject', alias: 'Owner alias', relationship: 'self', source: 'owner' })
  subject = person.id
  await json(`${base}/subjects/${subject}/consents`, 'POST', { request_id: 'vault', revision: 0, scope: 'vault', granted: true, method: 'owner confirmed' })
  firstFact = await json(`${base}/subjects/${subject}/facts`, 'POST', { request_id: 'fact', type: 'email', label: 'Primary email', value: oldSecret, passphrase: password, source: 'owner', use_for_scans: false })
})

afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

test('owner records organization and holding then works changed-fact checklist with evidence and history', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/privacy?subject=' + subject + '&shell=retained')
  const view = render(<PrivacyOrganizations subject={subject} facts={[firstFact]} />)
  await screen.findByRole('heading', { name: 'Record organization' })
  expect(screen.getByText(/no message is sent/)).toBeInTheDocument()
  change('Organization name', 'Example Bank')
  change('Organization category', 'finance')
  change('Organization website', 'https://bank.example/privacy')
  change('Organization contact', 'privacy@bank.example')
  fireEvent.click(screen.getByRole('button', { name: 'Save organization' }))
  await screen.findByRole('heading', { name: 'Edit organization' })
  expect(location.hash).toContain('org=')
  expect(location.hash).toContain('shell=retained')
  expect(screen.getByRole('button', { name: 'Example Bank · revision 1' })).toBeInTheDocument()
  expect(screen.getByText('Revision 1: Example Bank · active')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save holding' }))
  await screen.findByText('Primary email · revision 1 · held')
  const orgId = new URLSearchParams(location.hash.split('?')[1]).get('org')!
  const holdingRows = await json(`${base}/organizations/${orgId}/holdings`)
  expect(holdingRows.holdings).toHaveLength(1)
  expect(JSON.stringify(holdingRows)).not.toContain(oldSecret)
  fireEvent.click(screen.getByRole('button', { name: 'Holding history' }))
  const history = await screen.findByRole('region', { name: 'Holding and disposition history' })
  expect(within(history).getByText('Revision 1 · held')).toBeInTheDocument()
  currentFact = await json(`${base}/facts/${firstFact.id}`, 'PUT', { request_id: 'correct', revision: 1, value: newSecret, passphrase: password })
  view.rerender(<PrivacyOrganizations subject={subject} facts={[currentFact]} />)
  change('Previous fact revision', '1')
  fireEvent.click(screen.getByRole('button', { name: 'Start change checklist' }))
  const progress = await screen.findByRole('region', { name: 'Change progress' })
  expect(within(progress).getByText('1 pending · 0 updated · 0 removed · 1 total')).toBeInTheDocument()
  expect(within(progress).getByText(/Example Bank · pending · user_reported/)).toBeInTheDocument()
  change('Disposition evidence', 'Owner confirmation 90210')
  fireEvent.click(within(progress).getByRole('button', { name: 'Attest updated' }))
  await waitFor(() => expect(screen.getByText('0 pending · 1 updated · 0 removed · 1 total')).toBeInTheDocument())
  expect(screen.getByText(/Owner confirmation 90210/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Disposition history' }))
  const targetHistory = await screen.findByRole('region', { name: 'Holding and disposition history' })
  expect(within(targetHistory).getByText('Revision 1 · pending')).toBeInTheDocument()
  expect(within(targetHistory).getByText('Revision 2 · updated · Owner confirmation 90210')).toBeInTheDocument()
  const changes = await json(`${base}/subjects/${subject}/changes`)
  expect(changes.changes[0].progress).toEqual({ pending: 0, updated: 1, removed: 0, total: 1 })
  const held = await json(`${base}/organizations/${orgId}/holdings`)
  expect(held.holdings[0]).toMatchObject({ status: 'held', fact_revision: 2, change_id: null })
  expect(JSON.stringify(changes)).not.toContain(oldSecret)
  expect(JSON.stringify(changes)).not.toContain(newSecret)
  expect(JSON.stringify(changes)).not.toContain(password)
  expect(readFileSync(join(home, 'capabilities', 'privacy.sqlite3')).includes(Buffer.from(oldSecret))).toBe(false)
  expect(readFileSync(join(home, 'capabilities', 'privacy.sqlite3')).includes(Buffer.from(newSecret))).toBe(false)
  expect(readFileSync(join(home, 'capabilities', 'privacy.sqlite3')).includes(Buffer.from(password))).toBe(false)
  view.unmount()
})

test('owner can revise and archive organization while invalid stale writes remain visible as errors', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/privacy?subject=' + subject)
  const view = render(<PrivacyOrganizations subject={subject} facts={[currentFact]} />)
  const organization = await screen.findByRole('button', { name: 'Example Bank · revision 1' })
  fireEvent.click(organization)
  await screen.findByRole('heading', { name: 'Edit organization' })
  change('Organization name', 'Example Credit Union')
  fireEvent.click(screen.getByLabelText('Archive organization'))
  fireEvent.click(screen.getByRole('button', { name: 'Save organization' }))
  await screen.findByRole('button', { name: 'Example Credit Union · revision 2' })
  expect(screen.getByText('Revision 1: Example Bank · active')).toBeInTheDocument()
  expect(screen.getByText('Revision 2: Example Credit Union · archived')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save holding' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('active organization')
  expect(screen.queryByText(oldSecret)).not.toBeInTheDocument()
  expect(screen.queryByText(newSecret)).not.toBeInTheDocument()
  expect(screen.queryByText(password)).not.toBeInTheDocument()
  view.unmount()
})

test('removed disposition is user-attested and no draft or send control is exposed', async () => {
  const org = await json(`${base}/subjects/${subject}/organizations`, 'POST', { request_id: 'org-remove', name: 'Former Utility', category: 'utility', website: '', contact: '', source: 'owner' })
  await json(`${base}/organizations/${org.id}/holdings`, 'POST', { request_id: 'holding-remove', revision: 0, fact_id: currentFact.id, fact_revision: currentFact.revision, status: 'held', source: 'owner' })
  const thirdFact = await json(`${base}/facts/${currentFact.id}`, 'PUT', { request_id: 'correct-again', revision: 2, value: 'third-ui-private-333@example.test', passphrase: password })
  window.history.replaceState(null, '', '#/capabilities/wellbeing/privacy?subject=' + subject + '&org=' + org.id)
  const view = render(<PrivacyOrganizations subject={subject} facts={[thirdFact]} />)
  await screen.findByRole('heading', { name: 'Edit organization' })
  change('Previous fact revision', '2')
  fireEvent.click(screen.getByRole('button', { name: 'Start change checklist' }))
  const progress = await screen.findByRole('region', { name: 'Change progress' })
  change('Disposition evidence', 'Owner reports the utility removed the field')
  fireEvent.click(within(progress).getByRole('button', { name: 'Attest removed' }))
  await screen.findByText('0 pending · 0 updated · 1 removed · 1 total')
  expect(screen.getByText(/Former Utility · removed · user_reported/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /draft/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument()
  view.unmount()
})
