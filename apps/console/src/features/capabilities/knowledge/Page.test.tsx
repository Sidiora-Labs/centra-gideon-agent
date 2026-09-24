import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Page from './Page'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'knowledge-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/ui_server.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'HTTP application startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`HTTP application exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => {
      buffer += String(chunk)
      for (const line of buffer.split('\n')) {
        if (!line.startsWith('{"port":')) continue
        try { const { port } = JSON.parse(line); clearTimeout(timeout); done(`http://127.0.0.1:${port}`) } catch { /* Wait for complete line. */ }
      }
    })
  })
  // Resolve browser-relative requests to a real local server, without substituting responses.
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
}, 20000)
afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null) {
    const stopped = new Promise<void>(done => child.once('exit', () => done()))
    child.kill('SIGTERM')
    await stopped
  }
  rmSync(home, { recursive: true, force: true })
})

it('reads actual historical records and follows canonical source links across pages', async () => {
  window.history.replaceState(null, '', '#/capabilities/knowledge?date=2026-09-25&timezone=UTC')
  render(<Page />)
  await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(20))
  const first = screen.getAllByRole('link')[0]
  const title = first.textContent!
  expect(first.getAttribute('href')).toMatch(/^#\/knowledge\/item\/[a-f0-9-]+$/)
  expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3))
  expect(screen.queryByText(title)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
  await screen.findByText(title)
  expect(screen.getAllByRole('listitem')).toHaveLength(20)
})

it('recovers from actual validation errors and displays a real empty date', async () => {
  window.history.replaceState(null, '', '#/capabilities/knowledge?date=2026-09-25&timezone=Invalid')
  render(<Page />)
  expect(await screen.findByRole('alert')).toHaveTextContent('timezone')
  expect(screen.queryAllByRole('listitem')).toHaveLength(0)
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'UTC' } })
  await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(20))
  fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-09-26' } })
  await screen.findByText('No anniversaries on this date.')
  expect(screen.queryAllByRole('listitem')).toHaveLength(0)
  expect(window.location.hash).toContain('date=2026-09-26')
  expect(screen.getByText('Memory history is not connected. Showing your knowledge library.')).toBeInTheDocument()
})
