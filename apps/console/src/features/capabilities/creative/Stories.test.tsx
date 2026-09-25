import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Stories from './Stories'

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
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/stories`)
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
  const id = new URLSearchParams(location.hash.split('?')[1]).get('story')!
  const response = await fetch(`${apiRoot}/${id}`)
  expect(response.status).toBe(200)
  return response.json()
}
async function ready() {
  await waitFor(() => expect(screen.queryByText('Loading stories…')).not.toBeInTheDocument())
}

describe('Guided story development journeys', () => {
  it('guides readiness stages and creates a canonical writing work with pinned context', async () => {
    render(<Stories apiRoot={apiRoot} />)
    await screen.findByText('No stories found.')
    change('Story title', 'Night journey')
    const author = await screen.findByRole('option', { name: 'Pinned writer · revision 1' }) as HTMLOptionElement
    const universe = await screen.findByRole('option', { name: 'Pinned world · revision 1' }) as HTMLOptionElement
    change('Story author', author.value)
    change('Story universe', universe.value)
    fireEvent.click(screen.getByRole('button', { name: 'Save story' }))
    await screen.findByText('Story revision 1')
    expect(screen.getByText('Current stage: premise')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Advance story stage' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Advance story stage' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Complete story fields: premise')
    expect((await current()).revision).toBe(1)
    change('Premise', 'A traveler misses the last train.')
    fireEvent.click(screen.getByRole('button', { name: 'Advance story stage' }))
    await screen.findByText('Story revision 2')
    expect(screen.getByText('Current stage: development')).toBeInTheDocument()
    change('Protagonist goal', 'Get home')
    change('Conflict', 'No trains')
    change('Stakes', 'Family waiting')
    change('Ending', 'Walks home')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Advance story stage' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Advance story stage' }))
    await screen.findByText('Story revision 3')
    expect(screen.getByText('Current stage: outline')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add story beat' }))
    change('Beat title 1', 'Arrival')
    change('Beat summary 1', 'Too late for the train.')
    fireEvent.click(screen.getByRole('button', { name: 'Add story beat' }))
    change('Beat title 2', 'Walk')
    change('Beat summary 2', 'Finds another way.')
    fireEvent.click(screen.getByRole('button', { name: 'Move beat 2 up' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Advance story stage' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Advance story stage' }))
    await screen.findByText('Story revision 4')
    expect(screen.getByText('Current stage: ready')).toBeInTheDocument()
    const readyStory = await current()
    expect(readyStory.beats.map((b: { title: string }) => b.title)).toEqual(['Walk', 'Arrival'])
    expect(readyStory.source_status.map((r: { missing: boolean }) => r.missing)).toEqual([false, false])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create writing work' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Create writing work' }))
    const link = await screen.findByRole('link', { name: 'Open writing work' })
    const workId = new URLSearchParams(link.getAttribute('href')!.split('?')[1]).get('work')
    const workResponse = await fetch(`${apiRoot.replace(/stories$/, 'works')}/${workId}`)
    expect(workResponse.status).toBe(200)
    const work = await workResponse.json()
    expect(work.prompt).toContain('Goal: Get home')
    expect(work.prompt).toContain('Walk: Finds another way.')
    expect(work.author_ref).toEqual({ id: author.value, revision: 1 })
    expect(work.universe_ref).toEqual({ id: universe.value, revision: 1 })
    fireEvent.click(screen.getByRole('button', { name: 'Create writing work' }))
    await waitFor(() => expect(screen.getAllByRole('link', { name: 'Open writing work' })).toHaveLength(1))
    cleanup()
    render(<Stories apiRoot={apiRoot} />)
    await screen.findByText('Story revision 4')
    expect(screen.getByLabelText('Beat title 1')).toHaveValue('Walk')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restore story revision 2' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Restore story revision 2' }))
    await screen.findByText('Story revision 5')
    expect(screen.getByText('Current stage: development')).toBeInTheDocument()
    expect(screen.queryByLabelText('Beat title 1')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open writing work' })).toBeInTheDocument()
  })

  it('reviews a genuine authored proposal before adopting it', async () => {
    const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), title: 'Reviewed guidance' }) })
    expect(response.status).toBe(201)
    const story = await response.json()
    const proposed = await fetch(`${apiRoot}/${story.id}/suggestions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), revision: 1, mode: 'authored', patch: { premise: 'A midnight journey.', genre: 'Literary' } }) })
    expect(proposed.status).toBe(200)
    location.hash = `#/capabilities/creative?view=stories&story=${story.id}`
    render(<Stories apiRoot={apiRoot} />)
    await screen.findByText('Story revision 1')
    expect(screen.getByLabelText('Premise')).toHaveValue('')
    const suggestion = await screen.findByRole('region', { name: 'Story suggestion 1' })
    expect(suggestion).toHaveTextContent('Suggestion source: authored')
    expect(suggestion).toHaveTextContent('Premise: A midnight journey.')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Adopt suggestion 1' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Adopt suggestion 1' }))
    await screen.findByText('Story revision 2')
    expect(screen.getByLabelText('Premise')).toHaveValue('A midnight journey.')
    expect(screen.getByLabelText('Genre')).toHaveValue('Literary')
    expect(screen.getByRole('button', { name: 'Adopt suggestion 1' })).toBeDisabled()
    const history = await (await fetch(`${apiRoot}/${story.id}/revisions`)).json()
    expect(history.items[1].premise).toBe('')
    expect(history.items[0].premise).toBe('A midnight journey.')
  })

  it('handles stale metadata, missing deep links, filtering and pagination', async () => {
    for (let index = 0; index < 26; index++) {
      const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: `Paged story ${index}`, request_id: crypto.randomUUID() }) })
      expect(response.status).toBe(201)
    }
    render(<Stories apiRoot={apiRoot} />)
    await ready()
    change('Search stories', 'Paged story')
    await screen.findByText('26 stories')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled())
    await ready()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    change('Search stories', 'Unknown title')
    await screen.findByText('No stories found.')
    change('Story title', 'Concurrent story')
    fireEvent.click(screen.getByRole('button', { name: 'Save story' }))
    await screen.findByText('Story revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save story' })).toBeEnabled())
    const story = await current()
    const updated = await fetch(`${apiRoot}/${story.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 1, title: 'Other update' }) })
    expect(updated.status).toBe(200)
    change('Story title', 'Stale title')
    fireEvent.click(screen.getByRole('button', { name: 'Save story' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Story changed')
    expect((await current()).title).toBe('Other update')
    location.hash = '#/capabilities/creative?view=stories&story=missing'
    fireEvent(window, new HashChangeEvent('hashchange'))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Story not found'))
    expect(screen.getByLabelText('Story title')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Save story' })).toBeDisabled()
  })
})
