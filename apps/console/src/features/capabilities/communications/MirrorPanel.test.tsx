import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { MirrorPanel } from './MirrorPanel'

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

const raw = (id: string) => `From: friend@example.com
To: owner@example.com
Message-ID: <${id}@example.com>
Date: ${new Date(Date.now() - 60000).toUTCString()}
Subject: Mirrored ${id}
Content-Type: text/plain; charset=utf-8

Actual persisted message body ${id}
`

it('creates and syncs a real Maildir account through the UI and reads persisted messages', async () => {
  render(<MirrorPanel />)
  await screen.findByText(/Read mail into this runtime/)
  fireEvent.change(screen.getByLabelText('Account name'), { target: { value: 'UI Maildir' } })
  fireEvent.change(screen.getByLabelText('Owner email'), { target: { value: 'owner@example.com' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create mail account' }))
  await screen.findByText(/Sync: not_synced/)
  expect(screen.getByLabelText('Account name')).toHaveValue('')
  fireEvent.change(screen.getByLabelText('Mail source content'), { target: { value: raw('maildir') } })
  fireEvent.click(screen.getByRole('button', { name: 'Upload mail source' }))
  await waitFor(() => expect(screen.getByLabelText('Mail source content')).toHaveValue(''))
  fireEvent.click(screen.getByRole('button', { name: 'Sync mail account' }))
  await screen.findByText('Mirrored maildir')
  expect(screen.getByText(/Actual persisted message body maildir/)).toBeInTheDocument()
  expect(screen.getByText(/coverage: complete_source_snapshot/)).toBeInTheDocument()
  const response = await originalFetch(origin + base + '/mirror/accounts')
  const accounts = (await response.json()).accounts
  const account = accounts.find((row: { name: string }) => row.name === 'UI Maildir')
  expect(account.sync.seen).toBe(1)
  expect(account.sync.scope).toBe('uploaded_or_local_source')
  const messages = await originalFetch(origin + base + '/mirror/accounts/' + account.id + '/messages')
  expect((await messages.json()).messages[0].external_id).toBe('<maildir@example.com>')
  cleanup()
  render(<MirrorPanel />)
  await screen.findByRole('option', { name: 'UI Maildir' })
  fireEvent.change(screen.getByLabelText('Mail account'), { target: { value: account.id } })
  await screen.findByText('Mirrored maildir')
  fireEvent.click(screen.getByRole('button', { name: 'Read mirrored messages' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Read mirrored messages' })).not.toBeDisabled())
  expect(screen.getAllByText('Mirrored maildir')).toHaveLength(1)
  cleanup()
})

it('imports real mbox text and surfaces failed archive validation', async () => {
  render(<MirrorPanel />)
  await screen.findByRole('option', { name: 'UI Maildir' })
  fireEvent.change(screen.getByLabelText('Account name'), { target: { value: 'UI Mbox' } })
  fireEvent.change(screen.getByLabelText('Owner email'), { target: { value: 'owner@example.com' } })
  fireEvent.change(screen.getByLabelText('Source type'), { target: { value: 'mbox' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create mail account' }))
  await screen.findByText(/Sync: not_synced/)
  fireEvent.change(screen.getByLabelText('Mail source content'), { target: { value: 'invalid archive' } })
  fireEvent.click(screen.getByRole('button', { name: 'Upload mail source' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toMatch(/Mbox data/)
  expect(screen.getByLabelText('Mail source content')).toHaveValue('invalid archive')
  fireEvent.change(screen.getByLabelText('Mail source content'), { target: { value: 'From sender Tue Jan 1 00:00:00 2020\n' + raw('mbox') } })
  fireEvent.click(screen.getByRole('button', { name: 'Upload mail source' }))
  await waitFor(() => expect(screen.getByLabelText('Mail source content')).toHaveValue(''))
  fireEvent.click(screen.getByRole('button', { name: 'Sync mail account' }))
  await screen.findByText('Mirrored mbox')
  expect(screen.queryByText('Mirrored maildir')).not.toBeInTheDocument()
  expect(screen.getByText(/evidence: recorded/)).toBeInTheDocument()
  cleanup()
})

it('configures an IMAP credential reference and reports unavailable credentials without a success claim', async () => {
  render(<MirrorPanel />)
  await screen.findByRole('option', { name: 'UI Maildir' })
  fireEvent.change(screen.getByLabelText('Account name'), { target: { value: 'UI Remote' } })
  fireEvent.change(screen.getByLabelText('Owner email'), { target: { value: 'owner@example.com' } })
  fireEvent.change(screen.getByLabelText('Source type'), { target: { value: 'imap' } })
  fireEvent.change(screen.getByLabelText('host'), { target: { value: 'localhost' } })
  fireEvent.change(screen.getByLabelText('username'), { target: { value: 'owner' } })
  fireEvent.change(screen.getByLabelText('credential_ref'), { target: { value: 'GIDEON_ABSENT_UI_MAIL_SECRET_45F7' } })
  fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'xoauth2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create mail account' }))
  await screen.findByText(/Sync: not_synced/)
  expect(screen.queryByLabelText('Mail source content')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Sync mail account' }))
  await screen.findByText(/Sync: failed/)
  expect(screen.getByText(/coverage: unknown/)).toBeInTheDocument()
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toMatch(/credential/)
  fireEvent.click(screen.getByText('Adapter coverage'))
  expect(screen.getByText(/teams: unavailable/)).toBeInTheDocument()
  expect(screen.getByText(/gmail: available/).textContent).toContain('external_credentials_required')
  cleanup()
})
