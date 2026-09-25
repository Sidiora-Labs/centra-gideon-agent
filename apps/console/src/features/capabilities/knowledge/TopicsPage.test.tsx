import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import TopicsPage from './TopicsPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'capture-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/topics_ui_server.py')], {
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

it('creates and edits a topic, refreshes real evidence, reports unavailable memory and deletes only the topic', async () => {
  render(<TopicsPage />)
  await screen.findByRole('navigation', { name: 'Saved topics' })
  expect(screen.getByRole('button', { name: 'Save topic' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Topic name'), { target: { value: 'Evening observations' } })
  fireEvent.change(screen.getByLabelText('Keyword query'), { target: { value: 'observatory' } })
  fireEvent.click(screen.getByLabelText('memory'))
  fireEvent.click(screen.getByRole('button', { name: 'Save topic' }))
  const link = await screen.findByRole('link', { name: 'Observatory planning' })
  expect(link.getAttribute('href')).toMatch(/^#\/knowledge\/item\//)
  expect(screen.getByText('Book the telescope before sunset.')).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('memory source unavailable')
  expect(screen.getByText('1 matches in the available scanned records')).toBeInTheDocument()
  const data = await fetch('/api/capabilities/knowledge/topics').then(r => r.json())
  expect(data.total).toBe(1)
  const topicId = data.items[0].id
  const evidence = await fetch(`/api/capabilities/knowledge/topics/${topicId}/matches`).then(r => r.json())
  const sourceId = evidence.items[0].source_id
  fireEvent.click(screen.getByRole('button', { name: 'Refresh sources' }))
  await screen.findByRole('link', { name: 'Observatory planning' })
  fireEvent.change(screen.getByLabelText('Keyword query'), { target: { value: 'different keyword' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save topic' }))
  await screen.findByText('0 matches in the available scanned records')
  expect(screen.queryByRole('link', { name: 'Observatory planning' })).not.toBeInTheDocument()
  expect((await fetch('/api/capabilities/knowledge/topics').then(r => r.json())).items[0].revision).toBe(2)
  fireEvent.click(screen.getByRole('button', { name: 'Delete topic' }))
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Topic evidence' })).not.toBeInTheDocument())
  expect((await fetch('/api/capabilities/knowledge/topics').then(r => r.json())).total).toBe(0)
  const original = await fetch(`/api/capabilities/knowledge/sources/note/${sourceId}`)
  expect(original.status).toBe(200)
  expect((await original.json()).content).toBe('Book the telescope before sunset.')
})

it('shows real validation errors while preserving the editable topic draft', async () => {
  render(<TopicsPage />)
  fireEvent.change(screen.getByLabelText('Topic name'), { target: { value: 'Invalid punctuation query' } })
  fireEvent.change(screen.getByLabelText('Keyword query'), { target: { value: '---' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save topic' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('searchable words')
  expect(screen.getByLabelText('Topic name')).toHaveValue('Invalid punctuation query')
  expect(screen.getByLabelText('Keyword query')).toHaveValue('---')
  expect(screen.queryByRole('region', { name: 'Topic evidence' })).not.toBeInTheDocument()
  expect((await fetch('/api/capabilities/knowledge/topics').then(r => r.json())).total).toBe(0)
})
