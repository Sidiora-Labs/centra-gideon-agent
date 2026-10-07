import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import WorldEngine from './WorldEngine'
const networkFetch = globalThis.fetch
function acceptNativeReadiness(output: string) {
  const line = output.split('\n').find(value => value.startsWith('{'))
  if (!line) return undefined
  const ready: { port: number; token: string } = JSON.parse(line)
  if (!Number.isInteger(ready.port) || !ready.token) throw new Error('Invalid native readiness')
  const origin = `http://127.0.0.1:${ready.port}`
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), origin)
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
    if (url.origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
    return networkFetch(url, { ...init, headers })
  }
  return origin + '/api/capabilities/experience'
}
let child: ChildProcess
let home: string
let base: string
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-navigation-ui-'))
  child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/experience/serve_ui.py', home], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  base = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', data => { output += String(data); if (output.includes('\n')) { try { const ready = acceptNativeReadiness(output); if (ready) accept(ready) } catch (error) { reject(error) } } })
    child.stderr!.on('data', data => { errors += String(data) })
    child.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    child.on('error', reject)
  })
})
afterAll(async () => { globalThis.fetch = networkFetch; child?.kill(); await rm(home, { recursive: true, force: true }) })
it('displays actual missing engine readiness and never exposes an unavailable iframe', async () => {
  render(<WorldEngine baseUrl={base} />)
  await screen.findByRole('status')
  expect(screen.getByRole('status')).toHaveTextContent('unavailable')
  expect(screen.getByRole('button', { name: 'Start world engine' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Stop world engine' })).toBeDisabled()
  const unavailableOpen = screen.getByRole('button', { name: 'Open world' })
  expect(unavailableOpen).toHaveAttribute('aria-disabled', 'true')
  expect(unavailableOpen).toHaveAccessibleDescription('The world engine has no available URL yet')
  unavailableOpen.focus()
  expect(unavailableOpen).toHaveFocus()
  fireEvent.click(unavailableOpen)
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
  const status = await (await fetch(base + '/world-engine')).json()
  expect(status.state).toBe('unavailable')
  expect(status.version).toBeNull()
  expect(status.engine_url).toBeNull()
  expect(screen.getByText('The world engine runs as a separately installed local application.')).toBeVisible()
})
it('refreshes authoritative status and keeps operator configuration outside customer controls', async () => {
  render(<WorldEngine baseUrl={base} />)
  await screen.findByRole('status')
  const before = await (await fetch(base + '/world-engine')).json()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh world engine' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(before.reason))
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  const response = await fetch(base + '/world-engine/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target: 'http://other.invalid' }) })
  expect(response.status).toBe(400)
  const after = await (await fetch(base + '/world-engine')).json()
  expect(after).toEqual(before)
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
  const unavailableOpen = screen.getByRole('button', { name: 'Open world' })
  expect(unavailableOpen).toHaveAttribute('aria-disabled', 'true')
  expect(unavailableOpen).toHaveAccessibleDescription('The world engine has no available URL yet')
  unavailableOpen.focus()
  expect(unavailableOpen).toHaveFocus()
  fireEvent.click(unavailableOpen)
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
})
it('does not convert a successful unavailable start response into running UI', async () => {
  const response = await fetch(base + '/world-engine/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  expect(response.status).toBe(200)
  const status = await response.json()
  expect(status.state).toBe('unavailable')
  render(<WorldEngine baseUrl={base} />)
  await screen.findByRole('status')
  expect(screen.getByRole('status')).toHaveTextContent(status.reason)
  expect(screen.queryByText(/Actual managed world engine answered/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Stop world engine' })).toBeDisabled()
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
  const proxy = await fetch(base + '/world-engine/host/version')
  expect(proxy.status).toBe(409)
})
