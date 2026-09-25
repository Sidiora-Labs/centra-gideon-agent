import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import Page from './Page'

let server: ChildProcess
let baseUrl: string
const home = mkdtempSync(resolve(tmpdir(), 'gideon-shared-experience-'))

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_ui.py', home], {
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
      const origin = output.match(/^http:\/\/127\.0\.0\.1:\d+$/m)?.[0]
      if (origin) { clearTimeout(timer); accept(origin + '/api/capabilities/experience') }
    })
  })
})

afterAll(async () => {
  cleanup()
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

it('renders real experience components over the shared registered HTTP application', async () => {
  window.history.replaceState(null, '', '#/capabilities/experience')
  render(<Page baseUrl={baseUrl} />)
  expect(await screen.findByText('0 foundations · 0 controllers')).toBeTruthy()
  expect(await screen.findByText('0 game projects')).toBeTruthy()
  expect(await screen.findByText('Native duplex audio unavailable')).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'World foundations and controllers' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Game asset compiler' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Native duplex audio' })).toBeTruthy()
  const foundations = await fetch(baseUrl + '/world-foundations')
  expect(foundations.status).toBe(200)
  const snapshot = await foundations.json()
  expect(snapshot).toMatchObject({ schema_version: 1, foundations: [], controllers: [] })
  expect(snapshot.instance_id).toMatch(/^[a-f0-9]{24}$/)
  const projects = await fetch(baseUrl + '/game-assets/projects')
  expect(projects.status).toBe(200)
  expect(await projects.json()).toEqual({ projects: [], required_roles: ['sprite', 'artwork', 'music', 'model'] })
  expect((await fetch(baseUrl + '/native-duplex')).status).toBe(200)
})
