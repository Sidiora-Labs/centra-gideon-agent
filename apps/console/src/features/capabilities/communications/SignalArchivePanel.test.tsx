import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { SignalArchivePanel } from './SignalArchivePanel'

const key = '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff'
let server: ChildProcess
let origin: string
let home: string
let encrypted: Buffer
const originalFetch = globalThis.fetch
const base = '/api/capabilities/communications/signal-archive'

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-signal-ui-'))
  const root = resolve(process.cwd(), '../..')
  encrypted = readFileSync(resolve(root, 'checks/runtime/capabilities/communications/fixtures/signal_sqlcipher4.sqlite'))
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/communications/ui_server.py'], {
    cwd: root,
    env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') },
    stdio: ['ignore', 'pipe', 'pipe'],
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

function fill(keyValue = key) {
  const bytes = new Uint8Array(encrypted.byteLength)
  bytes.set(encrypted)
  fireEvent.change(screen.getByLabelText('Signal account label'), { target: { value: 'owned-signal' } })
  fireEvent.change(screen.getByLabelText('Encrypted Signal SQLite file'), { target: { files: [new File([bytes.buffer], 'signal.sqlite')] } })
  fireEvent.change(screen.getByLabelText('Transient SQLCipher key'), { target: { value: keyValue } })
}

it('previews and commits a genuinely encrypted Signal archive through the HTTP boundary', async () => {
  render(<SignalArchivePanel />)
  expect(screen.getByText(/never stores the key or decrypted database/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Preview encrypted archive' })).toBeDisabled()
  fill()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview encrypted archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview encrypted archive' }))
  await screen.findByText('1 authenticated Signal messages; 0 match existing people. Coverage: supplied_encrypted_archive.')
  expect(screen.getByText(/conversation-1:message-1/)).toHaveTextContent('inbound')
  expect(screen.getByText('Encrypted hello')).toBeInTheDocument()
  expect(screen.getByText('1 attachment reference(s)')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Commit Signal import' }))
  await screen.findByText('Imported 1 Signal messages; 0 linked relationship observations.')
  expect(screen.getByLabelText('Transient SQLCipher key')).toHaveValue('')
  const imports = await originalFetch(origin + base + '/imports').then(response => response.json())
  expect(imports.imports).toHaveLength(1)
  expect(JSON.stringify(imports)).not.toContain(key)
  cleanup()
})

it('reloads normalized history without asking for or recovering the transient key', async () => {
  render(<SignalArchivePanel />)
  fireEvent.change(screen.getByLabelText('Signal account label'), { target: { value: 'owned-signal' } })
  fireEvent.click(screen.getByRole('button', { name: 'Read Signal history' }))
  await screen.findByText('Stored: conversation-1:message-1')
  expect(screen.getByText('Encrypted hello')).toBeInTheDocument()
  expect(screen.getByLabelText('Transient SQLCipher key')).toHaveValue('')
  cleanup()
})

it('surfaces page authentication failure and retains inputs for key correction', async () => {
  render(<SignalArchivePanel />)
  fill('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview encrypted archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview encrypted archive' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert')).toHaveTextContent('authentication failed')
  expect(screen.getByLabelText('Signal account label')).toHaveValue('owned-signal')
  expect(screen.getByLabelText('Transient SQLCipher key')).toHaveValue('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  expect(screen.queryByRole('button', { name: 'Commit Signal import' })).not.toBeInTheDocument()
  cleanup()
})
