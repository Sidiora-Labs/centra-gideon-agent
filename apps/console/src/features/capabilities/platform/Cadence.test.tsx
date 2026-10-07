import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import Cadence from './Cadence'
let server: ChildProcess
let baseUrl: string
let home: string
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-cadence-`)
  const root = resolve(process.cwd(), '../..')
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/cadence_ui_server.py'], {
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
  expect((await nativeFetch(`${baseUrl}/api/capabilities/platform/cadence`)).status).toBe(403)
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
it('enables actual cadence policy and restores base interval on opt out', async () => {
  render(<Cadence baseUrl={baseUrl} />)
  expect(await screen.findByText('60s base → 60s effective')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Disable cadence Audit one' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Task class Audit one'), { target: { value: 'structural_audit' } })
  fireEvent.click(screen.getByRole('button', { name: 'Enable cadence Audit one' }))
  expect(await screen.findByText('60s base → 240s effective')).toBeVisible()
  expect(screen.getByText(/0\/5 successful/)).toHaveTextContent('observed')
  expect(screen.getByText(/0\/5 successful/)).toHaveTextContent('low_execution_success')
  fireEvent.click(screen.getByText('Execution evidence'))
  expect(screen.getByText('audit-one / 4: task failure')).toBeVisible()
  const enabled = await (await fetch(`${baseUrl}/api/capabilities/platform/cadence`)).json()
  expect(enabled.revision).toBe(1)
  expect(enabled.triggers[0].effective_interval).toBe(240)
  fireEvent.click(screen.getByRole('button', { name: 'Disable cadence Audit one' }))
  expect(await screen.findByText('60s base → 60s effective')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Disable cadence Audit one' })).toBeDisabled()
  const disabled = await (await fetch(`${baseUrl}/api/capabilities/platform/cadence`)).json()
  expect(disabled.revision).toBe(2)
  expect(disabled.triggers[0].task_class).toBeNull()
  expect(disabled.triggers[0].reason).toBe('not_opted_in')
})
it('rejects actual stale policy revision and retains unsaved class', async () => {
  render(<Cadence baseUrl={baseUrl} />)
  await screen.findByLabelText('Task class Audit one')
  const current = await (await fetch(`${baseUrl}/api/capabilities/platform/cadence`)).json()
  const changed = await fetch(`${baseUrl}/api/capabilities/platform/cadence/audit-one`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: current.revision, enabled: true, task_class: 'external_class' }) })
  expect(changed.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Task class Audit one'), { target: { value: 'unsaved_class' } })
  fireEvent.click(screen.getByRole('button', { name: 'Enable cadence Audit one' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('reload before saving')
  expect(screen.getByLabelText('Task class Audit one')).toHaveValue('unsaved_class')
  const persisted = await (await fetch(`${baseUrl}/api/capabilities/platform/cadence`)).json()
  expect(persisted.triggers[0].task_class).toBe('external_class')
  expect(persisted.revision).toBe(current.revision + 1)
})
