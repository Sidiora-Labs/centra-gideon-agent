import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import PrivacyBrokers from './PrivacyBrokers'

let server: ChildProcess
let origin = ''
let contractOrigin = ''
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
import asyncio, json, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_privacy import register as register_privacy
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_brokers import register as register_brokers
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_broker_spokeo import register as register_spokeo
from gideon.workspace.capabilities.wellbeing.privacy_broker_spokeo import SpokeoCaseAdapter, SpokeoProtocol
from gideon.workspace.capabilities.wellbeing.privacy_brokers import PrivacyBrokerStore
async def main():
 contract_state = {'listed': True, 'posts': []}
 contract = web.Application()
 async def search(request):
  body = '<html><article>Jane Doe lives in Oakland, CA</article></html>' if contract_state['listed'] else '<html><h1>No results found</h1></html>'
  return web.Response(text=body)
 async def optout(request):
  return web.Response(text='<form action="/optout/submit" method="post"><input type="hidden" name="csrf" value="ui-token"><input name="url"><input name="email"></form>')
 async def submit(request):
  contract_state['posts'].append(dict(await request.post()))
  return web.Response(text='<html>Request received. Check your email for the confirmation email.</html>')
 async def state(request):
  return web.json_response({'listed': contract_state['listed'], 'posts': contract_state['posts']})
 async def remove(request):
  contract_state['listed'] = False
  return web.json_response({'listed': False})
 contract.router.add_get('/Jane-Doe/CA', search)
 contract.router.add_get('/optout', optout)
 contract.router.add_post('/optout/submit', submit)
 contract.router.add_get('/contract/state', state)
 contract.router.add_post('/contract/remove', remove)
 contract_runner = web.AppRunner(contract)
 await contract_runner.setup()
 contract_site = web.TCPSite(contract_runner, '127.0.0.1', 0)
 await contract_site.start()
 contract_origin = 'http://127.0.0.1:' + str(contract_site._server.sockets[0].getsockname()[1])
 app = web.Application()
 home = Path(os.environ['GIDEON_HOME'])
 register_privacy(app, home)
 register_brokers(app, home)
 register_spokeo(app, adapter=SpokeoCaseAdapter(PrivacyBrokerStore(home), SpokeoProtocol(contract_origin, contract_mode=True)))
 runner = web.AppRunner(app)
 await runner.setup()
 site = web.TCPSite(runner, '127.0.0.1', 0)
 await site.start()
 print(json.dumps({'port': site._server.sockets[0].getsockname()[1], 'contract_origin': contract_origin}), flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  origin = await new Promise<string>((accept, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => {
      const ready = JSON.parse(String(data).trim())
      contractOrigin = ready.contract_origin
      accept(`http://127.0.0.1:${ready.port}`)
    })
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

test('Spokeo controls scan, prepare, require approval, submit and verify over real HTTP', async () => {
  const broker = await json(base + '/brokers', 'POST', {
    request_id: 'spokeo-broker', name: 'Spokeo', website: contractOrigin,
    optout_url: contractOrigin + '/optout', source: 'supported protocol' })
  const row = await json(`${base}/subjects/${subject}/broker-cases`, 'POST', {
    request_id: 'spokeo-case', broker_id: broker.id })
  window.history.replaceState(null, '', `#/capabilities/wellbeing/privacy?subject=${subject}&broker_case=${row.id}`)
  const view = render(<PrivacyBrokers subject={subject} />)
  const provider = await screen.findByRole('region', { name: 'Spokeo provider controls' })
  expect(within(provider).getByText(/No action runs automatically/)).toBeInTheDocument()
  change('Spokeo first name', 'Jane')
  change('Spokeo last name', 'Doe')
  change('Spokeo city', 'Oakland')
  change('Spokeo state', 'CA')
  fireEvent.click(within(provider).getByRole('button', { name: 'Scan Spokeo' }))
  await waitFor(() => expect(screen.getByText('State: found · evidence: provider_protocol · revision 2')).toBeInTheDocument())
  change('Spokeo profile URL', contractOrigin + '/Jane-Doe/CA/id-1')
  change('Spokeo contact email', 'owner@example.test')
  fireEvent.click(within(provider).getByRole('button', { name: 'Prepare opt-out' }))
  await screen.findByText('Contract server is ready for an owner-approved submission. This is not live-broker readiness; preparation sent no opt-out request.')
  await waitFor(() => expect(screen.getByText('State: optout_in_progress · evidence: provider_protocol · revision 3')).toBeInTheDocument())
  let contract = await (await networkFetch(contractOrigin + '/contract/state')).json()
  expect(contract.posts).toEqual([])
  const submit = within(provider).getByRole('button', { name: 'Submit approved opt-out' })
  expect(submit).toBeDisabled()
  fireEvent.click(within(provider).getByLabelText('I approve this Spokeo opt-out submission'))
  expect(submit).toBeEnabled()
  fireEvent.click(submit)
  await waitFor(() => expect(screen.getByText('State: submitted · evidence: provider_protocol · revision 4')).toBeInTheDocument())
  contract = await (await networkFetch(contractOrigin + '/contract/state')).json()
  expect(contract.posts).toEqual([{ csrf: 'ui-token', url: contractOrigin + '/Jane-Doe/CA/id-1', email: 'owner@example.test' }])
  await networkFetch(contractOrigin + '/contract/remove', { method: 'POST' })
  fireEvent.click(within(provider).getByRole('button', { name: 'Verify removal' }))
  await waitFor(() => expect(screen.getByText('State: confirmed_removed · evidence: provider_protocol · revision 5')).toBeInTheDocument())
  const persisted = await json(`${base}/broker-cases/${row.id}`)
  expect(persisted).toMatchObject({ state: 'confirmed_removed', evidence_basis: 'provider_protocol', verifier: 'spokeo-html-v1' })
  expect(persisted.verified_at).toBeTruthy()
  view.unmount()
})
