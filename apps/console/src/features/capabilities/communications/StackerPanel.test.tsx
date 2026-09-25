import { afterAll, beforeAll, expect, it } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { StackerPanel } from './StackerPanel'

let server: ChildProcess
let origin: string
let home: string
const originalFetch = globalThis.fetch
const base = '/api/capabilities/communications'

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-thread-ui-'))
  const root = resolve(process.cwd(), '../..')
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
it('prepares a real immutable discussion and explicitly reviews its destination and fees', async () => {
  const response = await originalFetch(origin + base + '/social/accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform: 'stackernews', handle: 'alice', request_key: 'ui-stacker', credential_ref: 'ABSENT_STACKER_UI_293D' }) })
  accountId = (await response.json()).account.id
  render(<StackerPanel />)
  await screen.findByRole('option', { name: '@alice' })
  fireEvent.change(screen.getByLabelText('Stacker registration'), { target: { value: accountId } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare Stacker action' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Action title'), { target: { value: 'UI discussion' } })
  fireEvent.change(screen.getByLabelText('Action text'), { target: { value: 'A careful discussion body' } })
  fireEvent.click(screen.getByRole('button', { name: 'Prepare Stacker action' }))
  await screen.findByText('Stacker action: draft; revision 1')
  expect(location.hash).toContain('stacker_account=')
  expect(screen.getByText('Action territory: ~bitcoin; target item: new discussion')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Submit reviewed Stacker action' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Stacker review' }))
  await screen.findByText('Stacker action: reviewed; revision 2')
  expect(screen.getByRole('button', { name: 'Submit reviewed Stacker action' })).toBeDisabled()
  expect(screen.getByRole('link', { name: 'Open reviewed Stacker destination' })).toHaveAttribute('href', 'https://stacker.news/~bitcoin')
  cleanup()
})

it('requires exact action approval and shows missing credentials without posting or payment claims', async () => {
  render(<StackerPanel />)
  await screen.findByText('Stacker action: reviewed; revision 2')
  expect(screen.getByText('A careful discussion body')).toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('Approve this exact action; provider may post and charge fees'))
  fireEvent.click(screen.getByRole('button', { name: 'Submit reviewed Stacker action' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('credential is unavailable')
  expect(screen.getByText('Stacker action: reviewed; revision 2')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Submit reviewed Stacker action' })).toBeDisabled()
  const response = await originalFetch(origin + base + '/stacker/accounts/' + accountId + '/actions')
  const action = (await response.json()).actions[0]
  expect(action.external_execution).toBe('not_attempted')
  expect(action.payin_id).toBeUndefined()
  cleanup()
})

it('keeps API-key zap as browser-only review with the exact amount and target', async () => {
  render(<StackerPanel />)
  await screen.findByText('Stacker action: reviewed; revision 2')
  fireEvent.change(screen.getByLabelText('Stacker action kind'), { target: { value: 'zap' } })
  fireEvent.change(screen.getByLabelText('Action item_id'), { target: { value: '123' } })
  fireEvent.change(screen.getByLabelText('Zap sats'), { target: { value: '21' } })
  fireEvent.click(screen.getByRole('button', { name: 'Prepare Stacker action' }))
  await screen.findByText('Stacker action: draft; revision 1')
  expect(screen.getByText('Requested zap: 21 sats')).toBeInTheDocument()
  expect(screen.getByText('Action territory: ~bitcoin; target item: 123')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Stacker review' }))
  await waitFor(() => expect(screen.getAllByRole('link', { name: 'Open reviewed Stacker destination' })).toHaveLength(2))
  expect(screen.getAllByRole('button', { name: 'Submit reviewed Stacker action' })).toHaveLength(1)
  const response = await originalFetch(origin + base + '/stacker/accounts/' + accountId + '/actions')
  const actions = (await response.json()).actions
  expect(actions[1].kind).toBe('zap')
  expect(actions[1].external_execution).toBe('not_attempted')
  cleanup()
})

it('rejects invalid territory reads locally and invalidates old account action controls', async () => {
  render(<StackerPanel />)
  await screen.findByText('Requested zap: 21 sats')
  fireEvent.change(screen.getByLabelText('Territory name'), { target: { value: '../invalid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Read territory' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('Invalid territory name')
  const response = await originalFetch(origin + base + '/social/accounts/' + accountId)
  const account = (await response.json()).account
  const { platform, handle, label, profile_url, credential_ref, person_id, notes, revision } = account
  const changed = await originalFetch(origin + base + '/social/accounts/' + accountId, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform, handle, label, profile_url, credential_ref, person_id, notes, revision, status: 'paused' }) })
  expect(changed.status).toBe(200)
  fireEvent.click(screen.getByRole('button', { name: 'Reload Stacker actions' }))
  await screen.findAllByText('Registration changed; create a new reviewed action.')
  expect(screen.queryByRole('button', { name: 'Submit reviewed Stacker action' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Open reviewed Stacker destination' })).not.toBeInTheDocument()
  cleanup()
})
