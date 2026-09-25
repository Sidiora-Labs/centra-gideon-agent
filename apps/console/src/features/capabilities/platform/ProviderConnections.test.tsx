import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { useState } from 'react'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProviderConnections } from './ProviderConnections'

let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-connections-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/connections_ui_server.py'], {
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
function Wrapper() {
  const [selected, setSelected] = useState('')
  return <ProviderConnections selected={selected} onSelect={setSelected} baseUrl={baseUrl} />
}
const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

it('creates, edits, binds, unbinds and deletes through the real HTTP store', async () => {
  render(<Wrapper />)
  expect(await screen.findByText('No shared connections configured.')).toBeVisible()
  fill('Connection ID', 'work')
  fill('Connection label', 'Work account')
  fill('Endpoint', 'https://example.invalid/v1')
  fill('Model access', 'allow')
  fill('Model patterns', 'alpha*')
  fireEvent.click(screen.getByRole('button', { name: 'Save connection' }))
  expect(await screen.findByRole('button', { name: 'Work account' })).toBeVisible()
  await waitFor(() => expect(screen.getByLabelText('Connection ID')).toBeDisabled())
  fill('Provider to bind', 'primary')
  fireEvent.click(screen.getByRole('button', { name: 'Bind provider' }))
  expect(await screen.findByRole('button', { name: 'Unbind primary' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Delete connection' })).toHaveAttribute('aria-disabled', 'true')
  const inventory = await (await fetch(`${baseUrl}/api/capabilities/platform/connections`)).json()
  expect(inventory.connections[0].bindings).toEqual(['primary'])
  expect(inventory.connections[0].model_access).toEqual({ mode: 'allow', patterns: ['alpha*'] })
  fill('Endpoint', 'https://changed.invalid/v1')
  fill('Model access', 'all')
  fireEvent.click(screen.getByRole('button', { name: 'Save connection' }))
  await waitFor(async () => {
    const changed = await (await fetch(`${baseUrl}/api/capabilities/platform/connections`)).json()
    expect(changed.connections[0].base_url).toBe('https://changed.invalid/v1')
  })
  fireEvent.click(screen.getByRole('button', { name: 'Unbind primary' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Unbind primary' })).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: 'Delete connection' }))
  expect(await screen.findByText('No shared connections configured.')).toBeVisible()
  const removed = await (await fetch(`${baseUrl}/api/capabilities/platform/connections`)).json()
  expect(removed.connections).toEqual([])
  expect(removed.providers[0].name).toBe('primary')
})

it('keeps invalid input and displays the actual server rejection', async () => {
  render(<Wrapper />)
  await screen.findByText('No shared connections configured.')
  fill('Connection ID', 'unsafe')
  fill('Connection label', 'Rejected endpoint')
  fill('Endpoint', 'https://example.invalid/v1?key=secret')
  fireEvent.click(screen.getByRole('button', { name: 'Save connection' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('without credentials or query parameters')
  expect(screen.getByLabelText('Endpoint')).toHaveValue('https://example.invalid/v1?key=secret')
  const inventory = await (await fetch(`${baseUrl}/api/capabilities/platform/connections`)).json()
  expect(inventory.connections).toEqual([])
  expect(screen.queryByRole('button', { name: 'Rejected endpoint' })).toBeNull()
})
