import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import BundlesPage from './BundlesPage'
import userEvent from '@testing-library/user-event'
import { execFileSync } from 'node:child_process'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/bundles_ui_server.py'), directory,
  ], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') } })
  endpoint = await new Promise<string>((resolveEndpoint, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error('HTTP server startup timed out: ' + errors)), 10000)
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('http://'))
      if (line) { clearTimeout(timer); resolveEndpoint(line.trim()) }
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

test('upload authenticated bundle, review selection, import and download encrypted notes through actual HTTP', async () => {
  const sourceHome = resolve(directory, 'source')
  const encoded = execFileSync('/tmp/gideon-runtime-venv/bin/python', ['-c', [
    'import json,sys',
    'from pathlib import Path',
    'from gideon.workspace.capabilities.identity.continuity import ContinuityStore',
    'from gideon.workspace.capabilities.identity.bundles import BundleService',
    'home=Path(sys.argv[1])',
    'ContinuityStore(home).append_anchor(slot="persona",text="Preserve careful reasoning.")',
    'print(json.dumps(BundleService(home).export_bundle(groups=["persona"],passphrase="a careful human passphrase")))',
  ].join('\n'), sourceHome], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, encoding: 'utf8' }).trim()
  render(<BundlesPage endpoint={endpoint} />)
  await screen.findByRole('checkbox', { name: /persona/ })
  expect(screen.getByRole('button', { name: 'Preview selected import' })).toBeDisabled()
  await userEvent.upload(screen.getByLabelText('Import encrypted bundle file'), new File([encoded], 'continuity.json', { type: 'application/json' }))
  await screen.findByText('Encrypted bundle loaded.')
  await waitFor(() => expect(screen.getByLabelText('Bundle passphrase')).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Bundle passphrase'), { target: { value: 'wrong passphrase value' } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview selected import' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview selected import' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('authentication')
  fireEvent.change(screen.getByLabelText('Bundle passphrase'), { target: { value: 'a careful human passphrase' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview selected import' }))
  const preview = await screen.findByRole('region', { name: 'Bundle preview' })
  expect(preview).toHaveTextContent('1 new · 0 duplicates · 0 human-removed')
  expect(preview).toHaveTextContent('Preserve careful reasoning.')
  const before = await (await fetch(endpoint)).json()
  expect(before.groups[0].count).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: 'Apply reviewed import' }))
  const receipt = await screen.findByRole('region', { name: 'Import receipt' })
  expect(receipt).toHaveTextContent('Import status: applied')
  expect(receipt).toHaveTextContent('persona: 1 added')
  expect(screen.getByLabelText('Bundle passphrase')).toHaveValue('')
  expect((await (await fetch(endpoint)).json()).groups[0].count).toBe(1)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Encrypt selected notes' })).toBeDisabled())
  fireEvent.change(screen.getByLabelText('Bundle passphrase'), { target: { value: 'another human passphrase' } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Encrypt selected notes' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Encrypt selected notes' }))
  const download = await screen.findByRole('link', { name: 'Download encrypted bundle' })
  expect(download).toHaveAttribute('download', 'continuity.gideon.json')
  const envelope = decodeURIComponent((download.getAttribute('href') || '').split(',')[1])
  expect(JSON.parse(envelope).format).toBe('gideon.identity.bundle.v1')
  expect(envelope).not.toContain('Preserve careful reasoning.')
  expect(envelope).not.toContain('another human passphrase')
  expect(screen.getByLabelText('Bundle passphrase')).toHaveValue('')
  cleanup()
  render(<BundlesPage endpoint={endpoint} />)
  expect(await screen.findByRole('checkbox', { name: /persona \(1 live notes\)/ })).toBeChecked()
  expect(screen.queryByRole('region', { name: 'Bundle preview' })).not.toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'Import receipt' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Bundle passphrase')).toHaveValue('')
})
