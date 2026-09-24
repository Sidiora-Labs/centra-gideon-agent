import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import CapturePage from './CapturePage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'capture-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/capture_ui_server.py')], {
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

it('captures original text, routes edited review and reopens the same correction history', async () => {
  window.history.replaceState(null, '', '#/capabilities/knowledge/capture')
  const view = render(<CapturePage />)
  await screen.findByText('No captures yet.')
  expect(screen.getByRole('button', { name: 'Save text' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Capture text'), { target: { value: 'An original thought from this morning' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save text' }))
  await screen.findByRole('region', { name: 'Review capture' })
  expect(screen.getByText('Original: An original thought from this morning')).toBeInTheDocument()
  const originalHash = window.location.hash
  expect(originalHash).toContain('?capture=')
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'My reviewed note' } })
  fireEvent.change(screen.getByLabelText('Reviewed text'), { target: { value: 'The revised and organized content' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save reviewed destination' }))
  const saved = await screen.findByRole('link', { name: 'Open saved knowledge' })
  const link = saved.getAttribute('href')
  expect(link).toMatch(/^#\/knowledge\/item\/[a-f0-9-]+$/)
  await waitFor(() => expect(screen.getByLabelText('Reviewed text')).toHaveValue('The revised and organized content'))
  fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'fleeting' } })
  fireEvent.change(screen.getByLabelText('Reviewed text'), { target: { value: 'A second deliberate correction' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save reviewed destination' }))
  await screen.findByText(/2 routing revisions/)
  expect(screen.getByRole('link', { name: 'Open saved knowledge' })).toHaveAttribute('href', link)
  expect(screen.getByText('Original: An original thought from this morning')).toBeInTheDocument()
  view.unmount()
  render(<CapturePage />)
  await waitFor(() => expect(screen.getByLabelText('Reviewed text')).toHaveValue('A second deliberate correction'))
  expect(screen.getByLabelText('Destination')).toHaveValue('fleeting')
  expect(window.location.hash).toBe(originalHash)
  expect(screen.getByLabelText('Title')).toHaveValue('My reviewed note')
})

it('surfaces a real missing-capture error without inventing content', async () => {
  window.history.replaceState(null, '', '#/capabilities/knowledge/capture?capture=missing-source')
  render(<CapturePage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Capture not found')
  expect(screen.queryByRole('region', { name: 'Review capture' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Reload captures' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Save recording' })).toBeDisabled()
})
