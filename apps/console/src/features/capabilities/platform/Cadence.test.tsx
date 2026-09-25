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
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-cadence-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/cadence_ui_server.py'], {
    cwd: root, env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_DEV_NO_AUTH: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  baseUrl = await new Promise<string>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => { if (/^\d+$/.test(line)) { accept(`http://127.0.0.1:${line}`); lines.close() } })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
})
afterAll(async () => {
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
