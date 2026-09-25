import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import ReviewsPage from './ReviewsPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'capture-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/reviews_ui_server.py')], {
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

it('previews real sources, saves reflection and reopens the canonical source snapshot', async () => {
  render(<ReviewsPage />)
  fireEvent.change(screen.getByLabelText('Review date'), { target: { value: '2026-09-25' } })
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'UTC' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview review' }))
  const source = await screen.findByRole('link', { name: 'Observatory planning' })
  expect(source.getAttribute('href')).toMatch(/^#\/knowledge\/item\//)
  expect(screen.getByText(/not an immutable completion event/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Reflection'), { target: { value: 'Reserve the telescope tomorrow.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save review' }))
  const saved = await screen.findByRole('link', { name: 'Open saved review' })
  const history = await fetch('/api/capabilities/knowledge/reviews').then(r => r.json())
  expect(history.total).toBe(1)
  expect(saved.getAttribute('href')).toBe(history.items[0].source_link)
  const original = await fetch(`/api/capabilities/knowledge/sources/note/${history.items[0].destination_id}`).then(r => r.json())
  expect(original.content).toContain('Reserve the telescope tomorrow.')
  expect(original.content).toContain(source.getAttribute('href'))
  expect(screen.getByRole('button', { name: 'Save review' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Period'), { target: { value: 'weekly' } })
  expect(screen.queryByRole('region', { name: 'Review preview' })).not.toBeInTheDocument()
})

it('creates, edits and disables an actual recurring clock schedule', async () => {
  render(<ReviewsPage />)
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'UTC' } })
  fireEvent.change(screen.getByLabelText('Period'), { target: { value: 'weekly' } })
  fireEvent.change(screen.getByLabelText('Weekday'), { target: { value: '4' } })
  fireEvent.change(screen.getByLabelText('Schedule time'), { target: { value: '18:30' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }))
  await screen.findByText(/Next fire:/)
  const first = await fetch('/api/capabilities/knowledge/reviews/schedules').then(r => r.json())
  expect(first.items).toHaveLength(1)
  expect(first.items[0].weekday).toBe(4)
  expect(first.items[0].period).toBe('weekly')
  expect(first.items[0].enabled).toBe(true)
  fireEvent.click(screen.getByLabelText('Schedule enabled'))
  fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }))
  await screen.findByText('Schedule disabled')
  const second = await fetch('/api/capabilities/knowledge/reviews/schedules').then(r => r.json())
  expect(second.items[0].id).toBe(first.items[0].id)
  expect(second.items[0].revision).toBe(2)
  expect(second.items[0].next_fire_at).toBe('')
  expect(second.items[0].enabled).toBe(false)
})

it('preserves inputs on actual calendar validation failure', async () => {
  render(<ReviewsPage />)
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'Unknown/Zone' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview review' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('IANA timezone')
  expect(screen.getByLabelText('Timezone')).toHaveValue('Unknown/Zone')
  expect(screen.queryByRole('region', { name: 'Review preview' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save review' })).not.toBeInTheDocument()
})
