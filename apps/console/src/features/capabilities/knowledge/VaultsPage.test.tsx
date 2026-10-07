import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
  child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [resolve(root, 'checks/runtime/capabilities/knowledge/vaults_ui_server.py')], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  let token = ''
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''; const timeout = setTimeout(() => fail(new Error(errors || 'Vault app startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Vault app exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => { buffer += String(chunk); for (const line of buffer.split('\n')) { if (!line.startsWith('{"port":')) continue; try { const ready = JSON.parse(line); if (typeof ready.token !== 'string' || !ready.token) throw new Error('Missing native owner token'); token = ready.token; clearTimeout(timeout); done(`http://127.0.0.1:${ready.port}`) } catch { /* wait */ } } })
  })
  const refused = await originalFetch(origin + '/api/capabilities/knowledge/vaults')
  expect(refused.status).toBe(403)
  expect(await refused.json()).toMatchObject({ error: 'Token required' })
  globalThis.fetch = (input, init) => {
    const target = typeof input === 'string' && input.startsWith('/') ? origin + input : input
    const url = target instanceof Request ? target.url : String(target)
    const headers = new Headers(target instanceof Request ? target.headers : undefined)
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${token}`)
    return originalFetch(target, { ...init, headers })
  }
}, 20000)
afterEach(cleanup)
afterAll(async () => { globalThis.fetch = originalFetch; if (child?.exitCode === null) { const stopped = new Promise<void>(done => child.once('exit', () => done())); child.kill('SIGTERM'); await stopped } rmSync(home, { recursive: true, force: true }) })

async function registerAndScan() {
  fireEvent.change(screen.getByLabelText('Vault name'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('Vault path'), { target: { value: vault } })
  fireEvent.click(screen.getByRole('button', { name: 'Register vault' }))
  expect(await screen.findByText('Vault registered', { selector: 'p[role="status"]' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Scan vault' }))
  expect(await screen.findByText('2 notes indexed · 0 missing references archived', { selector: 'p[role="status"]' })).toBeVisible()
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
  expect(await screen.findByText('Created.md saved atomically', { selector: 'p[role="status"]' })).toBeVisible()
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
