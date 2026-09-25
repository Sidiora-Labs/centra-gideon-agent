import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { SocialPanel } from './SocialPanel'

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

it('registers a normalized identity and reopens it from the URL with real persistence', async () => {
  render(<SocialPanel />)
  expect(screen.getByText(/Local registry only/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Social handle'), { target: { value: '@Alice' } })
  fireEvent.change(screen.getByLabelText('Social label'), { target: { value: 'Alice profile' } })
  fireEvent.click(screen.getByRole('button', { name: 'Register social account' }))
  await screen.findByText('Registration: active; revision 1')
  expect(location.hash).toContain('social_account=')
  expect(screen.getByRole('link', { name: 'Open profile' })).toHaveAttribute('href', 'https://x.com/alice')
  expect(screen.getByLabelText('Social handle')).toHaveValue('')
  const response = await originalFetch(origin + base + '/social/accounts')
  const row = (await response.json()).accounts[0]
  expect(row.handle).toBe('alice')
  expect(row.qualification).toBe('registry_only')
  cleanup()
  render(<SocialPanel />)
  await screen.findByText('Registration: active; revision 1')
  expect(screen.getByRole('heading', { name: 'Alice profile' })).toBeInTheDocument()
  cleanup()
})

it('edits and archives local registration with durable provenance', async () => {
  render(<SocialPanel />)
  await screen.findByText('Registration: active; revision 1')
  fireEvent.click(screen.getByRole('button', { name: 'Edit registration' }))
  expect(screen.getByLabelText('Social handle')).toHaveValue('alice')
  fireEvent.change(screen.getByLabelText('Registration status'), { target: { value: 'archived' } })
  fireEvent.change(screen.getByLabelText('Social notes'), { target: { value: 'Inactive account' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save registration' }))
  await screen.findByText('Registration: archived; revision 2')
  expect(screen.getByText('Inactive account')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Review registration history' }))
  await screen.findByText(/updated: revision 2/)
  expect(screen.getByText(/created: revision 1/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Edit registration' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel registration edit' }))
  expect(screen.getByLabelText('Social handle')).toHaveValue('')
  cleanup()
})

it('rejects malformed profile links and duplicate identities without losing user input', async () => {
  render(<SocialPanel />)
  await screen.findByText('Registration: archived; revision 2')
  fireEvent.change(screen.getByLabelText('Social handle'), { target: { value: 'Bob' } })
  fireEvent.change(screen.getByLabelText('Social profile_url'), { target: { value: 'http://example.com' } })
  fireEvent.click(screen.getByRole('button', { name: 'Register social account' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('HTTPS')
  expect(screen.getByLabelText('Social handle')).toHaveValue('Bob')
  fireEvent.change(screen.getByLabelText('Social profile_url'), { target: { value: '' } })
  fireEvent.change(screen.getByLabelText('Social handle'), { target: { value: '@ALICE' } })
  fireEvent.click(screen.getByRole('button', { name: 'Register social account' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('already registered'))
  const response = await originalFetch(origin + base + '/social/accounts')
  expect((await response.json()).accounts).toHaveLength(1)
  cleanup()
})

it('surfaces stale edits, reloads the current revision, and removes only local metadata', async () => {
  render(<SocialPanel />)
  await screen.findByText('Registration: archived; revision 2')
  fireEvent.click(screen.getByRole('button', { name: 'Edit registration' }))
  const response = await originalFetch(origin + base + '/social/accounts')
  const row = (await response.json()).accounts[0]
  const { id, revision, platform, handle, label, profile_url, credential_ref, person_id, status, notes } = row
  const changed = await originalFetch(origin + base + '/social/accounts/' + id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, platform, handle, label, profile_url, credential_ref, person_id, status, notes: notes + ' updated' }) })
  expect(changed.status).toBe(200)
  fireEvent.click(screen.getByRole('button', { name: 'Save registration' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('changed; reload')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel registration edit' }))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh registrations' }))
  await screen.findByText('Registration: archived; revision 3')
  fireEvent.click(screen.getByRole('button', { name: 'Remove local registration' }))
  await waitFor(() => expect(screen.queryByText('Registration: archived; revision 3')).not.toBeInTheDocument())
  const history = await originalFetch(origin + base + '/social/accounts/' + id + '/history')
  expect((await history.json()).history.at(-1).event).toBe('removed')
  expect(screen.getByLabelText('Social account')).toHaveValue('')
  cleanup()
})
