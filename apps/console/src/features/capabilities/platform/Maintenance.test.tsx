import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Maintenance from './Maintenance'
let server: ChildProcess
let baseUrl: string
let home: string
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-maintenance-`)
  const root = resolve(process.cwd(), '../..')
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/maintenance_ui_server.py'], {
    cwd: root, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  const ready = await new Promise<{ url: string; token: string }>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      try {
        const value = JSON.parse(line) as { url: string; token: string }
        if (typeof value.url !== 'string' || typeof value.token !== 'string') return
        accept(value); lines.close()
      } catch { /* Native readiness is the JSON line from this child. */ }
    })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
  baseUrl = ready.url
  expect((await nativeFetch(`${baseUrl}/api/capabilities/platform/maintenance`)).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), baseUrl)
    if (url.origin !== baseUrl) return nativeFetch(input, init)
    const headers = new Headers(init?.headers)
    headers.set('Authorization', `Bearer ${ready.token}`)
    return nativeFetch(url, { ...init, headers })
  }
})
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  await rm(home, { recursive: true, force: true })
})
async function configure() {
  const option = await screen.findByRole('option', { name: 'Maintenance project' })
  fireEvent.change(screen.getByLabelText('Maintenance project'), { target: { value: (option as HTMLOptionElement).value } })
  fireEvent.change(screen.getByLabelText('Maintenance verification command'), { target: { value: 'python -m pytest' } })
  fireEvent.change(screen.getByLabelText('Maintenance guard command'), { target: { value: 'git diff --check' } })
}
it('launches real supervised maintenance and cancels then resumes through HTTP', async () => {
  render(<Maintenance baseUrl={baseUrl} />)
  expect(screen.getByRole('button', { name: 'Start maintenance' })).toBeDisabled()
  await configure()
  expect(screen.getByText(/structural-drift → simplify/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Start maintenance' }))
  expect(await screen.findByRole('status')).toHaveTextContent('running')
  await waitFor(async () => {
    const value = await (await fetch(`${baseUrl}/api/capabilities/platform/maintenance`)).json()
    expect(value.runs[0].child_id).toBeTruthy()
  }, { timeout: 5000 })
  fireEvent.click(screen.getByRole('button', { name: 'Cancel maintenance for Maintenance project, stage 1' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('cancelled'), { timeout: 7000 })
  await waitFor(() => {
    const resume = screen.getByRole('button', { name: 'Resume maintenance for Maintenance project, stage 1' })
    expect(resume).not.toBeDisabled()
    expect(resume).not.toHaveAttribute('aria-disabled', 'true')
  })
  const before = await (await fetch(`${baseUrl}/api/capabilities/platform/maintenance`)).json()
  expect(before.runs[0].history[0].status).toBe('cancelled')
  fireEvent.click(screen.getByRole('button', { name: 'Resume maintenance for Maintenance project, stage 1' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('running'))
  const after = await (await fetch(`${baseUrl}/api/capabilities/platform/maintenance`)).json()
  expect(after.runs[0].child_id).not.toBe(before.runs[0].history[0].child_id)
  expect(after.runs[0].verify_command).toBe('python -m pytest')
  expect(after.runs[0].guard_command).toBe('git diff --check')
  expect(after.runs[0].stage).toBe(0)
}, 15000)
it('shows actual project overlap error without losing operator commands', async () => {
  render(<Maintenance baseUrl={baseUrl} />)
  await configure()
  fireEvent.click(screen.getByRole('button', { name: 'Start maintenance' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('already has active maintenance')
  expect(screen.getByLabelText('Maintenance verification command')).toHaveValue('python -m pytest')
  expect(screen.getByLabelText('Maintenance guard command')).toHaveValue('git diff --check')
  const value = await (await fetch(`${baseUrl}/api/capabilities/platform/maintenance`)).json()
  expect(value.runs).toHaveLength(1)
  expect(value.runs[0].status).toBe('running')
})
