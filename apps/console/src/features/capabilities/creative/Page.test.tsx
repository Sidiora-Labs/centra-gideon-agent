import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Page, { type Ingredient } from './Page'

let server: ChildProcess
let apiRoot: string
let home: string
const repository = resolve(process.cwd(), '../..')

beforeAll(async () => {
  home = mkdtempSync(`${tmpdir()}/gideon-creative-ui-`)
  server = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python',
    [resolve(repository, 'checks/runtime/capabilities/creative/http_server.py'), home],
    { env: { ...process.env, PYTHONPATH: resolve(repository, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
  apiRoot = await new Promise<string>((done, reject) => {
    let output = ''
    let errors = ''
    server.stderr?.on('data', chunk => { errors += chunk.toString() })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`Real server exited ${code}: ${errors}`)))
    server.stdout?.on('data', chunk => {
      output += chunk.toString()
      const port = output.split('\n').find(line => /^\d+$/.test(line.trim()))
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/ingredients`)
    })
  })
})

afterEach(() => { cleanup(); location.hash = '' })
afterAll(async () => {
  if (server && server.exitCode === null) {
    const ended = new Promise<void>(done => server.once('exit', () => done()))
    server.kill('SIGTERM')
    await ended
  }
  if (home) rmSync(home, { recursive: true, force: true })
})

async function create(title: string, more = {}) {
  const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: crypto.randomUUID(), type: 'concept', title, ...more }) })
  expect(response.status).toBe(201)
  return response.json() as Promise<Ingredient>
}
function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}
async function current(id: string) {
  const response = await fetch(`${apiRoot}/${id}`)
  expect(response.status).toBe(200)
  return response.json() as Promise<Ingredient>
}

describe('Creative catalog through the actual HTTP store', () => {
  it('creates, edits and restores with persistent revision history', async () => {
    render(<Page apiRoot={apiRoot} />)
    await screen.findByText('No ingredients found.')
    change('Title', 'UI protagonist')
    change('Body', 'Original story')
    change('Tags (comma separated)', 'Quest, hero')
    change('Source references', 'artifact:missing-source')
    fireEvent.click(screen.getByRole('button', { name: 'Save ingredient' }))
    await screen.findByText('Edit ingredient · revision 1')
    expect(await screen.findByText(/Source missing/)).toHaveTextContent('missing-source')
    const id = new URLSearchParams(location.hash.split('?')[1]).get('ingredient')!
    expect(id).toBeTruthy()
    const first = await current(id)
    expect(first.title).toBe('UI protagonist')
    expect(first.tags).toEqual(['quest', 'hero'])
    expect(first.body).toBe('Original story')
    expect(first.source_refs).toEqual([{ kind: 'artifact', id: 'missing-source' }])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save ingredient' })).not.toBeDisabled())
    change('Body', 'Revised story')
    fireEvent.click(screen.getByRole('button', { name: 'Save ingredient' }))
    await screen.findByText('Edit ingredient · revision 2')
    expect((await current(id)).body).toBe('Revised story')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restore revision 1' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Restore revision 1' }))
    await screen.findByText('Edit ingredient · revision 3')
    expect(screen.getByLabelText('Body')).toHaveValue('Original story')
    const response = await fetch(`${apiRoot}/${id}/revisions`)
    const versions = (await response.json()).items as Ingredient[]
    expect(versions.map(v => v.revision)).toEqual([3, 2, 1])
    expect(versions[1].body).toBe('Revised story')
    expect(versions[0].body).toBe('Original story')
    cleanup()
    render(<Page apiRoot={apiRoot} />)
    await screen.findByText('Edit ingredient · revision 3')
    expect(screen.getByLabelText('Title')).toHaveValue('UI protagonist')
    expect(screen.getByLabelText('Tags (comma separated)')).toHaveValue('quest, hero')
  })

  it('relates records and preserves failed edits for correction', async () => {
    const target = await create('Relation destination')
    render(<Page apiRoot={apiRoot} />)
    change('Title', 'Linked work')
    change('Relations', `related:${target.id}`)
    fireEvent.click(screen.getByRole('button', { name: 'Save ingredient' }))
    await screen.findByText('Edit ingredient · revision 1')
    const id = new URLSearchParams(location.hash.split('?')[1]).get('ingredient')!
    expect((await current(id)).relations).toEqual([{ kind: 'related', target_id: target.id }])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save ingredient' })).not.toBeDisabled())
    change('Relations', 'related:unknown-ingredient')
    fireEvent.click(screen.getByRole('button', { name: 'Save ingredient' }))
    await screen.findByRole('alert')
    expect(screen.getByLabelText('Relations')).toHaveValue('related:unknown-ingredient')
    expect((await current(id)).revision).toBe(1)
    change('Relations', `related:${target.id}`)
    fireEvent.click(screen.getByRole('button', { name: 'Save ingredient' }))
    await screen.findByText('Edit ingredient · revision 2')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports optimistic conflicts without overwriting another writer', async () => {
    const item = await create('Concurrent character')
    location.hash = `/capabilities/creative?ingredient=${item.id}`
    render(<Page apiRoot={apiRoot} />)
    await screen.findByText('Edit ingredient · revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save ingredient' })).not.toBeDisabled())
    const response = await fetch(`${apiRoot}/${item.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: 1, body: 'Another writer' }) })
    expect(response.status).toBe(200)
    change('Body', 'Local unsaved prose')
    fireEvent.click(screen.getByRole('button', { name: 'Save ingredient' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('reload')
    expect(screen.getByLabelText('Body')).toHaveValue('Local unsaved prose')
    expect((await current(item.id)).body).toBe('Another writer')
    fireEvent.click(screen.getByRole('button', { name: 'Reload catalog' }))
    await screen.findByText('Edit ingredient · revision 2')
    expect(screen.getByLabelText('Body')).toHaveValue('Another writer')
  })

  it('filters title, type and tag and paginates actual records', async () => {
    await Promise.all(Array.from({ length: 27 }, (_, n) => create(`Pagination item ${n}`, { tags: ['paging'], type: n === 0 ? 'event' : 'concept' })))
    render(<Page apiRoot={apiRoot} />)
    change('Filter tag', 'paging')
    await screen.findByText('27 ingredients')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous' })).not.toBeDisabled())
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled())
    change('Filter type', 'event')
    await screen.findByText('1 ingredients')
    expect(screen.getByRole('button', { name: 'Pagination item 0 · event' })).toBeInTheDocument()
    change('Search', 'Does not exist')
    await screen.findByText('No ingredients found.')
    expect(screen.getByText('0 ingredients')).toBeInTheDocument()
  })

  it('clears edit target on external navigation and exposes missing records', async () => {
    const item = await create('Navigation subject')
    location.hash = `/capabilities/creative?ingredient=${item.id}`
    render(<Page apiRoot={apiRoot} />)
    await screen.findByText('Edit ingredient · revision 1')
    location.hash = '/capabilities/creative?ingredient=missing'
    fireEvent(window, new Event('hashchange'))
    await screen.findByRole('alert')
    expect(screen.getByLabelText('Title')).toHaveValue('')
    expect(screen.queryByText('Edit ingredient · revision 1')).not.toBeInTheDocument()
    location.hash = '/capabilities/creative'
    fireEvent(window, new Event('hashchange'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save ingredient' })).not.toBeDisabled())
    expect(screen.getByLabelText('Body')).toHaveValue('')
    expect((await current(item.id)).revision).toBe(1)
  })

  it('requires reference syntax before sending malformed identifiers', async () => {
    render(<Page apiRoot={apiRoot} />)
    change('Title', 'Unsent record')
    change('Source references', 'not a reference')
    fireEvent.click(screen.getByRole('button', { name: 'Save ingredient' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('kind:identifier')
    expect(screen.getByLabelText('Title')).toHaveValue('Unsent record')
    const response = await fetch(`${apiRoot}?q=Unsent`)
    expect((await response.json()).total).toBe(0)
  })
})
