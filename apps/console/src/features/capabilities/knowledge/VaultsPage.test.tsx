import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import VaultsPage from './VaultsPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'vaults-console-'))
const allowed = join(home, 'allowed')
const vault = join(allowed, 'notes')
let child: ChildProcess
beforeAll(async () => {
  mkdirSync(vault, { recursive: true })
  writeFileSync(join(vault, 'Home.md'), '# Home\nActual searchable text [[Other]] #root')
  writeFileSync(join(vault, 'Other.md'), '# Other\nSecond note')
  writeFileSync(join(home, 'config.json'), JSON.stringify({ knowledge: { external_vault_roots: [allowed] } }))
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/vaults_ui_server.py')], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''; const timeout = setTimeout(() => fail(new Error(errors || 'Vault app startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Vault app exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => { buffer += String(chunk); for (const line of buffer.split('\n')) { if (!line.startsWith('{"port":')) continue; try { const { port } = JSON.parse(line); clearTimeout(timeout); done(`http://127.0.0.1:${port}`) } catch { /* wait */ } } })
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
}, 20000)
afterEach(cleanup)
afterAll(async () => { globalThis.fetch = originalFetch; if (child?.exitCode === null) { const stopped = new Promise<void>(done => child.once('exit', () => done())); child.kill('SIGTERM'); await stopped } rmSync(home, { recursive: true, force: true }) })

async function registerAndScan() {
  fireEvent.change(screen.getByLabelText('Vault name'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('Vault path'), { target: { value: vault } })
  fireEvent.click(screen.getByRole('button', { name: 'Register vault' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Vault registered')
  fireEvent.click(screen.getByRole('button', { name: 'Scan vault' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('2 notes indexed'))
}

it('registers an allowed real vault, scans notes and opens canonical references', async () => {
  render(<VaultsPage />)
  await screen.findByText(new RegExp(allowed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  await registerAndScan()
  fireEvent.click(screen.getByRole('button', { name: 'Home.md' }))
  expect(await screen.findByDisplayValue(/Actual searchable text/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open canonical reference' }).getAttribute('href')).toMatch(/^#\/knowledge\/item\//)
})

it('detects an external concurrent edit and preserves those bytes', async () => {
  render(<VaultsPage />)
  await screen.findByRole('navigation', { name: 'External vaults' })
  const button = await screen.findByRole('button', { name: /Research/ })
  fireEvent.click(button)
  fireEvent.click(screen.getByRole('button', { name: 'Scan vault' }))
  await screen.findByRole('button', { name: 'Home.md' })
  fireEvent.click(screen.getByRole('button', { name: 'Home.md' }))
  await screen.findByDisplayValue(/Actual searchable text/)
  writeFileSync(join(vault, 'Home.md'), 'External concurrent text')
  fireEvent.change(screen.getByLabelText('Markdown content'), { target: { value: 'My stale edit' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save note' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('changed')
  const registrations = await fetch('/api/capabilities/knowledge/vaults').then(response => response.json())
  const selected = registrations.items.find((item: { name: string }) => item.name === 'Research')
  const current = await fetch(`/api/capabilities/knowledge/vaults/${selected.id}/read`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Key': 'dashboard:ui' }, body: JSON.stringify({ path: 'Home.md' }) }).then(response => response.json())
  expect(current.content).toBe('External concurrent text')
})

it('creates a new contained note and returns it through live search', async () => {
  render(<VaultsPage />)
  await screen.findByRole('navigation', { name: 'External vaults' })
  fireEvent.click(await screen.findByRole('button', { name: /Research/ }))
  fireEvent.change(screen.getByLabelText('Note path'), { target: { value: 'Created.md' } })
  fireEvent.change(screen.getByLabelText('Markdown content'), { target: { value: 'Unique created phrase' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create note' }))
  expect(await screen.findByRole('status')).toHaveTextContent('saved atomically')
  fireEvent.change(screen.getByLabelText('Search notes'), { target: { value: 'unique created' } })
  fireEvent.click(screen.getByRole('button', { name: 'Search' }))
  expect(await screen.findByRole('button', { name: 'Created.md' })).toBeInTheDocument()
})

it('rejects a path outside configured roots without creating a registration', async () => {
  render(<VaultsPage />)
  await screen.findByText(new RegExp(allowed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  fireEvent.change(screen.getByLabelText('Vault name'), { target: { value: 'Outside' } })
  fireEvent.change(screen.getByLabelText('Vault path'), { target: { value: '/tmp' } })
  fireEvent.click(screen.getByRole('button', { name: 'Register vault' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('outside configured')
  const registrations = await fetch('/api/capabilities/knowledge/vaults').then(response => response.json())
  expect(registrations.items.filter((item: { name: string }) => item.name === 'Outside')).toEqual([])
})
