import { patchRouteQuery } from '../../../app/shell/useHashRoute'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import CapabilitiesSection from '../CapabilitiesSection'

const nativeFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'gideon-knowledge-shell-'))
let server: ChildProcess, origin = '', token = ''

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/shell_ui_server.py')], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr?.on('data', chunk => { diagnostics += String(chunk) })
  const ready = await new Promise<{ port: number; token: string }>((accept, reject) => {
    let output = ''
    const timeout = setTimeout(() => reject(new Error(diagnostics || 'Knowledge shell server did not start')), 20000)
    server.on('exit', code => { clearTimeout(timeout); reject(new Error(`Knowledge shell server ${code}: ${diagnostics}`)) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      for (const line of output.split('\n')) try {
        const value = JSON.parse(line)
        if (value.port && value.token) { clearTimeout(timeout); accept(value) }
      } catch { /* startup logs */ }
    })
  })
  origin = `http://127.0.0.1:${ready.port}`
  token = ready.token
  globalThis.fetch = (input, init) => {
    if (typeof input !== 'string' || !input.startsWith('/')) return nativeFetch(input, init)
    return nativeFetch(origin + input + (input.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`, init)
  }
}, 30000)

afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server?.exitCode === null) {
    const stopped = new Promise<void>(done => server.once('exit', () => done()))
    server.kill('SIGTERM')
    await stopped
  }
  rmSync(home, { recursive: true, force: true })
})

it('navigates the assembled Knowledge shell and enforces authentication on both mounted routes', async () => {
  const navigate = vi.fn()
  const routeProps = { navEpoch: 0, query: {}, setQuery: (patch: Record<string, string | null | undefined>) => { location.hash = patchRouteQuery(location.hash, patch, 'capabilities') } }
  const view = render(<CapabilitiesSection {...routeProps} sub="knowledge/links" navigate={navigate} />)
  expect(screen.getByRole('status')).toHaveTextContent('Loading')
  expect(await screen.findByRole('heading', { name: 'Links and repository study' })).toBeVisible()
  const tools = screen.getByRole('navigation', { name: 'Area tools' })
  expect(within(tools).getByRole('link', { name: 'links' })).toHaveAttribute('href', '#/capabilities/knowledge/links')
  expect(within(tools).getByRole('link', { name: 'vaults' })).toHaveAttribute('href', '#/capabilities/knowledge/vaults')
  expect(screen.getByRole('region', { name: 'Link buckets' })).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()

  view.rerender(<CapabilitiesSection {...routeProps} sub="knowledge/vaults" navigate={navigate} />)
  expect(screen.getByRole('status')).toHaveTextContent('Loading')
  expect(await screen.findByRole('heading', { name: 'External knowledge vaults' })).toBeVisible()
  expect(screen.getByText('Allowed roots: None configured')).toBeVisible()
  expect(screen.getByRole('region', { name: 'Vault registration' })).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()

  for (const path of ['/api/capabilities/knowledge/links/buckets', '/api/capabilities/knowledge/vaults']) {
    const response = await nativeFetch(origin + path)
    expect([401, 403], path).toContain(response.status)
  }
})
