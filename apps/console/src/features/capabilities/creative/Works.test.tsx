import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
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
const nativeFetch = globalThis.fetch

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
      const line = output.split('\n').find(line => line.startsWith('{') && line.endsWith('}'))
      if (!line) return
      const ready = JSON.parse(line) as { port: number; token: string }
      const origin = `http://127.0.0.1:${ready.port}`
      globalThis.fetch = (input, init) => {
        const requestUrl = input instanceof Request ? input.url : String(input)
        const headers = new Headers(input instanceof Request ? input.headers : undefined)
        new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
        if (new URL(requestUrl, window.location.href).origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
        return nativeFetch(input, { ...init, headers })
      }
      done(`${origin}/api/capabilities/creative/works`)
    })
  })
  const refused = await nativeFetch(apiRoot)
  expect(refused.status).toBe(403)
  expect(await refused.json()).toMatchObject({ error: 'Token required' })
  expect((await fetch(apiRoot)).status).toBe(200)
})
afterEach(() => { cleanup(); location.hash = '' })
afterAll(async () => {
  globalThis.fetch = nativeFetch
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
    await changeReady('Work title', 'Station exercise')
    await changeReady('Writing type', 'exercise')
    await changeReady('Writing prompt', 'Describe a train station.')
    const author = await screen.findByRole('option', { name: 'Pinned writer · revision 1' }) as HTMLOptionElement
    const universe = await screen.findByRole('option', { name: 'Pinned world · revision 1' }) as HTMLOptionElement
    await changeReady('Pin author', author.value)
    await changeReady('Pin universe', universe.value)
    await clickReady('Save work details')
    await screen.findByText('Work revision 1')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save work details' })))
    const first = await current()
    expect(first.kind).toBe('exercise')
    expect(first.author_ref).toEqual({ id: author.value, revision: 1 })
    expect(first.universe_ref).toEqual({ id: universe.value, revision: 1 })
    await clickReady('Read pinned context')
    const context = await screen.findByLabelText('Pinned writing context') as HTMLTextAreaElement
    const parsed = JSON.parse(context.value)
    expect(parsed.author.voice.tone).toBe('Quiet')
    expect(parsed.universe.canon[0].title).toBe('Night')
    await changeReady('Manuscript', '  The last train arrived.\n\n')
    await changeReady('Draft note', 'First attempt')
    await clickReady('Save new draft')
    await screen.findByText('Work revision 2')
    const second = await current()
    expect(second.text).toBe('  The last train arrived.\n\n')
    expect(second.active_draft.note).toBe('First attempt')
    expect(second.active_draft.artifact_version).toBe(1)
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save new draft' })))
    await changeReady('Manuscript', 'A second ending.')
    await changeReady('Draft note', 'Second attempt')
    await clickReady('Save new draft')
    await screen.findByText('Work revision 3')
    expect((await current()).text).toBe('A second ending.')
    const drafts = await (await fetch(`${apiRoot}/${first.id}/drafts`)).json()
    expect(drafts.items).toHaveLength(2)
    expect(drafts.items[0].artifact_id).not.toBe(drafts.items[1].artifact_id)
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Restore work revision 2' })))
    await clickReady('Restore work revision 2')
    await screen.findByText('Work revision 4')
    expect(screen.getByLabelText('Manuscript')).toHaveValue('  The last train arrived.\n\n')
    expect((await current()).active_draft_id).toBe(second.active_draft_id)
    await clickReady('Read draft 2')
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
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save work details' })))
    change('Manuscript', 'Unsaved prose stays here.')
    change('Draft note', 'Unsaved note')
    change('Writing prompt', 'Updated prompt')
    fireEvent.click(screen.getByRole('button', { name: 'Save work details' }))
    await screen.findByText('Work revision 2')
    expect(screen.getByLabelText('Manuscript')).toHaveValue('Unsaved prose stays here.')
    expect(screen.getByLabelText('Draft note')).toHaveValue('Unsaved note')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save new draft' })))
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
    await changeReady('Search writing works', 'Paged work')
    await screen.findByText('26 writing works')
    await clickReady('Next page')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Previous page' })))
    await ready()
    expectGuardedButton(screen.getByRole('button', { name: 'Next page' }))
    await changeReady('Search writing works', 'No such work')
    await screen.findByText('No writing works found.')
    expect(screen.getByText('0 writing works')).toBeInTheDocument()
    location.hash = '#/capabilities/creative?view=works&work=missing'
    fireEvent(window, new HashChangeEvent('hashchange'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Work not found')
    expectGuardedButton(screen.getByRole('button', { name: 'Save work details' }))
    expect(screen.queryByLabelText('Manuscript')).not.toBeInTheDocument()
    location.hash = '#/capabilities/creative?view=works'
    fireEvent(window, new HashChangeEvent('hashchange'))
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save work details' })))
    await changeReady('Search writing context', 'Pinned writer')
    await screen.findByRole('option', { name: 'Pinned writer · revision 1' })
    await changeReady('Search writing context', 'No such context')
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Pinned writer · revision 1' })).not.toBeInTheDocument())
  })
})

function expectAvailableButton(button: HTMLElement) {
  expect(button).not.toBeDisabled()
  expect(button).not.toHaveAttribute('aria-disabled', 'true')
}
function expectGuardedButton(button: HTMLElement) {
  if (button.hasAttribute('disabled')) {
    expect(button).toBeDisabled()
    expect(button).not.toHaveAttribute('aria-disabled', 'true')
  } else {
    expect(button).toHaveAttribute('aria-disabled', 'true')
    const ids = button.getAttribute('aria-describedby')?.split(' ') ?? []
    expect(ids.length).toBeGreaterThan(0)
    for (const id of ids) expect(document.getElementById(id)?.textContent?.trim()).toBeTruthy()
    button.focus()
    expect(document.activeElement).toBe(button)
  }
  const requests = vi.spyOn(globalThis, 'fetch')
  try {
    fireEvent.click(button)
    expect(requests).not.toHaveBeenCalled()
  } finally { requests.mockRestore() }
}

async function changeReady(label: string, value: string) {
  await waitFor(() => {
    const field = screen.getByLabelText(label)
    expect(field).not.toBeDisabled()
    expect(field).not.toHaveAttribute('readonly')
    expect(field).not.toHaveAttribute('aria-readonly', 'true')
  })
  change(label, value)
  const field = screen.getByLabelText(label)
  expect(field).toHaveValue(field.getAttribute('type') === 'number' ? Number(value) : value)
}
async function clickReady(name: string) {
  let button: HTMLElement | undefined
  await waitFor(() => {
    button = screen.getByRole('button', { name })
    expect(button).toBeVisible()
    expect(button).toHaveAccessibleName(name)
    expectAvailableButton(button)
  })
  if (!button) throw new Error(`Button did not become available: ${name}`)
  fireEvent.click(button)
}
