import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import PrivacyBrokers from './PrivacyBrokers'

let server: ChildProcess
let origin = ''
let home = ''
let subject = ''
let caseId = ''
const networkFetch = globalThis.fetch
const base = '/api/capabilities/wellbeing/privacy'

async function json(path: string, method = 'GET', body?: unknown) {
  const response = await networkFetch(origin + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
  return response.json()
}

beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-brokers-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_privacy import register as register_privacy
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_brokers import register as register_brokers
async def main():
 app = web.Application()
 home = Path(os.environ['GIDEON_HOME'])
 register_privacy(app, home)
 register_brokers(app, home)
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
  const person = await json(base + '/subjects', 'POST', { request_id: 'subject', alias: 'Owner', relationship: 'self', source: 'owner' })
  subject = person.id
  await json(`${base}/subjects/${subject}/consents`, 'POST', { request_id: 'scan', revision: 0, scope: 'broker_scan', granted: true, method: 'owner choice' })
  await json(`${base}/subjects/${subject}/consents`, 'POST', { request_id: 'submit', revision: 0, scope: 'broker_submit', granted: true, method: 'owner choice' })
})

afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

test('owner creates a broker case and records an explicitly user-attested observation', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/privacy?subject=' + subject + '&shell=retained')
  const view = render(<PrivacyBrokers subject={subject} />)
  await screen.findByRole('heading', { name: 'Add broker' })
  expect(screen.getByText(/without sending requests/)).toBeInTheDocument()
  expect(screen.getByText(/Owner reports stay labelled user-attested/)).toBeInTheDocument()
  expect(screen.getByText(/Confirmed removal is reserved for an integrated verifier re-scan/)).toBeInTheDocument()
  change('Broker name', 'Example People Search')
  change('Broker website', 'https://broker.example/profile')
  change('Broker opt-out URL', 'https://broker.example/remove')
  change('Broker source', 'owner researched')
  fireEvent.click(screen.getByRole('button', { name: 'Save broker' }))
  await waitFor(() => expect(screen.getByRole('option', { name: 'Example People Search' })).toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'Start broker case' }))
  const selected = await screen.findByRole('region', { name: 'Selected broker case' })
  expect(within(selected).getByText('State: unscanned · evidence: none · revision 1')).toBeInTheDocument()
  expect(location.hash).toContain('broker_case=')
  expect(location.hash).toContain('shell=retained')
  caseId = new URLSearchParams(location.hash.split('?')[1]).get('broker_case')!
  change('Observation evidence', 'Owner saw matching contact details')
  fireEvent.click(within(selected).getByRole('button', { name: 'Record user-attested observation' }))
  await waitFor(() => expect(screen.getByText('State: found · evidence: user_attested · revision 2')).toBeInTheDocument())
  expect(screen.getByText('Recorded evidence: Owner saw matching contact details')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Example People Search · found' })).toBeInTheDocument()
  const history = screen.getByRole('region', { name: 'Broker case history' })
  expect(within(history).getByText('Revision 1 · unscanned · none')).toBeInTheDocument()
  expect(within(history).getByText('Revision 2 · found · user_attested')).toBeInTheDocument()
  const events = screen.getByRole('region', { name: 'Broker case events' })
  expect(within(events).getByText('Revision 1 · created · unscanned')).toBeInTheDocument()
  expect(within(events).getByText('Revision 2 · owner_observation · found')).toBeInTheDocument()
  const persisted = await json(`${base}/broker-cases/${caseId}`)
  expect(persisted).toMatchObject({ state: 'found', evidence_basis: 'user_attested', evidence: 'Owner saw matching contact details' })
  expect(persisted).not.toHaveProperty('verified_at')
  expect(persisted).not.toHaveProperty('verifier')
  view.unmount()
})

test('manual UI advances legal lifecycle, requests re-check, and never offers verified state or sending', async () => {
  window.history.replaceState(null, '', `#/capabilities/wellbeing/privacy?subject=${subject}&broker_case=${caseId}`)
  const view = render(<PrivacyBrokers subject={subject} />)
  let selected = await screen.findByRole('region', { name: 'Selected broker case' })
  change('Transition reason', 'Owner started the broker process elsewhere')
  fireEvent.click(within(selected).getByRole('button', { name: 'Move to optout_in_progress' }))
  await waitFor(() => expect(screen.getByText('State: optout_in_progress · evidence: user_attested · revision 3')).toBeInTheDocument())
  selected = screen.getByRole('region', { name: 'Selected broker case' })
  change('Transition reason', 'Owner submitted the broker form')
  fireEvent.click(within(selected).getByRole('button', { name: 'Move to submitted' }))
  await waitFor(() => expect(screen.getByText('State: submitted · evidence: user_attested · revision 4')).toBeInTheDocument())
  selected = screen.getByRole('region', { name: 'Selected broker case' })
  change('Transition reason', 'Owner received an acknowledgement')
  fireEvent.click(within(selected).getByRole('button', { name: 'Move to verification_pending' }))
  await waitFor(() => expect(screen.getByText('State: verification_pending · evidence: user_attested · revision 5')).toBeInTheDocument())
  selected = screen.getByRole('region', { name: 'Selected broker case' })
  expect(within(selected).queryByRole('button', { name: /confirmed_removed/ })).not.toBeInTheDocument()
  expect(within(selected).queryByRole('button', { name: /reappeared/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /verify removal/i })).not.toBeInTheDocument()
  fireEvent.click(within(selected).getByRole('button', { name: 'Request re-check' }))
  await waitFor(() => expect(screen.getByText('State: verification_pending · evidence: user_attested · revision 6')).toBeInTheDocument())
  expect(screen.getByText('Revision 6 · recheck_requested · verification_pending')).toBeInTheDocument()
  const persisted = await json(`${base}/broker-cases/${caseId}`)
  expect(persisted.state).toBe('verification_pending')
  expect(persisted.evidence_basis).toBe('user_attested')
  expect(persisted.allowed_transitions).toEqual(['awaiting_processing', 'human_task_queued'])
  view.unmount()
})

test('server validation remains visible and an owner report cannot become verified removal', async () => {
  window.history.replaceState(null, '', `#/capabilities/wellbeing/privacy?subject=${subject}&broker_case=${caseId}`)
  const view = render(<PrivacyBrokers subject={subject} />)
  const selected = await screen.findByRole('region', { name: 'Selected broker case' })
  fireEvent.change(within(selected).getByLabelText('Owner observation'), { target: { value: 'not_found' } })
  change('Observation evidence', 'Owner could no longer see a public result')
  fireEvent.click(within(selected).getByRole('button', { name: 'Record user-attested observation' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('must complete or request verification')
  expect(screen.getByText('State: verification_pending · evidence: user_attested · revision 6')).toBeInTheDocument()
  const response = await networkFetch(`${origin}${base}/broker-cases/${caseId}/verified`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  expect(response.status).toBe(404)
  const persisted = await json(`${base}/broker-cases/${caseId}`)
  expect(persisted.state).not.toBe('confirmed_removed')
  expect(persisted.evidence_basis).not.toBe('verified_rescan')
  view.unmount()
})
