import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
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
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/series`)
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
    change('Series title', 'Night journeys')
    change('Series synopsis', 'A traveler finds home.')
    fireEvent.click(screen.getByRole('button', { name: 'Add volume' }))
    change('Volume title 1', 'Departure')
    fireEvent.click(screen.getByRole('button', { name: 'Add chapter to volume 1' }))
    change('Chapter title 1.1', 'Station')
    change('Chapter prompt 1.1', 'Begin at a station.')
    fireEvent.click(screen.getByRole('button', { name: 'Add chapter to volume 1' }))
    change('Chapter title 1.2', 'Walk')
    change('Chapter prompt 1.2', 'Follow the tracks.')
    fireEvent.click(screen.getByRole('button', { name: 'Add arc' }))
    change('Arc title 1', 'Homecoming')
    change('Arc summary 1', 'Learn where home is.')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Arc 1 includes Station' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Arc 1 includes Walk' }))
    const author = await screen.findByRole('option', { name: 'Pinned writer · revision 1' }) as HTMLOptionElement
    change('Series author', author.value)
    fireEvent.click(screen.getByRole('button', { name: 'Save series plan' }))
    await screen.findByText('Series revision 1')
    const series = await current()
    expect(series.volumes[0].chapters.map((c: { title: string }) => c.title)).toEqual(['Station', 'Walk'])
    expect(series.arcs[0].chapter_ids).toHaveLength(2)
    expect(series.author_ref).toEqual({ id: author.value, revision: 1 })
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    change('Drafting chapter', series.volumes[0].chapters[1].id)
    await screen.findByText('Review preceding chapters first')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare chapter work' }))
    await screen.findByRole('link', { name: 'Open chapter writing work' })
    expect(screen.getByRole('button', { name: 'Save staged chapter draft' })).toBeDisabled()
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    change('Drafting chapter', series.volumes[0].chapters[0].id)
    fireEvent.click(await screen.findByRole('button', { name: 'Prepare chapter work' }))
    await screen.findByLabelText('Chapter manuscript')
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    change('Chapter manuscript', 'The last train arrived.\n')
    fireEvent.click(screen.getByRole('button', { name: 'Save staged chapter draft' }))
    await screen.findByText('Chapter stage: drafted')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mark chapter reviewed' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Mark chapter reviewed' }))
    await screen.findByText('Chapter stage: reviewed')
    const updated = await current()
    expect(updated.chapter_status[1].ready_to_draft).toBe(true)
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    change('Drafting chapter', series.volumes[0].chapters[1].id)
    await screen.findByText('Chapter stage: planned')
    await waitFor(() => expect(screen.getByLabelText('Chapter manuscript')).toBeEnabled())
    change('Chapter manuscript', 'She followed the tracks.')
    fireEvent.click(screen.getByRole('button', { name: 'Save staged chapter draft' }))
    await screen.findByText('Chapter stage: drafted')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mark chapter reviewed' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Mark chapter reviewed' }))
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
    change('Drafting chapter', series.volumes[0].chapters[1].id)
    await waitFor(() => expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('She followed the tracks.'))
    expect(screen.getByText('Chapter stage: reviewed')).toBeInTheDocument()
  })

  it('preserves unsaved manuscript during review and metadata save, then invalidates downstream review', async () => {
    render(<Series apiRoot={apiRoot} />)
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Night journeys' }))
    await screen.findByText('Series revision 1')
    const series = await current()
    change('Drafting chapter', series.volumes[0].chapters[0].id)
    await waitFor(() => expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('The last train arrived.\n'))
    change('Chapter manuscript', 'Unsaved revision remains here.')
    fireEvent.click(screen.getByRole('button', { name: 'Mark chapter reviewed' }))
    await waitFor(() => expect(screen.getByLabelText('Drafting chapter')).toBeEnabled())
    expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('Unsaved revision remains here.')
    change('Series title', 'Night journeys revised')
    fireEvent.click(screen.getByRole('button', { name: 'Save series plan' }))
    await screen.findByText('Series revision 2')
    expect(screen.getByLabelText('Chapter manuscript')).toHaveValue('Unsaved revision remains here.')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save staged chapter draft' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Save staged chapter draft' }))
    await screen.findByText('Chapter stage: drafted')
    const changed = await current()
    expect(changed.chapter_status[1].stage).toBe('drafted')
    expect(changed.chapter_status[1].ready_to_draft).toBe(false)
    expect(screen.getByLabelText('Chapter title 1.1')).toBeDisabled()
    expect(screen.getByLabelText('Chapter prompt 1.1')).toBeDisabled()
  })

  it('reorders and restores plans, filters paginated series and clears missing deep links', async () => {
    for (let index = 0; index < 26; index++) {
      const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: `Paged series ${index}`, request_id: crypto.randomUUID() }) })
      expect(response.status).toBe(201)
    }
    render(<Series apiRoot={apiRoot} />)
    await ready()
    change('Search series', 'Paged series')
    await screen.findByText('26 series')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled())
    await ready()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    change('Search series', 'Night journeys revised')
    fireEvent.click(await screen.findByRole('button', { name: 'Night journeys revised' }))
    await screen.findByText('Series revision 2')
    fireEvent.click(screen.getByRole('button', { name: 'Move chapter 1.2 up' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save series plan' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Save series plan' }))
    await screen.findByText('Series revision 3')
    expect(screen.getByLabelText('Chapter title 1.1')).toHaveValue('Walk')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restore series revision 2' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Restore series revision 2' }))
    await screen.findByText('Series revision 4')
    expect(screen.getByLabelText('Chapter title 1.1')).toHaveValue('Station')
    location.hash = '#/capabilities/creative?view=series&series=missing'
    fireEvent(window, new HashChangeEvent('hashchange'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Series not found')
    expect(screen.getByLabelText('Series title')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Save series plan' })).toBeDisabled()
  })
})
