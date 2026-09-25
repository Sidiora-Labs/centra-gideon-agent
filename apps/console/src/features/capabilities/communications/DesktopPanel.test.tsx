import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, execFileSync, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { DesktopPanel } from './DesktopPanel'

let server: ChildProcess
let origin: string
let home: string
const originalFetch = globalThis.fetch
const base = '/api/capabilities/communications'

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-thread-ui-'))
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/communications/ui_server.py'], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    let output = ''
    let errors = ''
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('{"port":'))
      if (line) resolveOrigin(`http://127.0.0.1:${JSON.parse(line).port}`)
    })
    server.on('error', reject)
    server.on('exit', code => reject(new Error(`HTTP server exited ${code}: ${errors}`)))
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
})

afterAll(() => {
  cleanup()
  globalThis.fetch = originalFetch
  server?.kill()
  rmSync(home, { recursive: true, force: true })
})

async function post(path: string, body: unknown) {
  const response = await originalFetch(origin + base + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  expect(response.ok).toBe(true)
  return response.json()
}

function snapshot(source: string) {
  const root = resolve(process.cwd(), '../..')
  const encoded = execFileSync(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', `import base64; from checks.runtime.capabilities.communications.test_desktop import snapshot; print(base64.b64encode(snapshot('${source}')).decode())`], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, encoding: 'utf8' }).trim()
  return new File([Buffer.from(encoded, 'base64')], 'snapshot.sqlite', { type: 'application/octet-stream' })
}

it('previews and commits an actual iMessage SQLite file and reads durable history', async () => {
  await post('/people', { name: 'Desktop UI friend', identities: [{ kind: 'phone', value: '+12025550123' }] })
  render(<DesktopPanel />)
  expect(screen.getByText(/Encrypted Signal databases require/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Preview desktop snapshot' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Desktop source account'), { target: { value: 'ui-desktop' } })
  fireEvent.change(screen.getByLabelText('SQLite snapshot file'), { target: { files: [snapshot('imessage')] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview desktop snapshot' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview desktop snapshot' }))
  await screen.findByText('1 source messages; 1 match people.')
  expect(screen.getByText('Imported desktop hello')).toBeInTheDocument()
  expect(screen.getByText(/linked to person/)).toBeInTheDocument()
  expect(screen.getByText('Snapshot coverage never certifies a complete conversation')).toBeInTheDocument()
  const before = await originalFetch(origin + base + '/desktop/imports')
  expect((await before.json()).imports).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Commit desktop import' }))
  await screen.findByRole('status')
  expect(screen.getByRole('status').textContent).toContain('Imported: 1 messages; 1 relationship observations')
  expect(screen.getByRole('status').textContent).toContain('Coverage remains unknown')
  fireEvent.click(screen.getByRole('button', { name: 'Commit desktop import' }))
  await screen.findByText(/Already imported: 1 messages/)
  const after = await originalFetch(origin + base + '/desktop/imports')
  expect((await after.json()).imports).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Read desktop history' }))
  await screen.findByText('Stored: message-1')
  expect(screen.getAllByText('Imported desktop hello')).toHaveLength(2)
  cleanup()
})

it('imports an actual Signal Desktop schema snapshot while keeping source account isolation', async () => {
  render(<DesktopPanel />)
  fireEvent.change(screen.getByLabelText('Desktop source'), { target: { value: 'signal' } })
  fireEvent.change(screen.getByLabelText('Desktop source account'), { target: { value: 'signal-ui' } })
  fireEvent.change(screen.getByLabelText('SQLite snapshot file'), { target: { files: [snapshot('signal')] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview desktop snapshot' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview desktop snapshot' }))
  await screen.findByText('1 source messages; 1 match people.')
  fireEvent.click(screen.getByRole('button', { name: 'Commit desktop import' }))
  await screen.findByRole('status')
  const response = await originalFetch(origin + base + '/desktop/history?source=signal&source_account_id=signal-ui')
  expect((await response.json()).messages[0].external_id).toBe('message-1')
  fireEvent.change(screen.getByLabelText('Desktop source account'), { target: { value: 'other-source' } })
  expect(screen.queryByRole('button', { name: 'Commit desktop import' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Read desktop history' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Read desktop history' })).not.toBeDisabled())
  expect(screen.queryByText('Stored: message-1')).not.toBeInTheDocument()
  cleanup()
})

it('rejects encrypted data through the real HTTP reader without offering a commit', async () => {
  render(<DesktopPanel />)
  fireEvent.change(screen.getByLabelText('Desktop source'), { target: { value: 'signal' } })
  fireEvent.change(screen.getByLabelText('Desktop source account'), { target: { value: 'encrypted-source' } })
  fireEvent.change(screen.getByLabelText('SQLite snapshot file'), { target: { files: [new File(['encrypted SQLCipher bytes'], 'encrypted.sqlite')] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview desktop snapshot' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview desktop snapshot' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('plain SQLite snapshot is required')
  expect(screen.queryByRole('button', { name: 'Commit desktop import' })).not.toBeInTheDocument()
  const response = await originalFetch(origin + base + '/desktop/history?source=signal&source_account_id=encrypted-source')
  expect((await response.json()).messages).toHaveLength(0)
  cleanup()
})
