import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import Page from './Page'

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
let server: ChildProcess
let baseUrl: string
const home = mkdtempSync(resolve(tmpdir(), 'gideon-shared-experience-'))

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/experience/serve_ui.py', home], {
    cwd: root,
    env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  baseUrl = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    const timer = setTimeout(() => reject(new Error(errors || 'experience HTTP startup timed out')), 20000)
    server.stderr?.on('data', value => { errors += String(value) })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`experience HTTP exited ${code}: ${errors}`)))
    server.stdout?.on('data', value => {
      output += String(value)
      try {
        const ready = acceptNativeReadiness(output)
        if (ready) { clearTimeout(timer); accept(ready) }
      } catch (error) { clearTimeout(timer); reject(error) }
    })
  })
})

afterAll(async () => { globalThis.fetch = networkFetch;
  cleanup()
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

it('renders real experience components over the shared registered HTTP application', async () => {
  window.history.replaceState(null, '', '#/capabilities/experience')
  render(<Page baseUrl={baseUrl} />)
  fireEvent.click(screen.getByRole('button', { name: 'Foundations' }))
  expect(await screen.findByText('0 foundations · 0 controllers')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Game assets' }))
  expect(await screen.findByText('0 game projects')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Native calls' }))
  expect(await screen.findByText('Native duplex audio unavailable')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Moltworld' }))
  expect(await screen.findByRole('heading', { name: 'Moltworld', level: 2 })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Moltbook' }))
  expect(await screen.findByRole('heading', { name: 'Moltbook', level: 2 })).toBeTruthy()
  expect(await screen.findByText('No Moltbook account configured')).toBeTruthy()
  const foundations = await fetch(baseUrl + '/world-foundations')
  expect(foundations.status).toBe(200)
  const snapshot = await foundations.json()
  expect(snapshot).toMatchObject({ schema_version: 1, foundations: [], controllers: [] })
  expect(snapshot.instance_id).toMatch(/^[a-f0-9]{24}$/)
  const projects = await fetch(baseUrl + '/game-assets/projects')
  expect(projects.status).toBe(200)
  expect(await projects.json()).toEqual({ projects: [], required_roles: ['sprite', 'artwork', 'music', 'model'] })
  expect((await fetch(baseUrl + '/native-duplex')).status).toBe(200)
  const moltworld = await fetch(baseUrl + '/moltworld')
  expect(moltworld.status).toBe(200)
  expect(await moltworld.json()).toMatchObject({ readiness: { protocol: 'moltworld-v1-2026-09-25', remote_status: 'unverified' }, history: [] })
  const moltbook = await fetch(baseUrl + '/moltbook/config')
  expect(moltbook.status).toBe(200)
  expect(await moltbook.json()).toMatchObject({ configured: false, registration_supported: false, external_qualified: false })
})
