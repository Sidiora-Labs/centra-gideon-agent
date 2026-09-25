import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Works from './Works'

let server: ChildProcess
let apiRoot: string
let home: string
const repository = resolve(process.cwd(), '../..')

beforeAll(async () => {
  home = mkdtempSync(`${tmpdir()}/gideon-moodboard-ui-`)
  server = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python',
    [resolve(repository, 'checks/runtime/capabilities/creative/work_server.py'), home],
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
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/works`)
    })
  })
})
afterEach(() => { cleanup(); location.hash = '' })
afterAll(async () => {
  if (server && server.exitCode === null) {
    const ended = new Promise<void>(done => server.once('exit', () => done()))
    server.kill('SIGTERM'); await ended
  }
  if (home) rmSync(home, { recursive: true, force: true })
})

function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}
async function current() {
  const id = new URLSearchParams(location.hash.split('?')[1]).get('work')!
  const response = await fetch(`${apiRoot}/${id}`)
  expect(response.status).toBe(200)
  return response.json()
}
async function ready() {
  await waitFor(() => expect(screen.queryByText('Loading writing works…')).not.toBeInTheDocument())
}

describe('Writing works and canonical manuscript journeys', () => {
  it('pins author and canon, saves actual drafts and restores active manuscript history', async () => {
    render(<Works apiRoot={apiRoot} />)
    await screen.findByText('No writing works found.')
    change('Work title', 'Station exercise')
    change('Writing type', 'exercise')
    change('Writing prompt', 'Describe a train station.')
    const author = await screen.findByRole('option', { name: 'Pinned writer · revision 1' }) as HTMLOptionElement
    const universe = await screen.findByRole('option', { name: 'Pinned world · revision 1' }) as HTMLOptionElement
    change('Pin author', author.value)
    change('Pin universe', universe.value)
    fireEvent.click(screen.getByRole('button', { name: 'Save work details' }))
    await screen.findByText('Work revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save work details' })).toBeEnabled())
    const first = await current()
    expect(first.kind).toBe('exercise')
    expect(first.author_ref).toEqual({ id: author.value, revision: 1 })
    expect(first.universe_ref).toEqual({ id: universe.value, revision: 1 })
    fireEvent.click(screen.getByRole('button', { name: 'Read pinned context' }))
    const context = await screen.findByLabelText('Pinned writing context') as HTMLTextAreaElement
    const parsed = JSON.parse(context.value)
    expect(parsed.author.voice.tone).toBe('Quiet')
    expect(parsed.universe.canon[0].title).toBe('Night')
    change('Manuscript', '  The last train arrived.\n\n')
    change('Draft note', 'First attempt')
    fireEvent.click(screen.getByRole('button', { name: 'Save new draft' }))
    await screen.findByText('Work revision 2')
    const second = await current()
    expect(second.text).toBe('  The last train arrived.\n\n')
    expect(second.active_draft.note).toBe('First attempt')
    expect(second.active_draft.artifact_version).toBe(1)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save new draft' })).toBeEnabled())
    change('Manuscript', 'A second ending.')
    change('Draft note', 'Second attempt')
    fireEvent.click(screen.getByRole('button', { name: 'Save new draft' }))
    await screen.findByText('Work revision 3')
    expect((await current()).text).toBe('A second ending.')
    const drafts = await (await fetch(`${apiRoot}/${first.id}/drafts`)).json()
    expect(drafts.items).toHaveLength(2)
    expect(drafts.items[0].artifact_id).not.toBe(drafts.items[1].artifact_id)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restore work revision 2' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Restore work revision 2' }))
    await screen.findByText('Work revision 4')
    expect(screen.getByLabelText('Manuscript')).toHaveValue('  The last train arrived.\n\n')
    expect((await current()).active_draft_id).toBe(second.active_draft_id)
    fireEvent.click(screen.getByRole('button', { name: 'Read draft 2' }))
    await waitFor(() => expect(screen.getByLabelText('Manuscript')).toHaveValue('A second ending.'))
    expect((await current()).text).toBe('  The last train arrived.\n\n')
    cleanup()
    render(<Works apiRoot={apiRoot} />)
    await screen.findByText('Work revision 4')
    expect(screen.getByLabelText('Manuscript')).toHaveValue('  The last train arrived.\n\n')
    expect(screen.getByRole('button', { name: 'Read draft 2' })).toBeInTheDocument()
  })

  it('retains unsaved prose while saving metadata and rejects stale draft writes', async () => {
    render(<Works apiRoot={apiRoot} />)
    await ready()
    change('Work title', 'Continuity work')
    fireEvent.click(screen.getByRole('button', { name: 'Save work details' }))
    await screen.findByText('Work revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save work details' })).toBeEnabled())
    change('Manuscript', 'Unsaved prose stays here.')
    change('Draft note', 'Unsaved note')
    change('Writing prompt', 'Updated prompt')
    fireEvent.click(screen.getByRole('button', { name: 'Save work details' }))
    await screen.findByText('Work revision 2')
    expect(screen.getByLabelText('Manuscript')).toHaveValue('Unsaved prose stays here.')
    expect(screen.getByLabelText('Draft note')).toHaveValue('Unsaved note')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save new draft' })).toBeEnabled())
    const work = await current()
    const changed = await fetch(`${apiRoot}/${work.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 2, title: 'Concurrent metadata' }) })
    expect(changed.status).toBe(200)
    fireEvent.click(screen.getByRole('button', { name: 'Save new draft' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Work changed')
    expect((await current()).active_draft_id).toBeNull()
    expect(screen.getByLabelText('Manuscript')).toHaveValue('Unsaved prose stays here.')
    const drafts = await (await fetch(`${apiRoot}/${work.id}/drafts`)).json()
    expect(drafts.items).toEqual([])
  })

  it('clears missing work selection and searches/paginates the collection', async () => {
    for (let index = 0; index < 26; index++) {
      const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: `Paged work ${index}`, request_id: crypto.randomUUID() }) })
      expect(response.status).toBe(201)
    }
    render(<Works apiRoot={apiRoot} />)
    await ready()
    change('Search writing works', 'Paged work')
    await screen.findByText('26 writing works')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled())
    await ready()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    change('Search writing works', 'No such work')
    await screen.findByText('No writing works found.')
    expect(screen.getByText('0 writing works')).toBeInTheDocument()
    location.hash = '#/capabilities/creative?view=works&work=missing'
    fireEvent(window, new HashChangeEvent('hashchange'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Work not found')
    expect(screen.getByRole('button', { name: 'Save work details' })).toBeDisabled()
    expect(screen.queryByLabelText('Manuscript')).not.toBeInTheDocument()
    location.hash = '#/capabilities/creative?view=works'
    fireEvent(window, new HashChangeEvent('hashchange'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save work details' })).toBeEnabled())
    change('Search writing context', 'Pinned writer')
    await screen.findByRole('option', { name: 'Pinned writer · revision 1' })
    change('Search writing context', 'No such context')
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Pinned writer · revision 1' })).not.toBeInTheDocument())
  })
})
