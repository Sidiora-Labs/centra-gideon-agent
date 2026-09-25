import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { XPanel } from './XPanel'

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
it('selects a real registration and reports missing credentials without fabricating a feed', async () => {
  const response = await originalFetch(origin + base + '/social/accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform: 'x', handle: 'alice', request_key: 'ui-x', credential_ref: 'ABSENT_X_UI_929A' }) })
  accountId = (await response.json()).account.id
  render(<XPanel />)
  await screen.findByRole('option', { name: '@alice' })
  fireEvent.change(screen.getByLabelText('X registration'), { target: { value: accountId } })
  await screen.findByText('X read: not_synced; unknown')
  expect(location.hash).toContain('x_account=')
  fireEvent.click(screen.getByRole('button', { name: 'Read recent X posts' }))
  await screen.findByText('X read: failed; unknown')
  expect(screen.getByText('X credential is unavailable')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Open X post' })).not.toBeInTheDocument()
  cleanup()
})

it('persists a draft, requires review, and produces an encoded browser handoff without posting', async () => {
  render(<XPanel />)
  await screen.findByText('X read: failed; unknown')
  fireEvent.change(screen.getByLabelText('X draft text'), { target: { value: 'A reviewed update & question?' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save X draft' }))
  await screen.findByText('X draft: draft; revision 1')
  expect(screen.queryByRole('link', { name: 'Open reviewed X composer' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm review for browser handoff' }))
  await screen.findByText('X draft: reviewed_handoff; revision 1')
  const link = screen.getByRole('link', { name: 'Open reviewed X composer' })
  expect(new URL(link.getAttribute('href')!).searchParams.get('text')).toBe('A reviewed update & question?')
  expect(screen.getByText(/Verify the signed-in X account/)).toBeInTheDocument()
  const response = await originalFetch(origin + base + '/x/accounts/' + accountId + '/drafts')
  expect((await response.json()).drafts[0].external_posted).toBe(false)
  cleanup()
})

it('reopens durable review and invalidates it when draft content changes', async () => {
  render(<XPanel />)
  await screen.findByRole('link', { name: 'Open reviewed X composer' })
  fireEvent.click(screen.getByRole('button', { name: 'Edit X draft' }))
  expect(screen.getByLabelText('X draft text')).toHaveValue('A reviewed update & question?')
  fireEvent.change(screen.getByLabelText('X draft text'), { target: { value: 'Changed after review' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save X draft changes' }))
  await screen.findByText('X draft: draft; revision 2')
  expect(screen.queryByRole('link', { name: 'Open reviewed X composer' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm review for browser handoff' }))
  await screen.findByText('X draft: reviewed_handoff; revision 2')
  expect(new URL(screen.getByRole('link', { name: 'Open reviewed X composer' }).getAttribute('href')!).searchParams.get('text')).toBe('Changed after review')
  cleanup()
})

it('invalidates previous account review and binds an explicit new review to changed registration', async () => {
  render(<XPanel />)
  await screen.findByText('X draft: reviewed_handoff; revision 2')
  const response = await originalFetch(origin + base + '/social/accounts/' + accountId)
  const account = (await response.json()).account
  const { platform, handle, label, profile_url, credential_ref, person_id, status, notes, revision } = account
  expect(handle).toBe('alice')
  const changed = await originalFetch(origin + base + '/social/accounts/' + accountId, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform, handle: 'bob', label, profile_url, credential_ref, person_id, status, notes, revision }) })
  expect(changed.status).toBe(200)
  fireEvent.click(screen.getByRole('button', { name: 'Reload X workspace' }))
  await screen.findByText('X draft: registration_changed; revision 2')
  expect(screen.queryByRole('link', { name: 'Open reviewed X composer' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm review for browser handoff' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('registration changed')
  fireEvent.click(screen.getByRole('button', { name: 'Edit X draft' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save X draft changes' }))
  await screen.findByText('X draft: draft; revision 3')
  fireEvent.click(screen.getByRole('button', { name: 'Confirm review for browser handoff' }))
  await screen.findByText('X draft: reviewed_handoff; revision 3')
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  cleanup()
})
