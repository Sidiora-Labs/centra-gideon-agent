import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import ArchivePage from './ArchivePage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'capture-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/archive_ui_server.py')], {
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

const exported = (id: string, invalid = false) => ({ id, title: `Conversation ${id}`, create_time: 1700000000, mapping: { root: { parent: null, message: null }, user: { parent: invalid ? 'absent' : 'root', message: { author: { role: 'user' }, create_time: 1700000000, content: { content_type: 'text', parts: ['An original question', { image: 'retained reference' }] } } }, answer: { parent: 'user', message: { author: { role: 'assistant' }, create_time: 1700000060, content: { content_type: 'text', parts: ['A historical response'] } } } } })

it('previews valid and invalid conversations, imports selected records and preserves original bytes', async () => {
  const raw = JSON.stringify([exported('first'), exported('invalid', true), exported('unselected')], null, 2)
  render(<ArchivePage />)
  await screen.findByText('No archive imports yet.')
  expect(screen.getByRole('button', { name: 'Preview archive' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Export JSON'), { target: { value: raw } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview archive' }))
  await screen.findByRole('region', { name: 'Conversation selection' })
  expect(screen.getByText('3 conversations found')).toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: 'Import Conversation invalid' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('missing parent')
  expect(screen.getByRole('button', { name: 'Import selected conversations' })).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: 'Import Conversation first' }))
  fireEvent.click(screen.getByRole('button', { name: 'Import selected conversations' }))
  const target = await screen.findByRole('link', { name: 'Open conversation first' })
  expect(target.getAttribute('href')).toMatch(/^#\/knowledge\/item\//)
  expect(screen.queryByRole('link', { name: 'Open conversation unselected' })).not.toBeInTheDocument()
  const history = await fetch('/api/capabilities/knowledge/archives').then(r => r.json())
  expect(history.total).toBe(1)
  expect(history.items[0].items).toHaveLength(1)
  const original = await fetch(`/api/capabilities/knowledge/archives/sources/${history.items[0].source_item_id}`)
  expect(original.status).toBe(200)
  expect(await original.text()).toBe(raw)
  expect(original.headers.get('content-disposition')).toContain('attachment')
  fireEvent.click(screen.getByRole('button', { name: 'Import selected conversations' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Import selected conversations' })).toBeEnabled())
  expect(screen.getAllByRole('link', { name: 'Open conversation first' })).toHaveLength(1)
  expect((await fetch('/api/capabilities/knowledge/archives').then(r => r.json())).total).toBe(1)
})

it('invalidates review when original JSON changes and shows real parsing errors', async () => {
  render(<ArchivePage />)
  await screen.findByRole('link', { name: 'Open conversation first' })
  fireEvent.change(screen.getByLabelText('Export JSON'), { target: { value: JSON.stringify([exported('second')]) } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview archive' }))
  await screen.findByRole('checkbox', { name: 'Import Conversation second' })
  fireEvent.change(screen.getByLabelText('Export JSON'), { target: { value: '[not json' } })
  expect(screen.queryByRole('region', { name: 'Conversation selection' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Preview archive' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Archive is not valid finite JSON')
  expect(screen.queryByRole('button', { name: 'Import selected conversations' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open conversation first' })).toBeInTheDocument()
})
