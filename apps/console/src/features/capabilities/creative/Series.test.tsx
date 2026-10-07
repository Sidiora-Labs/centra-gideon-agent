import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Series from './Series'

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
      done(`${origin}/api/capabilities/creative/series`)
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
  const id = new URLSearchParams(location.hash.split('?')[1]).get('series')!
  const response = await fetch(`${apiRoot}/${id}`)
  expect(response.status).toBe(200)
  return response.json()
}
async function ready() {
  await waitFor(() => expect(screen.queryByText('Loading series…')).not.toBeInTheDocument())
}

describe('Ordered series and staged chapter drafting', () => {
  it('creates volume chapters and arc links, drafts and reviews chapters sequentially', async () => {
    render(<Series apiRoot={apiRoot} />)
    await screen.findByText('No series found.')
    await changeReady('Series title', 'Night journeys')
    await changeReady('Series synopsis', 'A traveler finds home.')
    await clickReady('Add volume')
    await changeReady('Volume title 1', 'Departure')
    await clickReady('Add chapter to volume 1')
    await changeReady('Chapter title 1.1', 'Station')
    await changeReady('Chapter prompt 1.1', 'Begin at a station.')
    await clickReady('Add chapter to volume 1')
    await changeReady('Chapter title 1.2', 'Walk')
    await changeReady('Chapter prompt 1.2', 'Follow the tracks.')
    await clickReady('Add arc')
    await changeReady('Arc title 1', 'Homecoming')
    await changeReady('Arc summary 1', 'Learn where home is.')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Arc 1 includes Station' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Arc 1 includes Walk' }))
    const author = await screen.findByRole('option', { name: 'Pinned writer · revision 1' }) as HTMLOptionElement
    await changeReady('Series author', author.value)
    await clickReady('Save series plan')
    await screen.findByText('Series revision 1')
    const series = await current()
    expect(series.volumes[0].chapters.map((c: { title: string }) => c.title)).toEqual(['Station', 'Walk'])
    expect(series.arcs[0].chapter_ids).toHaveLength(2)
    expect(series.author_ref).toEqual({ id: author.value, revision: 1 })
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    await changeReady('Drafting chapter', series.volumes[0].chapters[1].id)
    await screen.findByText('Review preceding chapters first')
    await clickReady('Prepare chapter work')
    await screen.findByRole('link', { name: 'Open chapter writing work' })
    expectGuardedButton(screen.getByRole('button', { name: 'Save staged chapter draft' }))
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    await changeReady('Drafting chapter', series.volumes[0].chapters[0].id)
    await clickReady('Prepare chapter work')
    await screen.findByLabelText('Chapter manuscript')
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    await changeReady('Chapter manuscript', 'The last train arrived.\n')
    await clickReady('Save staged chapter draft')
    await screen.findByText('Chapter stage: drafted')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Mark chapter reviewed' })))
    await clickReady('Mark chapter reviewed')
    await screen.findByText('Chapter stage: reviewed')
    const updated = await current()
    expect(updated.chapter_status[1].ready_to_draft).toBe(true)
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    await changeReady('Drafting chapter', series.volumes[0].chapters[1].id)
    await screen.findByText('Chapter stage: planned')
    await waitFor(() => expect(screen.getByLabelText('Chapter manuscript')).toBeEnabled())
    await changeReady('Chapter manuscript', 'She followed the tracks.')
    await clickReady('Save staged chapter draft')
    await screen.findByText('Chapter stage: drafted')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Mark chapter reviewed' })))
    await clickReady('Mark chapter reviewed')
    await screen.findByText('Chapter stage: reviewed')
    const complete = await current()
    expect(complete.chapter_status.map((s: { stage: string }) => s.stage)).toEqual(['reviewed', 'reviewed'])
    const workId = complete.chapter_status[1].work_id
    const workResponse = await fetch(`${apiRoot.replace(/series$/, 'works')}/${workId}`)
    expect(workResponse.status).toBe(200)
    expect((await workResponse.json()).text).toBe('She followed the tracks.')
    cleanup()
    render(<Series apiRoot={apiRoot} />)
    await screen.findByText('Series revision 1')
    await changeReady('Drafting chapter', series.volumes[0].chapters[1].id)
    await waitFor(() => expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('She followed the tracks.'))
    expect(screen.getByText('Chapter stage: reviewed')).toBeInTheDocument()
  })

  it('preserves unsaved manuscript during review and metadata save, then invalidates downstream review', async () => {
    render(<Series apiRoot={apiRoot} />)
    await ready()
    await clickReady('Night journeys')
    await screen.findByText('Series revision 1')
    const series = await current()
    await changeReady('Drafting chapter', series.volumes[0].chapters[0].id)
    await waitFor(() => expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('The last train arrived.\n'))
    await changeReady('Chapter manuscript', 'Unsaved revision remains here.')
    await clickReady('Mark chapter reviewed')
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('Unsaved revision remains here.')
    await changeReady('Series title', 'Night journeys revised')
    await clickReady('Save series plan')
    await screen.findByText('Series revision 2')
    expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('Unsaved revision remains here.')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save staged chapter draft' })))
    await clickReady('Save staged chapter draft')
    await screen.findByText('Chapter stage: drafted')
    const changed = await current()
    expect(changed.chapter_status[1].stage).toBe('drafted')
    expect(changed.chapter_status[1].ready_to_draft).toBe(false)
    expectReadOnlyField(screen.getByLabelText('Chapter title 1.1'))
    expectReadOnlyField(screen.getByLabelText('Chapter prompt 1.1'))
  })

  it('reorders and restores plans, filters paginated series and clears missing deep links', async () => {
    for (let index = 0; index < 26; index++) {
      const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: `Paged series ${index}`, request_id: crypto.randomUUID() }) })
      expect(response.status).toBe(201)
    }
    render(<Series apiRoot={apiRoot} />)
    await ready()
    await changeReady('Search series', 'Paged series')
    await screen.findByText('26 series')
    await clickReady('Next page')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Previous page' })))
    await ready()
    expectGuardedButton(screen.getByRole('button', { name: 'Next page' }))
    await changeReady('Search series', 'Night journeys revised')
    await clickReady('Night journeys revised')
    await screen.findByText('Series revision 2')
    await clickReady('Move chapter 1.2 up')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save series plan' })))
    await clickReady('Save series plan')
    await screen.findByText('Series revision 3')
    expect(screen.getByLabelText('Chapter title 1.1')).toHaveValue('Walk')
    await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Restore series revision 2' })))
    await clickReady('Restore series revision 2')
    await screen.findByText('Series revision 4')
    expect(screen.getByLabelText('Chapter title 1.1')).toHaveValue('Station')
    location.hash = '#/capabilities/creative?view=series&series=missing'
    fireEvent(window, new HashChangeEvent('hashchange'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Series not found')
    expect(screen.getByLabelText('Series title')).toHaveValue('')
    expectGuardedButton(screen.getByRole('button', { name: 'Save series plan' }))
  })
})

function expectReadOnlyField(field: HTMLElement) {
  expect(field).toHaveAttribute('readonly')
  expect(field).not.toBeDisabled()
  const ids = field.getAttribute('aria-describedby')?.split(' ') ?? []
  expect(ids.length).toBeGreaterThan(0)
  for (const id of ids) expect(document.getElementById(id)?.textContent).toContain('A writing work already exists')
  field.focus()
  expect(document.activeElement).toBe(field)
}

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
