import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import JournalsPage from './JournalsPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'capture-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/journals_ui_server.py')], {
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

it('reviews actual activity before saving and reopens the same canonical journal', async () => {
  render(<JournalsPage />)
  fireEvent.change(screen.getByLabelText('Journal date'), { target: { value: '2026-09-25' } })
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'UTC' } })
  fireEvent.click(screen.getByRole('button', { name: 'Open journal' }))
  await screen.findByRole('region', { name: 'Journal editor' })
  expect(screen.getByRole('button', { name: 'Save journal' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Journal text'), { target: { value: 'My reflection before the visit.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview activity draft' }))
  const source = await screen.findByRole('link', { name: 'Observatory planning' })
  expect(screen.getByLabelText('Journal text')).toHaveValue('My reflection before the visit.')
  fireEvent.click(screen.getByRole('button', { name: 'Append reviewed draft' }))
  expect((screen.getByLabelText('Journal text') as HTMLTextAreaElement).value).toContain('Observatory planning')
  fireEvent.click(screen.getByRole('button', { name: 'Save journal' }))
  await screen.findByText('Journal saved · revision 1')
  const first = await fetch('/api/capabilities/knowledge/journals?date=2026-09-25&timezone=UTC').then(r => r.json())
  expect(first.journal.content).toContain('My reflection before the visit.')
  expect(first.journal.content).toContain(source.getAttribute('href'))
  fireEvent.change(screen.getByLabelText('Journal text'), { target: { value: first.journal.content + '\nA later observation.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save journal' }))
  await screen.findByText('Journal saved · revision 2')
  const second = await fetch('/api/capabilities/knowledge/journals?date=2026-09-25&timezone=UTC').then(r => r.json())
  expect(second.journal.id).toBe(first.journal.id)
  expect(second.journal.content).toContain('A later observation.')
  fireEvent.click(screen.getByRole('button', { name: 'Open journal' }))
  await waitFor(() => expect(screen.getByLabelText('Journal text')).toHaveValue(second.journal.content))
  expect(screen.getByRole('link', { name: 'Open canonical journal' }).getAttribute('href')).toBe(second.journal.source_link)
})

it('preserves unsaved text when another real API editor commits first', async () => {
  render(<JournalsPage />)
  fireEvent.change(screen.getByLabelText('Journal date'), { target: { value: '2026-09-25' } })
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'UTC' } })
  fireEvent.click(screen.getByRole('button', { name: 'Open journal' }))
  await screen.findByRole('region', { name: 'Journal editor' })
  const opened = await fetch('/api/capabilities/knowledge/journals?date=2026-09-25&timezone=UTC').then(r => r.json())
  const external = await fetch('/api/capabilities/knowledge/journals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'ui-other-editor-write', date: '2026-09-25', timezone: 'UTC', revision: opened.journal?.revision || 0, fingerprint: opened.journal?.fingerprint || '', title: opened.journal?.title || 'Other editor journal', content: 'Another editor correction.', preview_id: '' }) })
  expect(external.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Journal text'), { target: { value: 'Unsaved local reflection.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save journal' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('reload before saving')
  expect(screen.getByLabelText('Journal text')).toHaveValue('Unsaved local reflection.')
  const actual = await fetch('/api/capabilities/knowledge/journals?date=2026-09-25&timezone=UTC').then(r => r.json())
  expect(actual.journal.content).toBe('Another editor correction.')
})

it('rejects an invalid timezone and never exposes a save operation before loading', async () => {
  render(<JournalsPage />)
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'Invalid/Timezone' } })
  fireEvent.click(screen.getByRole('button', { name: 'Open journal' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('IANA timezone')
  expect(screen.queryByRole('button', { name: 'Save journal' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Timezone')).toHaveValue('Invalid/Timezone')
})
