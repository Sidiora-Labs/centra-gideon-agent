import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import PlatformPage from './platform/Page'
import Privacy from './wellbeing/Privacy'

let platformServer: ChildProcess
let privacyServer: ChildProcess
let platformOrigin = ''
let privacyOrigin = ''
let platformHome = ''
let privacyHome = ''
const networkFetch = globalThis.fetch
const testPython = process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python'

function waitForOrigin(server: ChildProcess, diagnostics: () => string) {
  return new Promise<string>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      if (/^\d+$/.test(line)) {
        accept(`http://127.0.0.1:${line}`)
        lines.close()
      }
    })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics()}`)))
  })
}

async function terminate(server: ChildProcess) {
  if (server?.exitCode === null) await new Promise<void>(done => {
    server.once('exit', () => done())
    server.kill('SIGTERM')
  })
}

async function json(origin: string, path: string, method = 'GET', body?: unknown) {
  const response = await networkFetch(origin + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
  return response.json()
}

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  platformHome = await mkdtemp(join(tmpdir(), 'gideon-platform-mount-'))
  privacyHome = await mkdtemp(join(tmpdir(), 'gideon-privacy-mount-'))
  let platformDiagnostics = ''
  platformServer = spawn(testPython, ['checks/runtime/capabilities/platform/integration_apps_ui_server.py'], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: platformHome, GIDEON_DEV_NO_AUTH: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  platformServer.stderr!.on('data', chunk => { platformDiagnostics += chunk.toString() })
  platformOrigin = await waitForOrigin(platformServer, () => platformDiagnostics)
  let privacyDiagnostics = ''
  privacyServer = spawn(testPython, ['-u', '-c', `
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
`], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: privacyHome },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  privacyServer.stderr!.on('data', chunk => { privacyDiagnostics += chunk.toString() })
  privacyOrigin = await waitForOrigin(privacyServer, () => privacyDiagnostics)
})

afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  await terminate(platformServer)
  await terminate(privacyServer)
  await rm(platformHome, { recursive: true, force: true })
  await rm(privacyHome, { recursive: true, force: true })
})

test('platform page mounts reviewed integration apps with its real HTTP workflow', async () => {
  history.replaceState(null, '', '#/capabilities/platform')
  const view = render(<PlatformPage baseUrl={platformOrigin} />)
  fireEvent.click(screen.getByRole('button', { name: 'Integrations' }))
  const region = await screen.findByRole('region', { name: 'Integration applications' })
  expect(region).toHaveTextContent('Jira, Datadog and GitHub')
  fireEvent.change(screen.getByLabelText('Integration provider'), { target: { value: 'github' } })
  fireEvent.change(screen.getByLabelText('Integration label'), { target: { value: 'Release repositories' } })
  fireEvent.change(screen.getByLabelText('Integration credential name'), { target: { value: 'github-release-token' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save connection' }))
  const option = await screen.findByRole('option', { name: 'Release repositories · github' })
  expect(option).toBeVisible()
  const persisted = await json(platformOrigin, '/api/capabilities/platform/integration-apps')
  expect(persisted.connections).toHaveLength(1)
  expect(persisted.connections[0]).toMatchObject({ kind: 'github', credential_name: 'github-release-token' })
  expect(persisted.connections[0]).not.toHaveProperty('credential_value')
  view.unmount()
})

test('selected privacy subject keeps fact history and organization controls beside broker cases', async () => {
  const base = '/api/capabilities/wellbeing/privacy'
  const subject = await json(privacyOrigin, base + '/subjects', 'POST', { request_id: 'mounted-subject', alias: 'Owner alias', relationship: 'self', source: 'owner supplied' })
  await json(privacyOrigin, `${base}/subjects/${subject.id}/consents`, 'POST', { request_id: 'vault', revision: 0, scope: 'vault', granted: true, method: 'owner choice' })
  await json(privacyOrigin, `${base}/subjects/${subject.id}/consents`, 'POST', { request_id: 'scan', revision: 0, scope: 'broker_scan', granted: true, method: 'owner choice' })
  const fact = await json(privacyOrigin, `${base}/subjects/${subject.id}/facts`, 'POST', { request_id: 'fact', type: 'email', label: 'Private email', value: 'mounted@example.test', passphrase: 'long integration passphrase', source: 'owner supplied', use_for_scans: false })
  const broker = await json(privacyOrigin, base + '/brokers', 'POST', { request_id: 'broker', name: 'Example Search', website: 'https://broker.example/profile', optout_url: 'https://broker.example/remove', source: 'owner researched' })
  const brokerCase = await json(privacyOrigin, `${base}/subjects/${subject.id}/broker-cases`, 'POST', { request_id: 'case', broker_id: broker.id })
  history.replaceState(null, '', `#/capabilities/wellbeing/privacy?subject=${subject.id}&fact=${fact.id}&broker_case=${brokerCase.id}&shell=retained`)
  globalThis.fetch = (input, init) => networkFetch(new URL(String(input), privacyOrigin), init)
  const view = render(<Privacy />)
  expect(await screen.findByRole('heading', { name: 'Correct selected private fact' })).toBeVisible()
  expect(screen.getByRole('region', { name: 'Private fact history' })).toHaveTextContent('Revision 1: Private email · ••••')
  expect(screen.getByRole('region', { name: 'Organizations and change propagation' })).toBeVisible()
  expect(screen.getByRole('region', { name: 'Privacy broker cases' })).toBeVisible()
  const selected = await screen.findByRole('region', { name: 'Selected broker case' })
  expect(selected).toHaveTextContent('Example Search')
  expect(selected).toHaveTextContent('State: unscanned · evidence: none · revision 1')
  expect(location.hash).toContain(`subject=${subject.id}`)
  expect(location.hash).toContain(`fact=${fact.id}`)
  expect(location.hash).toContain(`broker_case=${brokerCase.id}`)
  expect(location.hash).toContain('shell=retained')
  const persistedFact = await json(privacyOrigin, `${base}/facts/${fact.id}`)
  expect(persistedFact.masked_value).toBe('••••')
  expect(persistedFact).not.toHaveProperty('value')
  await waitFor(() => expect(screen.queryByText('Loading broker cases…')).not.toBeInTheDocument())
  view.unmount()
  globalThis.fetch = networkFetch
})
