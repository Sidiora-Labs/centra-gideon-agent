import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { ThreadsPanel } from './ThreadsPanel'

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

async function post(path: string, body: unknown) {
  const response = await originalFetch(origin + base + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  expect(response.ok).toBe(true)
  return response.json()
}

it('shows empty evidence honestly then unknown, unanswered and answered from persisted observations', async () => {
  render(<ThreadsPanel />)
  await screen.findByText('No thread observations have been imported.')
  expect(screen.getByText(/not live account status/)).toBeInTheDocument()
  const created = await post('/people', { name: 'Evidence Friend' })
  const id = created.person.id
  const now = Date.now()
  const iso = (offset: number) => new Date(now + offset).toISOString()
  const incoming = { person_id: id, thread_id: 'conversation', external_id: 'incoming', occurred_at: iso(-3600000), direction: 'inbound', summary: 'A real imported question' }
  const partial = { source: 'local-archive', source_account_id: 'source-local-account', captured_at: iso(-2000), coverage_start: iso(-7200000), coverage_end: iso(-2000), incoming_complete: true, outgoing_complete: false, messages: [incoming] }
  await post('/threads/evidence', partial)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh thread evidence' }))
  await screen.findByText(/Thread conversation · unknown/)
  expect(screen.getByText('Both incoming and outgoing coverage are required')).toBeInTheDocument()
  expect(screen.getByText('A real imported question')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Evidence Friend' })).toHaveAttribute('href', `#/capabilities/communications?person=${id}`)
  expect(screen.getByText(/Source: local-archive · source-local-account/)).toBeInTheDocument()
  await post('/threads/evidence', { ...partial, captured_at: iso(-1000), coverage_end: iso(-1000), outgoing_complete: true })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh thread evidence' }))
  await screen.findByText(/Thread conversation · unanswered/)
  expect(screen.queryByText('Both incoming and outgoing coverage are required')).not.toBeInTheDocument()
  const outgoing = { ...incoming, external_id: 'outgoing', occurred_at: iso(-500), direction: 'outbound', summary: 'An imported reply' }
  await post('/threads/evidence', { ...partial, captured_at: iso(0), coverage_end: iso(0), outgoing_complete: true, messages: [outgoing] })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh thread evidence' }))
  await screen.findByText(/Thread conversation · answered · 2 observed messages/)
  expect(screen.getByText('An imported reply')).toBeInTheDocument()
  cleanup()
  render(<ThreadsPanel />)
  await screen.findByText(/Thread conversation · answered/)
  const response = await originalFetch(origin + base + '/threads')
  const projection = await response.json()
  expect(projection.qualification).toBe('recorded_evidence_only')
  expect(projection.threads[0].state).toBe('answered')
  expect(projection.threads[0].message_count).toBe(2)
  expect(projection.threads[0].latest.external_id).toBe('outgoing')
  expect(projection.people[0].care.state).toBe('current')
  const peopleResponse = await originalFetch(origin + base + '/people')
  expect((await peopleResponse.json()).people[0].care.state).toBe('current')
  const detailResponse = await originalFetch(origin + base + '/people/' + id)
  expect((await detailResponse.json()).care.state).toBe('current')
  cleanup()
})
