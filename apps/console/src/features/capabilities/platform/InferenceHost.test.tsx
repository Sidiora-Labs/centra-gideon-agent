import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import InferenceHost from './InferenceHost'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-inference-host-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/inference_host_ui_server.py'], {
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
it('configures starts and disarms an actual independently authenticated listener', async () => {
  render(<InferenceHost baseUrl={baseUrl} />)
  await screen.findByRole('option', { name: 'Unavailable inference' })
  expect(screen.getByText(/Listener stopped/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Configure loopback listener' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start inference listener' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Start inference listener' }))
  await screen.findByText(/Listening on 127.0.0.1/)
  const state = await (await fetch(`${baseUrl}/api/capabilities/platform/inference-host`)).json()
  expect(state.enabled).toBe(true)
  expect(state.peers).toEqual({ operator: 'peer-key' })
  expect(state.actual_port).toBeGreaterThan(0)
  const modelsUrl = `http://127.0.0.1:${state.actual_port}/v1/models`
  expect((await fetch(modelsUrl)).status).toBe(401)
  const models = await (await fetch(modelsUrl, { headers: { Authorization: 'Bearer qualification-listener-peer-secret' } })).json()
  expect(models.data[0].id).toBe('declared-model')
  expect(models.data[0].availability).toBe('not_probed')
  fireEvent.click(screen.getByRole('button', { name: 'Stop and disarm' }))
  await screen.findByText(/Listener stopped.*Disarmed/)
  expect(JSON.parse(await readFile(`${home}/capabilities/platform/inference_host.json`, 'utf8')).enabled).toBe(false)
  await expect(fetch(modelsUrl)).rejects.toThrow()
})
it('shows a real missing provider failure as unknown usage without claiming inference', async () => {
  render(<InferenceHost baseUrl={baseUrl} />)
  await screen.findByRole('option', { name: 'Unavailable inference' })
  fireEvent.click(screen.getByRole('button', { name: 'Start inference listener' }))
  await screen.findByText(/Listening on 127.0.0.1/)
  const state = await (await fetch(`${baseUrl}/api/capabilities/platform/inference-host`)).json()
  const response = await fetch(`http://127.0.0.1:${state.actual_port}/v1/chat/completions`, { method: 'POST', headers: { Authorization: 'Bearer qualification-listener-peer-secret', 'Content-Type':'application/json' }, body: JSON.stringify({ model: 'declared-model', messages: [{role:'user',content:'Do not persist this prompt'}] }) })
  expect(response.status).toBe(503)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh listener' }))
  expect(await screen.findByText('failed')).toBeVisible()
  expect(screen.getByText('Unknown / Unknown')).toBeVisible()
  const persisted = await readFile(`${home}/capabilities/platform/inference_host.json`, 'utf8')
  expect(persisted).not.toContain('Do not persist this prompt')
  expect(persisted).not.toContain('qualification-listener-peer-secret')
  fireEvent.click(screen.getByRole('button', { name: 'Stop and disarm' }))
  await screen.findByText(/Listener stopped.*Disarmed/)
})
it('configures an explicit supervised runtime and reports missing runtime files honestly', async () => {
  render(<InferenceHost baseUrl={baseUrl} />)
  await screen.findByRole('option', { name: 'Unavailable GPU runtime' })
  fireEvent.change(screen.getByRole('combobox', { name: 'Supervised runtime' }), { target: { value: 'Unavailable GPU runtime' } })
  fireEvent.click(screen.getByRole('button', { name: 'Configure loopback listener' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Provision runtime' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Provision runtime' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Configured inference runtime files are unavailable')
  fireEvent.click(screen.getByRole('button', { name: 'Check runtime readiness' }))
  await screen.findByText('Runtime status: stopped')
  expect(screen.getByText(/Supervised runtime Unavailable GPU runtime: stopped/)).toBeVisible()
})
