import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import LinksPage from './LinksPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'links-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/links_ui_server.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'Link application startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Link application exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => {
      buffer += String(chunk)
      for (const line of buffer.split('\n')) {
        if (!line.startsWith('{"port":')) continue
        try { const { port } = JSON.parse(line); clearTimeout(timeout); done(`http://127.0.0.1:${port}`) } catch { /* wait */ }
      }
    })
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
}, 20000)
afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null) { const stopped = new Promise<void>(done => child.once('exit', () => done())); child.kill('SIGTERM'); await stopped }
  rmSync(home, { recursive: true, force: true })
})

async function createBucket(name = 'Reading') {
  fireEvent.change(screen.getByLabelText('Bucket name'), { target: { value: name } })
  fireEvent.click(screen.getByRole('button', { name: 'Create bucket' }))
  return screen.findByRole('region', { name: 'Selected bucket' })
}

async function addLink(title: string, url: string) {
  fireEvent.change(screen.getByLabelText('Link title'), { target: { value: title } })
  fireEvent.change(screen.getByLabelText('Link URL'), { target: { value: url } })
  fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Selected bucket' })).toHaveTextContent(title))
}

it('creates a real canonical bucket, adds links and persists their explicit order', async () => {
  render(<LinksPage />)
  await screen.findByRole('navigation', { name: 'Buckets' })
  const selected = await createBucket()
  expect(selected).toHaveTextContent('Reading')
  await addLink('First source', 'https://example.com/first')
  await addLink('Second source', 'https://example.com/second')
  const list = within(screen.getByRole('region', { name: 'Selected bucket' })).getByRole('list')
  expect(list.textContent?.indexOf('First source')).toBeLessThan(list.textContent?.indexOf('Second source') || 0)
  fireEvent.click(screen.getByRole('button', { name: 'Move Second source up' }))
  await waitFor(() => {
    const current = within(screen.getByRole('region', { name: 'Selected bucket' })).getByRole('list').textContent || ''
    expect(current.indexOf('Second source')).toBeLessThan(current.indexOf('First source'))
  })
  const saved = await fetch('/api/capabilities/knowledge/links/buckets').then(response => response.json())
  expect(saved.buckets[0].links.map((item: { title: string }) => item.title)).toEqual(['Second source', 'First source'])
})

it('deletes a bucket while the receipt identifies preserved canonical bookmarks', async () => {
  render(<LinksPage />)
  await screen.findByRole('navigation', { name: 'Buckets' })
  await createBucket('Temporary')
  await addLink('Preserved', 'https://example.com/preserved')
  const before = await fetch('/api/capabilities/knowledge/links/buckets').then(response => response.json())
  const id = before.buckets.find((item: { name: string }) => item.name === 'Temporary').id
  const response = await fetch(`/api/capabilities/knowledge/links/buckets/${id}/bucket`, { method: 'DELETE', headers: { 'X-Session-Key': 'dashboard:ui' } })
  const receipt = await response.json()
  expect(receipt.preserved_item_ids).toHaveLength(1)
  expect(receipt.preserved_item_ids[0]).toBe(before.buckets.find((item: { name: string }) => item.name === 'Temporary').links[0].id)
})

it('rejects an unsupported repository before showing fabricated study success', async () => {
  render(<LinksPage />)
  await screen.findByRole('navigation', { name: 'Repository studies' })
  fireEvent.change(screen.getByLabelText('Public repository URL'), { target: { value: 'https://example.com/not/a/repository' } })
  fireEvent.click(screen.getByRole('button', { name: 'Fetch and study repository' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('GitHub or GitLab')
  expect(screen.queryByRole('region', { name: 'Selected repository' })).not.toBeInTheDocument()
  const jobs = await fetch('/api/capabilities/knowledge/links/repositories').then(response => response.json())
  expect(jobs.items).toEqual([])
})

it('keeps form input after invalid link submission and prevents hidden path overrides', async () => {
  render(<LinksPage />)
  await screen.findByRole('navigation', { name: 'Buckets' })
  await createBucket('Validation')
  fireEvent.change(screen.getByLabelText('Link title'), { target: { value: 'Local file' } })
  fireEvent.change(screen.getByLabelText('Link URL'), { target: { value: 'file:///tmp/private' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('HTTP or HTTPS')
  expect(screen.getByLabelText('Link URL')).toHaveValue('file:///tmp/private')
  const response = await fetch('/api/capabilities/knowledge/links/buckets?home=/tmp')
  expect(response.status).toBe(400)
})
