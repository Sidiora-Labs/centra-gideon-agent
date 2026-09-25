import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { TeamsPanel } from './TeamsPanel'

let server: ChildProcess
let origin: string
let home: string
const originalFetch = globalThis.fetch
const base = '/api/capabilities/communications/teams/sources'

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-teams-ui-'))
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/communications/ui_server.py'], {
    cwd: root,
    env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') },
    stdio: ['ignore', 'pipe', 'pipe'],
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

it('creates an account-bound Teams source and records missing credential state without claiming history', async () => {
  render(<TeamsPanel />)
  await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
  expect(screen.getByText(/Sync never sends messages/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Create Teams source' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Teams source name'), { target: { value: 'Customer Teams' } })
  fireEvent.change(screen.getByLabelText('Verified Microsoft owner email'), { target: { value: 'owner@example.com' } })
  fireEvent.change(screen.getByLabelText('Graph credential reference'), { target: { value: 'ABSENT_UI_GRAPH_8432' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Teams source' }))
  await screen.findByText('Sync: not_synced · coverage: unknown')
  expect(location.hash).toContain('teams_source=')
  expect(screen.getByRole('button', { name: 'Save Teams source' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Sync Teams history' }))
  await screen.findByText('Sync: failed · coverage: unknown')
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('credential is unavailable')
  expect(screen.getAllByText('Microsoft Graph credential is unavailable; connect this source first')).toHaveLength(2)
  expect(screen.getByText('No Teams messages have been acquired.')).toBeInTheDocument()
  const response = await originalFetch(origin + base)
  const rows = (await response.json()).sources
  expect(rows).toHaveLength(1)
  expect(rows[0].owner_email).toBe('owner@example.com')
  expect(rows[0].credential_ref).toBe('ABSENT_UI_GRAPH_8432')
  expect(rows[0].sync.state).toBe('failed')
  expect(JSON.stringify(rows[0])).not.toContain('Bearer')
  cleanup()
})

it('loads the persisted source, edits it with a revision, and preserves failed acquisition truth', async () => {
  render(<TeamsPanel />)
  await screen.findByRole('link', { name: 'Customer Teams' })
  fireEvent.click(screen.getByRole('link', { name: 'Customer Teams' }))
  await waitFor(() => expect(screen.getByLabelText('Teams source name')).toHaveValue('Customer Teams'))
  expect(screen.getByLabelText('Verified Microsoft owner email')).toHaveValue('owner@example.com')
  expect(screen.getByLabelText('Graph credential reference')).toHaveValue('ABSENT_UI_GRAPH_8432')
  expect(screen.getByText('Sync: failed · coverage: unknown')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Teams source name'), { target: { value: 'Renamed Customer Teams' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save Teams source' }))
  await screen.findByRole('link', { name: 'Renamed Customer Teams' })
  await waitFor(() => expect(screen.getByText('Sync: not_synced · coverage: unknown')).toBeInTheDocument())
  const response = await originalFetch(origin + base)
  const row = (await response.json()).sources[0]
  expect(row.name).toBe('Renamed Customer Teams')
  expect(row.revision).toBe(2)
  expect(row.owner_email).toBe('owner@example.com')
  expect(row.sync.state).toBe('not_synced')
  cleanup()
})

it('surfaces validation errors and retains entered source details for correction', async () => {
  render(<TeamsPanel />)
  await screen.findByRole('link', { name: 'Renamed Customer Teams' })
  fireEvent.click(screen.getByRole('button', { name: 'New Teams source' }))
  fireEvent.change(screen.getByLabelText('Teams source name'), { target: { value: 'Invalid Teams' } })
  fireEvent.change(screen.getByLabelText('Verified Microsoft owner email'), { target: { value: 'not-an-email' } })
  fireEvent.change(screen.getByLabelText('Graph credential reference'), { target: { value: '../secret' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Teams source' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toMatch(/email|credential/i)
  expect(screen.getByLabelText('Teams source name')).toHaveValue('Invalid Teams')
  expect(screen.getByLabelText('Verified Microsoft owner email')).toHaveValue('not-an-email')
  expect(screen.getByLabelText('Graph credential reference')).toHaveValue('../secret')
  const response = await originalFetch(origin + base)
  expect((await response.json()).sources).toHaveLength(1)
  cleanup()
})
