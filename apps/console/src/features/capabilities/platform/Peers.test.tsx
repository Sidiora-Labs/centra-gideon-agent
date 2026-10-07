import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Peers from './Peers'

let server: ChildProcess; let baseUrl: string; let home: string
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-peers-ui-`)
  const root = resolve(process.cwd(), '../..')
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/peers_ui_server.py'], { cwd: root, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] })
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
  expect((await nativeFetch(`${baseUrl}/api/capabilities/platform/peers`)).status).toBe(403)
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

const peerIdentity = async () => {
  const response = await fetch(`${baseUrl}/api/capabilities/platform/peers`)
  return (await response.json()).self as { peer_id: string; public_key: string }
}

it('creates, revises, reloads and removes an actual directional peer policy', async () => {
  render(<Peers baseUrl={baseUrl} />)
  expect(await screen.findByText('No direct peers configured.')).toBeVisible()
  const identity = await peerIdentity()
  fireEvent.change(screen.getByLabelText('Peer ID'), { target: { value: identity.peer_id } })
  fireEvent.change(screen.getByLabelText('Peer label'), { target: { value: 'Studio peer' } })
  fireEvent.change(screen.getByLabelText('Peer endpoint'), { target: { value: 'https://studio.example' } })
  fireEvent.change(screen.getByLabelText('Peer public key'), { target: { value: identity.public_key } })
  fireEvent.change(screen.getByLabelText('Send scopes'), { target: { value: 'experience.world_guest\nmedia.assets' } })
  fireEvent.change(screen.getByLabelText('Receive scopes'), { target: { value: 'experience.world_guest' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save peer policy' }))
  expect(await screen.findByRole('button', { name: 'Studio peer' })).toBeVisible()
  expect(screen.getByText(/send 2 \/ receive 1/)).toBeVisible()
  expect(screen.getByLabelText('Peer ID')).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Peer enabled'))
  fireEvent.change(screen.getByLabelText('Peer label'), { target: { value: 'Studio paused' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save peer policy' }))
  expect(await screen.findByRole('button', { name: 'Studio paused' })).toBeVisible()
  expect(screen.getByText(/disabled/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Remove peer' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Studio paused' })).not.toBeInTheDocument())
  expect(screen.getByText('No direct peers configured.')).toBeVisible()
})

it('keeps validation failures visible and preserves the draft', async () => {
  render(<Peers baseUrl={baseUrl} />)
  await screen.findByText('No direct peers configured.')
  fireEvent.change(screen.getByLabelText('Peer ID'), { target: { value: 'peer-invalid' } })
  fireEvent.change(screen.getByLabelText('Peer label'), { target: { value: 'Invalid peer' } })
  fireEvent.change(screen.getByLabelText('Peer endpoint'), { target: { value: 'file:///tmp/peer' } })
  fireEvent.change(screen.getByLabelText('Peer public key'), { target: { value: 'invalid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save peer policy' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/Invalid peer record/)
  expect(screen.getByLabelText('Peer label')).toHaveValue('Invalid peer')
  expect(screen.getByText('No direct peers configured.')).toBeVisible()
})
