import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import TypesPage from './TypesPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'capture-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/types_ui_server.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'Capture application startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Capture application exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => {
      buffer += String(chunk)
      for (const line of buffer.split('\n')) {
        if (!line.startsWith('{"port":')) continue
        try { const { port } = JSON.parse(line); clearTimeout(timeout); done(`http://127.0.0.1:${port}`) } catch { /* Wait for complete address. */ }
      }
    })
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
}, 20000)
afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null) {
    const stopped = new Promise<void>(done => child.once('exit', () => done()))
    child.kill('SIGTERM'); await stopped
  }
  rmSync(home, { recursive: true, force: true })
})

it('reviews unsupported fields, creates a canonical idea and retries without duplicates', async () => {
  const created = await fetch('/api/capabilities/knowledge/captures', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'ui-typed-original', text: 'A source idea for review' }) }).then(r => r.json())
  render(<TypesPage />)
  await screen.findByRole('option', { name: 'A source idea for review' })
  expect(screen.getByRole('button', { name: 'Review mapping' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Original capture'), { target: { value: created.id } })
  fireEvent.change(screen.getByLabelText('Original fields (JSON)'), { target: { value: JSON.stringify({ title: 'A reviewed idea', content: 'Canonical content', unsupported: { original: true } }) } })
  fireEvent.click(screen.getByRole('button', { name: 'Review mapping' }))
  await screen.findByRole('region', { name: 'Mapping review' })
  expect(screen.getByText(/Unmapped original fields: unsupported/)).toBeInTheDocument()
  expect(screen.getByText('Destination: fleeting')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed record' }))
  const link = await screen.findByRole('link', { name: 'Open idea' })
  expect(link.getAttribute('href')).toMatch(/^#\/knowledge\/item\//)
  const history = await fetch('/api/capabilities/knowledge/types').then(r => r.json())
  expect(history.total).toBe(1)
  expect(history.items[0].original_fields.unsupported).toEqual({ original: true })
  expect(history.items[0].original_capture.text).toBe('A source idea for review')
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed record' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Import reviewed record' })).toBeEnabled())
  expect(screen.getAllByRole('link', { name: 'Open idea' })).toHaveLength(1)
  expect((await fetch('/api/capabilities/knowledge/types').then(r => r.json())).total).toBe(1)
  fireEvent.change(screen.getByLabelText('Original fields (JSON)'), { target: { value: '{bad json' } })
  expect(screen.queryByRole('region', { name: 'Mapping review' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Review mapping' }))
  expect(await screen.findByRole('alert')).toBeInTheDocument()
})

it('shows actual memory unavailability without enabling a false import', async () => {
  const created = await fetch('/api/capabilities/knowledge/captures', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'ui-memory-original', text: 'An original memory' }) }).then(r => r.json())
  render(<TypesPage />)
  await screen.findByRole('option', { name: 'An original memory' })
  fireEvent.change(screen.getByLabelText('Original capture'), { target: { value: created.id } })
  fireEvent.change(screen.getByLabelText('Record type'), { target: { value: 'memory' } })
  fireEvent.change(screen.getByLabelText('Original fields (JSON)'), { target: { value: JSON.stringify({ text: 'A remembered journey to the coast' }) } })
  fireEvent.click(screen.getByRole('button', { name: 'Review mapping' }))
  expect(await screen.findByRole('status')).toHaveTextContent('The bound memory service is unavailable')
  expect(screen.getByRole('button', { name: 'Import reviewed record' })).toBeDisabled()
  expect(screen.queryByRole('link', { name: 'Open memory' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open capture inbox' })).toHaveAttribute('href', '#/capabilities/knowledge/capture')
})
