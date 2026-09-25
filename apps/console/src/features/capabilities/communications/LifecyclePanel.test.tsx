import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, execFileSync, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { LifecyclePanel } from './LifecyclePanel'

let server: ChildProcess
let origin: string
let home: string
const originalFetch = globalThis.fetch
const base = '/api/capabilities/communications'

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-thread-ui-'))
  const root = resolve(process.cwd(), '../..')
  execFileSync(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', "from gideon.core.config.loader import AppConfig,AgentProfile; c=AppConfig.load(); c.agents={'research':AgentProfile(provider='native')}; c.save()"], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') } })
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/communications/ui_server.py'], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    let output = ''
    let errors = ''
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('{"port":'))
      if (line) resolveOrigin(`http://127.0.0.1:${JSON.parse(line).port}`)
    })
    server.on('error', reject)
    server.on('exit', code => reject(new Error(`HTTP server exited ${code}: ${errors}`)))
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
})

afterAll(() => {
  cleanup()
  globalThis.fetch = originalFetch
  server?.kill()
  rmSync(home, { recursive: true, force: true })
})

let accountId: string
it('links a real configured agent to a real registered account and activates reviewed ownership', async () => {
  const response = await originalFetch(origin + base + '/social/accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform: 'x', handle: 'alice', request_key: 'ui-assignment' }) })
  accountId = (await response.json()).account.id
  render(<LifecyclePanel />)
  await screen.findByRole('option', { name: 'research' })
  fireEvent.change(screen.getByLabelText('Configured agent'), { target: { value: 'research' } })
  fireEvent.change(screen.getByLabelText('Registered platform account'), { target: { value: accountId } })
  fireEvent.change(screen.getByLabelText('Assignment reason'), { target: { value: 'Research account ownership' } })
  fireEvent.click(screen.getByRole('button', { name: 'Request platform assignment' }))
  await screen.findByText('Assignment: requested; revision 1')
  expect(location.hash).toContain('platform_assignment=')
  expect(screen.getByRole('button', { name: 'Pause assignment' })).toBeDisabled()
  expect(screen.getByText('Ready locally: no')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Assignment reason'), { target: { value: 'Reviewed current account' } })
  fireEvent.click(screen.getByRole('button', { name: 'Activate assignment' }))
  await screen.findByText('Assignment: active; revision 2')
  expect(screen.getByText('Ready locally: yes')).toBeInTheDocument()
  cleanup()
})

it('reopens current assignment and pauses with durable reason history', async () => {
  render(<LifecyclePanel />)
  await screen.findByText('Assignment: active; revision 2')
  fireEvent.change(screen.getByLabelText('Assignment reason'), { target: { value: 'Owner paused operation' } })
  fireEvent.click(screen.getByRole('button', { name: 'Pause assignment' }))
  await screen.findByText('Assignment: paused; revision 3')
  expect(screen.getByText('Ready locally: no')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Review assignment history' }))
  await screen.findByText('paused: Owner paused operation — revision 3')
  expect(screen.getByText('requested: Research account ownership — revision 1')).toBeInTheDocument()
  expect(screen.getByText('active: Reviewed current account — revision 2')).toBeInTheDocument()
  cleanup()
})

it('requires explicit reactivation against a changed account revision', async () => {
  render(<LifecyclePanel />)
  await screen.findByText('Assignment: paused; revision 3')
  const response = await originalFetch(origin + base + '/social/accounts/' + accountId)
  const account = (await response.json()).account
  const { platform, handle, label, profile_url, person_id, status, notes, revision } = account
  const changed = await originalFetch(origin + base + '/social/accounts/' + accountId, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform, handle, label, profile_url, person_id, status, notes, revision, credential_ref: 'ROTATED_UI_CONNECTION' }) })
  expect(changed.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Assignment reason'), { target: { value: 'Old account version' } })
  fireEvent.click(screen.getByRole('button', { name: 'Activate assignment' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('account changed')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh platform assignments' }))
  await screen.findByText('Account registration changed. Refresh and explicitly activate against its current revision.')
  fireEvent.change(screen.getByLabelText('Assignment reason'), { target: { value: 'Reviewed rotated account reference' } })
  fireEvent.click(screen.getByRole('button', { name: 'Activate assignment' }))
  await screen.findByText('Assignment: active; revision 4')
  expect(screen.getByText('Ready locally: yes')).toBeInTheDocument()
  cleanup()
})

it('revokes only local assignment and preserves original external registry and history', async () => {
  render(<LifecyclePanel />)
  await screen.findByText('Assignment: active; revision 4')
  fireEvent.change(screen.getByLabelText('Assignment reason'), { target: { value: 'Owner revoked assignment' } })
  fireEvent.click(screen.getByRole('button', { name: 'Revoke local assignment' }))
  await screen.findByText('Assignment: revoked; revision 5')
  expect(screen.queryByRole('button', { name: 'Activate assignment' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Review assignment history' }))
  await screen.findByText('revoked: Owner revoked assignment — revision 5')
  const response = await originalFetch(origin + base + '/social/accounts/' + accountId)
  const account = (await response.json()).account
  expect(account.status).toBe('active')
  expect(account.credential_ref).toBe('ROTATED_UI_CONNECTION')
  expect(account.revision).toBe(2)
  cleanup()
})
