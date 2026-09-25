import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { BeeperPanel } from './BeeperPanel'

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

it('saves a connection reference, queues a durable draft, and reports missing credentials without claiming delivery', async () => {
  render(<BeeperPanel />)
  await screen.findByText('No queued messages.')
  expect(screen.getByText(/Pending sends are not confirmed delivery/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Beeper credential reference'), { target: { value: 'GIDEON_ABSENT_UI_BEEPER_678F' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save Beeper connection' }))
  await screen.findByText('Connection reference saved')
  fireEvent.change(screen.getByLabelText('Beeper chat ID'), { target: { value: 'ui-chat' } })
  fireEvent.change(screen.getByLabelText('Beeper draft text'), { target: { value: 'Reviewed UI draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Queue Beeper draft' }))
  await screen.findByText('ui-chat: draft; not_sent')
  expect(screen.getByText('ui-chat: draft; not_sent')).toBeInTheDocument()
  expect(location.hash).toContain('beeper_chat=ui-chat')
  fireEvent.click(screen.getByRole('button', { name: 'Send queued message' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('credential is unavailable')
  expect(screen.getByText('ui-chat: draft; not_sent')).toBeInTheDocument()
  const response = await originalFetch(origin + base + '/beeper/outbox')
  const rows = (await response.json()).outbox
  expect(rows).toHaveLength(1)
  expect(rows[0].state).toBe('draft')
  expect(rows[0].pending_message_id).toBeNull()
  expect(rows[0].revision).toBe(1)
  cleanup()
})

it('reopens the persisted draft and discards without retracting any remote message', async () => {
  render(<BeeperPanel />)
  await screen.findByText('Reviewed UI draft')
  expect(screen.getByLabelText('Beeper credential reference')).toHaveValue('GIDEON_ABSENT_UI_BEEPER_678F')
  expect(screen.getByLabelText('Beeper chat ID')).toHaveValue('ui-chat')
  fireEvent.click(screen.getByRole('button', { name: 'Discard outbox item' }))
  await screen.findByText('ui-chat: discarded; not_retracted')
  expect(screen.queryByRole('button', { name: 'Send queued message' })).not.toBeInTheDocument()
  const response = await originalFetch(origin + base + '/beeper/outbox')
  const rows = (await response.json()).outbox
  expect(rows).toHaveLength(1)
  expect(rows[0].text).toBe('Reviewed UI draft')
  expect(rows[0].revision).toBe(2)
  expect(rows[0].delivery).toBe('not_retracted')
  cleanup()
})

it('reports unavailable remote pages honestly and preserves the cached empty state', async () => {
  render(<BeeperPanel />)
  await screen.findByText('Reviewed UI draft')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Beeper chats' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('credential is unavailable')
  expect(screen.getByText('No cached Beeper chats.')).toBeInTheDocument()
  const before = await originalFetch(origin + base + '/beeper/chats')
  expect((await before.json()).coverage).toBe('unknown')
  fireEvent.click(screen.getByRole('button', { name: 'Read cached Beeper messages' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Read cached Beeper messages' })).not.toBeDisabled())
  expect(screen.queryByText('Captured source text')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Beeper messages' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('credential is unavailable')
  const messages = await originalFetch(origin + base + '/beeper/messages?chat_id=ui-chat')
  expect((await messages.json()).items).toHaveLength(0)
  cleanup()
})

it('rejects a nonlocal endpoint without overwriting the saved connection', async () => {
  render(<BeeperPanel />)
  await screen.findByText('Reviewed UI draft')
  fireEvent.change(screen.getByLabelText('Beeper endpoint'), { target: { value: 'https://unapproved.example' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save Beeper connection' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('loopback HTTP origin')
  const response = await originalFetch(origin + base + '/beeper/settings')
  const settings = (await response.json()).settings
  expect(settings.base_url).toBe('http://127.0.0.1:23373')
  expect(settings.revision).toBe(1)
  cleanup()
})

it('disconnects explicitly, clears provider cache, and preserves the outbox audit record', async () => {
  render(<BeeperPanel />)
  await screen.findByText('Reviewed UI draft')
  expect(screen.getByText(/Connection: configured; mode: manual_refresh_only/)).toBeInTheDocument()
  expect(screen.getByText(/no background socket or poller is running/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect Beeper' }))
  await screen.findByText(/Beeper disconnected; cached provider data cleared/)
  expect(screen.getByText(/Connection: disconnected; mode: manual_refresh_only/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Disconnect Beeper' })).not.toBeInTheDocument()
  expect(screen.getByText('Reviewed UI draft')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Send queued message' })).not.toBeInTheDocument()
  const response = await originalFetch(origin + base + '/beeper/settings')
  const settings = (await response.json()).settings
  expect(settings.credential_ref).toBe('')
  expect(settings.connected).toBe(false)
  expect(settings.revision).toBe(2)
  cleanup()
})
