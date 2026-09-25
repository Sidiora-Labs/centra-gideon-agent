import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react'
import Harnesses from './Harnesses'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-harnesses-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/harnesses_ui_server.py'], {
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
it('renders real runner coverage, preserves invalid version and displays actual API rejection', async () => {
  render(<Harnesses baseUrl={baseUrl} />)
  const codex = within(await screen.findByRole('article', { name: 'Codex' }))
  expect(codex.getByText('No managed adapter installed')).toBeVisible()
  expect(codex.getByRole('button', { name: 'Install Codex' })).toBeDisabled()
  expect(within(screen.getByRole('article', { name: 'Gemini CLI' })).getByText('This runner has no managed adapter package.')).toBeVisible()
  fireEvent.change(codex.getByLabelText('Codex exact version'), { target: { value: 'latest' } })
  fireEvent.click(codex.getByRole('button', { name: 'Install Codex' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('exact package version')
  expect(codex.getByLabelText('Codex exact version')).toHaveValue('latest')
  const data = await (await fetch(`${baseUrl}/api/capabilities/platform/harnesses`)).json()
  expect(data.harnesses.find((row: { id: string }) => row.id === 'codex').installed).toBeNull()
})
it('installs and removes a real versioned adapter through rendered controls', async () => {
  render(<Harnesses baseUrl={baseUrl} />)
  const codex = within(await screen.findByRole('article', { name: 'Codex' }))
  fireEvent.change(codex.getByLabelText('Codex exact version'), { target: { value: '1.13.1' } })
  fireEvent.click(codex.getByRole('button', { name: 'Install Codex' }))
  await waitFor(() => expect(codex.getByText('Installed 1.13.1')).toBeVisible(), { timeout: 180000 })
  expect(codex.getByRole('button', { name: 'Update Codex' })).toBeVisible()
  const data = await (await fetch(`${baseUrl}/api/capabilities/platform/harnesses`)).json()
  expect(data.harnesses.find((row: { id: string }) => row.id === 'codex').installed.integrity).toMatch(/^sha512-/)
  fireEvent.click(codex.getByRole('button', { name: 'Remove Codex' }))
  await waitFor(() => expect(codex.getByText('No managed adapter installed')).toBeVisible(), { timeout: 180000 })
  expect(codex.queryByRole('button', { name: 'Remove Codex' })).toBeNull()
  expect(codex.getByRole('button', { name: 'Install Codex' })).toBeVisible()
}, 240000)
