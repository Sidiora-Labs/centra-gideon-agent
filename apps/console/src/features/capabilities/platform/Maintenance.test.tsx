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
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-maintenance-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/maintenance_ui_server.py'], {
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
  fireEvent.click(screen.getByRole('button', { name: 'Cancel maintenance' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('cancelled'), { timeout: 7000 })
  expect(screen.getByRole('button', { name: 'Resume maintenance' })).not.toBeDisabled()
  const before = await (await fetch(`${baseUrl}/api/capabilities/platform/maintenance`)).json()
  expect(before.runs[0].history[0].status).toBe('cancelled')
  fireEvent.click(screen.getByRole('button', { name: 'Resume maintenance' }))
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
