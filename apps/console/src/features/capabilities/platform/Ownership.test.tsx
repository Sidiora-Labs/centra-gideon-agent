import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Ownership from './Ownership'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-ownership-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/ownership_ui_server.py'], {
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
async function select(feature: string) {
  const option = await screen.findByRole('option', { name: 'Feature project' })
  fireEvent.change(screen.getByLabelText('Ownership project'), { target: { value: (option as HTMLOptionElement).value } })
  fireEvent.change(screen.getByLabelText('Ownership feature'), { target: { value: feature } })
}
it('claims, reloads and releases persistent ownership through real HTTP with history', async () => {
  const rendered = render(<Ownership baseUrl={baseUrl} />)
  await select('editor')
  fireEvent.click(screen.getByRole('button', { name: 'Claim feature' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Release feature' })).not.toBeDisabled())
  expect(screen.getByRole('button', { name: 'Claim feature' })).toBeDisabled()
  expect(await screen.findByRole('list', { name: 'Ownership history' })).toHaveTextContent('claim')
  const data = await (await fetch(`${baseUrl}/api/capabilities/platform/ownership`)).json()
  expect(data.ownership[0].owner).toBe(data.actor)
  expect(data.ownership[0].revision).toBe(1)
  rendered.unmount()
  render(<Ownership baseUrl={baseUrl} />)
  await select('editor')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Release feature' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Release feature' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Claim feature' })).not.toBeDisabled())
  expect(screen.getByRole('list', { name: 'Ownership history' })).toHaveTextContent('release')
  const released = await (await fetch(`${baseUrl}/api/capabilities/platform/ownership`)).json()
  expect(released.ownership[0].owner).toBeNull()
  expect(released.ownership[0].revision).toBe(2)
})
it('keeps invalid feature input and displays actual server rejection', async () => {
  render(<Ownership baseUrl={baseUrl} />)
  await select('invalid feature')
  fireEvent.click(screen.getByRole('button', { name: 'Claim feature' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid feature')
  expect(screen.getByLabelText('Ownership feature')).toHaveValue('invalid feature')
  const data = await (await fetch(`${baseUrl}/api/capabilities/platform/ownership`)).json()
  expect(data.ownership).toHaveLength(1)
})
