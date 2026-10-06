import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { createRequire } from 'node:module'
import { transferableAbortController } from 'node:util'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { RunningLoopsSection, TasksSection } from './CompanionSections'
import { resetDataStore } from '../../shared/data/data'

let process: ChildProcess
let base: string
let home: string
const fetchNetwork = globalThis.fetch
let stderr = ''
let restoreSocketEvents: (() => void) | undefined

const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
async function control(action: string, title?: string): Promise<{ reads: number; sockets: number }> {
  return (await fetchNetwork(base + '/fixture/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, title }) })).json()
}

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-companion-live-'))
  const workspace = resolve(home, 'workspace'); mkdirSync(workspace)
  const repo = resolve(import.meta.dirname, '../../../../..')
  process = spawn(globalThis.process.env.GIDEON_TEST_PYTHON || 'python3', [resolve(repo, 'checks/runtime/companion_live_fixture.py')], {
    cwd: repo, env: { ...globalThis.process.env, GIDEON_HOME: resolve(home, 'home'), GIDEON_WORKSPACE: workspace, PYTHONPATH: resolve(repo, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  process.stderr!.on('data', chunk => { stderr += chunk.toString() })
  const port = await new Promise<number>((resolvePort, reject) => {
    let data = ''
    const timeout = setTimeout(() => reject(new Error('Native fixture not ready: ' + stderr)), 15000)
    process.on('exit', code => { clearTimeout(timeout); reject(new Error(`Native fixture exited ${code}: ${stderr}`)) })
    process.stdout!.on('data', chunk => {
      data += chunk.toString()
      for (const line of data.split('\n')) {
        try { const row = JSON.parse(line); if (row.port) { clearTimeout(timeout); resolvePort(row.port) } } catch { /* wait for readiness line */ }
      }
    })
  })
  base = `http://127.0.0.1:${port}`
  // Real network adapters supply jsdom's browser URL resolution; no API or socket doubles.
  vi.stubGlobal('location', new URL(base))
  // Use jsdom's installed WebSocket client dependency for actual network traffic.
  // Its event classes remain in Node's realm; no generated frames or transport mocks.
  const require = createRequire(import.meta.url)
  const requireJsdom = createRequire(require.resolve('jsdom/package.json'))
  const NetworkSocket = requireJsdom('undici').WebSocket as typeof WebSocket
  let NetworkEvent: typeof Event | undefined
  const controller = transferableAbortController()
  controller.signal.addEventListener('abort', event => { NetworkEvent = event.constructor as typeof Event })
  controller.abort()
  if (!NetworkEvent) throw new Error('Node event realm unavailable')
  const dispatch = NetworkSocket.prototype.dispatchEvent
  NetworkSocket.prototype.dispatchEvent = function (event: Event): boolean {
    const normalized = new NetworkEvent!(event.type, { bubbles: event.bubbles, cancelable: event.cancelable })
    for (const key of ['data', 'origin', 'lastEventId', 'code', 'reason', 'wasClean']) {
      if (key in event) Object.defineProperty(normalized, key, { value: (event as unknown as Record<string, unknown>)[key] })
    }
    return dispatch.call(this, normalized)
  }
  restoreSocketEvents = () => { NetworkSocket.prototype.dispatchEvent = dispatch }
  vi.stubGlobal('WebSocket', NetworkSocket)
  vi.stubGlobal('fetch', (input: string | URL | Request, init?: RequestInit) => fetchNetwork(typeof input === 'string' ? new URL(input, base) : input, init))
}, 20000)

afterEach(() => { cleanup(); resetDataStore() })
afterAll(async () => {
  cleanup()
  if (base) await control('stop')
  if (process?.exitCode === null) await new Promise<void>(resolveExit => { process.once('exit', () => resolveExit()); setTimeout(() => { process.kill('SIGKILL'); resolveExit() }, 3000) })
  restoreSocketEvents?.()
  vi.unstubAllGlobals()
  if (home) rmSync(home, { recursive: true, force: true })
})

describe('native companion stays current through the real gateway transport', () => {
  it('renders the actual served incident hold and explicit resume without changing loop status', async () => {
    await control('incident-resume')
    render(<RunningLoopsSection />)
    await screen.findByText('Native companion loop')
    await screen.findByText(/Running · cycle/)
    await act(async () => {
      await control('incident-hold')
      window.dispatchEvent(new Event('focus'))
    })
    await screen.findByText(/Held · cycle/)
    const held = await (await fetchNetwork(base + '/api/loops')).json()
    expect(held.loops[0].status).toBe('running')
    expect(held.loops[0].held).toBeTruthy()
    await act(async () => {
      await control('incident-resume')
      window.dispatchEvent(new Event('focus'))
    })
    await screen.findByText(/Running · cycle/)
    const resumed = await (await fetchNetwork(base + '/api/loops')).json()
    expect(resumed.loops[0].status).toBe('running')
    expect(resumed.loops[0].held).toBe('')
  })

  it('follows task writes, coalesces bursts and recovers on focus and socket reconnect', async () => {
    const socketsBefore = (await control('count')).sockets
    render(<TasksSection />)
    await screen.findByText('Original native task')
    await pause(250)
    const initial = await control('count')
    expect(initial.sockets - socketsBefore).toBeLessThanOrEqual(1)
    expect(initial.sockets).toBeGreaterThan(0)
    await act(async () => { await control('change', 'Changed native task') })
    await screen.findByText('Changed native task')
    const before = await control('count')
    await act(async () => { await control('burst'); await pause(250) })
    const after = await control('count')
    expect(after.reads - before.reads).toBe(2) // one open/in-progress pair for the burst
    await control('quiet-change', 'Focus recovered task')
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await screen.findByText('Focus recovered task')
    await control('quiet-change', 'Reconnect recovered task')
    await act(async () => { await control('drop') })
    await screen.findByText('Reconnect recovered task', {}, { timeout: 5000 })
    expect((await control('count')).sockets).toBeGreaterThan(1)
  })

  it('fences a response already in flight when a task frame names newer data', async () => {
    await control('quiet-change', 'Old request task')
    await control('hold')
    render(<TasksSection />)
    await waitFor(async () => { expect((await control('count')).sockets).toBeGreaterThan(1) })
    await pause(40)
    await act(async () => { await control('change', 'Newest native task') })
    await screen.findByText('Newest native task')
    await act(async () => { await control('release'); await pause(100) })
    expect(screen.queryByText('Old request task')).toBeNull()
    expect(screen.getByText('Newest native task')).toBeInTheDocument()
    cleanup()
    const before = await control('count')
    await control('burst'); await pause(250)
    expect((await control('count')).reads).toBe(before.reads)
  })
})
