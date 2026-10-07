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
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-ownership-`)
  const root = resolve(process.cwd(), '../..')
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/ownership_ui_server.py'], {
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
  expect((await nativeFetch(`${baseUrl}/api/capabilities/platform/ownership`)).status).toBe(403)
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
async function select(feature: string) {
  const option = await screen.findByRole('option', { name: 'Feature project' })
  fireEvent.change(screen.getByLabelText('Ownership project'), { target: { value: (option as HTMLOptionElement).value } })
  fireEvent.change(screen.getByLabelText('Ownership feature'), { target: { value: feature } })
}
it('claims, reloads and releases persistent ownership through real HTTP with history', async () => {
  const rendered = render(<Ownership baseUrl={baseUrl} />)
  await select('editor')
  fireEvent.click(screen.getByRole('button', { name: 'Claim feature' }))
  await waitFor(() => {
    const release = screen.getByRole('button', { name: 'Release feature' })
    expect(release).not.toBeDisabled()
    expect(release).not.toHaveAttribute('aria-disabled', 'true')
  })
  expect(screen.getByRole('button', { name: 'Claim feature' })).toBeDisabled()
  expect(await screen.findByRole('list', { name: 'Ownership history' })).toHaveTextContent('claim')
  const data = await (await fetch(`${baseUrl}/api/capabilities/platform/ownership`)).json()
  expect(data.ownership[0].owner).toBe(data.actor)
  expect(data.ownership[0].revision).toBe(1)
  rendered.unmount()
  render(<Ownership baseUrl={baseUrl} />)
  await select('editor')
  await waitFor(() => {
    const release = screen.getByRole('button', { name: 'Release feature' })
    expect(release).not.toBeDisabled()
    expect(release).not.toHaveAttribute('aria-disabled', 'true')
  })
  fireEvent.click(screen.getByRole('button', { name: 'Release feature' }))
  await waitFor(() => {
    const claim = screen.getByRole('button', { name: 'Claim feature' })
    expect(claim).not.toBeDisabled()
    expect(claim).not.toHaveAttribute('aria-disabled', 'true')
  })
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
