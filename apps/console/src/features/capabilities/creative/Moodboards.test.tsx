import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Moodboards from './Moodboards'
import Page from './Page'

let server: ChildProcess
let apiRoot: string
let home: string
const repository = resolve(process.cwd(), '../..')

beforeAll(async () => {
  home = mkdtempSync(`${tmpdir()}/gideon-moodboard-ui-`)
  server = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python',
    [resolve(repository, 'checks/runtime/capabilities/creative/moodboard_server.py'), home],
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
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/boards`)
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
  const id = new URLSearchParams(location.hash.split('?')[1]).get('board')!
  const response = await fetch(`${apiRoot}/${id}`)
  expect(response.status).toBe(200)
  return response.json()
}
async function ready() {
  await waitFor(() => expect(screen.queryByText('Loading moodboards…')).not.toBeInTheDocument())
}

describe('Artifact-backed moodboard journeys', () => {
  it('creates grouped image references with captions, colors and catalog links', async () => {
    render(<Moodboards apiRoot={apiRoot} />)
    await screen.findByText('No moodboards yet.')
    change('Board title', 'City inspiration')
    fireEvent.click(screen.getByRole('button', { name: 'Add group' }))
    change('Group name 1', 'Architecture')
    await screen.findByRole('option', { name: 'Blue reference · version 1' })
    change('Add source to group 1', 'blue-reference')
    change('Caption 1.1', 'Evening facade')
    change('Colors 1.1', '#123456, #abcdef')
    const ingredient = screen.getByRole('option', { name: 'Linked city' }) as HTMLOptionElement
    change('Link ingredient', ingredient.value)
    fireEvent.click(screen.getByRole('button', { name: 'Save moodboard' }))
    await screen.findByText('Moodboard revision 1')
    const image = await screen.findByRole('img', { name: 'Evening facade' })
    const response = await fetch(image.getAttribute('src')!)
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe('image/png')
    const bytes = new Uint8Array(await response.arrayBuffer())
    expect([...bytes.slice(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10])
    const board = await current()
    expect(board.groups[0].title).toBe('Architecture')
    expect(board.groups[0].cards[0].caption).toBe('Evening facade')
    expect(board.groups[0].cards[0].colors).toEqual(['#123456', '#abcdef'])
    expect(board.groups[0].cards[0].artifact_version).toBe(1)
    expect(board.groups[0].cards[0].provenance.title).toBe('Blue reference')
    expect(board.ingredient_ids).toEqual([ingredient.value])
    expect(board.ingredient_status[0].missing).toBe(false)
    cleanup()
    render(<Moodboards apiRoot={apiRoot} />)
    await screen.findByText('Moodboard revision 1')
    expect(screen.getByLabelText('Caption 1.1')).toHaveValue('Evening facade')
    expect(screen.getByLabelText('Colors 1.1')).toHaveValue('#123456, #abcdef')
  })

  it('reorders cards and groups, restores history and exports the pinned revision', async () => {
    render(<Moodboards apiRoot={apiRoot} />)
    await ready()
    change('Board title', 'Ordering study')
    fireEvent.click(screen.getByRole('button', { name: 'Add group' }))
    change('Add source to group 1', 'blue-reference')
    change('Add source to group 1', 'red-reference')
    fireEvent.click(within(screen.getByRole('article', { name: 'Card 1.2' })).getByRole('button', { name: 'Move card up' }))
    expect(within(screen.getByRole('article', { name: 'Card 1.1' })).getByText('Red reference · version 1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add group' }))
    change('Group name 2', 'Foreground')
    fireEvent.click(within(screen.getByRole('region', { name: 'Group 2' })).getByRole('button', { name: 'Move group up' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save moodboard' }))
    await screen.findByText('Moodboard revision 1')
    const first = await current()
    expect(first.groups[0].title).toBe('Foreground')
    expect(first.groups[1].cards.map((c: { artifact_id: string }) => c.artifact_id)).toEqual(['red-reference', 'blue-reference'])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save moodboard' })).not.toBeDisabled())
    change('Board title', 'Ordering changed')
    change('Colors 2.1', '#123456')
    fireEvent.click(screen.getByRole('button', { name: 'Save moodboard' }))
    await screen.findByText('Moodboard revision 2')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restore board revision 1' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Restore board revision 1' }))
    await screen.findByText('Moodboard revision 3')
    expect(screen.getByLabelText('Colors 2.1')).toHaveValue('')
    expect((await current()).groups).toEqual(first.groups)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Export moodboard' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Export moodboard' }))
    const exported = await screen.findByLabelText('Exported moodboard') as HTMLTextAreaElement
    const json = JSON.parse(exported.value)
    expect(json.title).toBe('Ordering study')
    expect(json.revision).toBe(3)
    expect(json.groups).toEqual(first.groups)
    expect(json.source_status).toBeUndefined()
  })

  it('keeps invalid colors editable and refuses stale external changes', async () => {
    render(<Moodboards apiRoot={apiRoot} />)
    await ready()
    change('Board title', 'Validation study')
    fireEvent.click(screen.getByRole('button', { name: 'Add group' }))
    change('Add source to group 1', 'blue-reference')
    change('Colors 1.1', 'red')
    fireEvent.click(screen.getByRole('button', { name: 'Save moodboard' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('six-digit hex')
    expect(screen.getByLabelText('Colors 1.1')).toHaveValue('red')
    change('Colors 1.1', '#123456')
    fireEvent.click(screen.getByRole('button', { name: 'Save moodboard' }))
    await screen.findByText('Moodboard revision 1')
    const first = await current()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save moodboard' })).not.toBeDisabled())
    const response = await fetch(`${apiRoot}/${first.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 1, title: 'Remote title' }) })
    expect(response.status).toBe(200)
    change('Board title', 'Unsaved title')
    fireEvent.click(screen.getByRole('button', { name: 'Save moodboard' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('reload')
    expect(screen.getByLabelText('Board title')).toHaveValue('Unsaved title')
    expect((await current()).title).toBe('Remote title')
  })

  it('supports board pagination and narrows canonical source choices', async () => {
    await Promise.all(Array.from({ length: 26 }, (_, n) => fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), title: `Page board ${n}`, groups: [] }) })))
    render(<Moodboards apiRoot={apiRoot} />)
    change('Search boards', 'Page board')
    await screen.findByText('26 moodboards')
    fireEvent.click(screen.getByRole('button', { name: 'Next boards' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous boards' })).not.toBeDisabled())
    expect(screen.getByRole('button', { name: 'Next boards' })).toBeDisabled()
    change('Search boards', 'nothing matches')
    await screen.findByText('No moodboards yet.')
    expect(screen.getByText('0 moodboards')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add group' }))
    change('Find source artifacts', 'Red')
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Blue reference · version 1' })).not.toBeInTheDocument())
    expect(screen.getByRole('option', { name: 'Red reference · version 1' })).toBeInTheDocument()
  })

  it('exposes the board workspace through the creative page route', async () => {
    location.hash = '/capabilities/creative?view=boards'
    render(<Page apiRoot={apiRoot.replace(/boards$/, 'ingredients')} />)
    expect(screen.getByRole('heading', { name: 'Moodboards' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Moodboards' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('navigation', { name: 'Creative workspace' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Ingredients' }))
    await screen.findByRole('heading', { name: 'Creative ingredients' })
    expect(screen.getByRole('button', { name: 'Moodboards' })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: 'Moodboards' }))
    expect(location.hash).toBe('#/capabilities/creative?view=boards')
  })
})
