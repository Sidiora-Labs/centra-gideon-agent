import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import BundlesPage from './BundlesPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
const python = process.env.GIDEON_TEST_PYTHON || resolve(root, '.venv/bin/python')
const originalFetch = globalThis.fetch
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  const runtimeEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: directory }
  delete runtimeEnv.GIDEON_DEV_NO_AUTH
  server = spawn(python, [
    resolve(root, 'checks/runtime/capabilities/identity/bundle_extended_ui_server.py'), directory,
  ], { env: runtimeEnv })
  const ready = await new Promise<{ endpoint: string; token: string }>((resolveEndpoint, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error('HTTP server startup timed out: ' + errors)), 10000)
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('{'))
      if (line) { clearTimeout(timer); resolveEndpoint(JSON.parse(line)) }
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
  endpoint = ready.endpoint
  const origins = new Set(endpoint.split('|').map(value => new URL(value).origin))
  for (const address of endpoint.split('|')) expect((await originalFetch(address)).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const url = input instanceof Request ? input.url : String(input)
    const headers = new Headers(input instanceof Request ? input.headers : undefined)
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    if (origins.has(new URL(url).origin)) headers.set('Authorization', `Bearer ${ready.token}`)
    return originalFetch(input, { ...init, headers })
  }
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

test('expanded identity groups export and import through actual HTTP with explicit review', async () => {
  const [sourceEndpoint, destinationEndpoint] = endpoint.split('|')
  render(<BundlesPage endpoint={sourceEndpoint} />)
  await screen.findByLabelText(/human_twin/)
  fireEvent.click(screen.getByLabelText(/persona \(/))
  fireEvent.click(screen.getByLabelText(/human_twin/))
  fireEvent.click(screen.getByLabelText(/autobiography/))
  fireEvent.change(screen.getByLabelText('Bundle passphrase'), { target: { value: 'real-ui-encryption-passphrase' } })
  fireEvent.click(screen.getByRole('button', { name: 'Encrypt selected records' }))
  const link = await screen.findByRole('link', { name: 'Download encrypted bundle' })
  const serialized = decodeURIComponent(link.getAttribute('href')!.split(',')[1])
  const envelope = JSON.parse(serialized)
  expect(envelope.format).toBe('gideon.identity.bundle.v2')
  expect(serialized).not.toContain('Source private story')
  expect(screen.getByLabelText('Bundle passphrase')).toHaveValue('')
  cleanup()
  render(<BundlesPage endpoint={destinationEndpoint} />)
  await screen.findByLabelText(/human_twin/)
  fireEvent.click(screen.getByLabelText(/persona \(/))
  fireEvent.click(screen.getByLabelText(/human_twin/))
  fireEvent.click(screen.getByLabelText(/autobiography/))
  fireEvent.change(screen.getByLabelText('Import encrypted bundle file'), { target: { files: [new File([serialized], 'identity.json', { type: 'application/json' })] } })
  await screen.findByText('Encrypted bundle loaded.')
  fireEvent.change(screen.getByLabelText('Bundle passphrase'), { target: { value: 'real-ui-encryption-passphrase' } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview selected import' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview selected import' }))
  const preview = await screen.findByRole('region', { name: 'Bundle preview' })
  expect(preview).toHaveTextContent('Human identity snapshot (disabled; privacy flags preserved)')
  expect(preview).toHaveTextContent('Autobiography chronology')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Apply reviewed import' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Apply reviewed import' }))
  const receipt = await screen.findByRole('region', { name: 'Import receipt' })
  expect(receipt).toHaveTextContent('Import status: applied')
  expect(receipt).toHaveTextContent('human_twin: 1 added')
  expect(receipt).toHaveTextContent('autobiography: 1 added')
  expect(screen.getByLabelText('Bundle passphrase')).toHaveValue('')
  const persisted = await (await fetch(destinationEndpoint)).json()
  expect(persisted.groups.find((row: { id: string }) => row.id === 'human_twin').count).toBe(1)
  expect(persisted.groups.find((row: { id: string }) => row.id === 'autobiography').count).toBe(1)
  cleanup()
  render(<BundlesPage endpoint={destinationEndpoint} />)
  expect(await screen.findByLabelText(/human_twin \(1 records\)/)).toBeVisible()
  expect(screen.getByLabelText(/autobiography \(1 records\)/)).toBeVisible()
})
