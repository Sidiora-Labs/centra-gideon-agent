import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Universes from './Universes'

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
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/universes`)
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
  const id = new URLSearchParams(location.hash.split('?')[1]).get('universe')!
  const response = await fetch(`${apiRoot}/${id}`)
  expect(response.status).toBe(200)
  return response.json()
}
async function ready() {
  await waitFor(() => expect(screen.queryByText('Loading universes…')).not.toBeInTheDocument())
}

describe('Universe canon and visual identity journeys', () => {
  it('creates ordered canon and identity with ingredient and pinned moodboard', async () => {
    const boardResponse = await fetch(apiRoot.replace(/universes$/, 'boards'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'Universe board', request_id: crypto.randomUUID() }) })
    expect(boardResponse.status).toBe(201)
    const board = await boardResponse.json()
    render(<Universes apiRoot={apiRoot} />)
    await screen.findByText('No universes found.')
    change('Universe title', 'Night universe')
    fireEvent.click(screen.getByRole('button', { name: 'Add canon entry' }))
    change('Canon title 1', 'Permanent night')
    change('Canon text 1', 'The city has no sunrise.')
    fireEvent.click(screen.getByRole('button', { name: 'Add canon entry' }))
    change('Canon title 2', 'Moon')
    change('Canon text 2', 'One red moon.')
    change('Universe colors', '#aabbcc, #112233')
    expect(screen.getByTitle('#112233')).toHaveStyle({ backgroundColor: '#112233' })
    change('Visual style notes', 'Copper and glass.')
    const ingredient = await screen.findByRole('option', { name: 'Linked city' }) as HTMLOptionElement
    change('Link canon ingredient', ingredient.value)
    await screen.findByRole('option', { name: 'Universe board · revision 1' })
    change('Pin moodboard', board.id)
    fireEvent.click(screen.getByRole('button', { name: 'Save universe' }))
    await screen.findByText('Universe revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save universe' })).toBeEnabled())
    const saved = await current()
    expect(saved.canon.map((item: { title: string }) => item.title)).toEqual(['Permanent night', 'Moon'])
    expect(saved.visual_identity).toEqual({ colors: ['#aabbcc', '#112233'], style_notes: 'Copper and glass.' })
    expect(saved.ingredient_ids).toEqual([ingredient.value])
    expect(saved.board_refs).toEqual([{ id: board.id, revision: 1 }])
    expect(saved.ingredient_status[0].missing).toBe(false)
    const pinned = screen.getByRole('link', { name: 'Open pinned moodboard' })
    const pinnedResponse = await fetch(pinned.getAttribute('href')!)
    expect(pinnedResponse.status).toBe(200)
    expect((await pinnedResponse.json()).revision).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Move entry 2 up' }))
    change('Universe colors', '#abcdef')
    change('Visual style notes', 'Only glass.')
    fireEvent.click(screen.getByRole('button', { name: 'Save universe' }))
    await screen.findByText('Universe revision 2')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save universe' })).toBeEnabled())
    expect((await current()).canon[0].title).toBe('Moon')
    fireEvent.click(screen.getByRole('button', { name: 'Restore universe revision 1' }))
    await screen.findByText('Universe revision 3')
    expect(screen.getByLabelText('Canon title 1')).toHaveValue('Permanent night')
    expect(screen.getByLabelText('Universe colors')).toHaveValue('#aabbcc, #112233')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Export universe' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Export universe' }))
    const output = await screen.findByLabelText('Universe export') as HTMLTextAreaElement
    const exported = JSON.parse(output.value)
    expect(exported.revision).toBe(3)
    expect(exported.board_refs).toEqual(saved.board_refs)
    expect(exported.canon).toEqual(saved.canon)
    expect(exported.source_status).toBeUndefined()
    cleanup()
    render(<Universes apiRoot={apiRoot} />)
    await screen.findByText('Universe revision 3')
    expect(screen.getByLabelText('Visual style notes')).toHaveValue('Copper and glass.')
    expect(screen.getByLabelText('Canon title 2')).toHaveValue('Moon')
  })

  it('rejects invalid palette and stale writes, then reloads the authoritative revision', async () => {
    render(<Universes apiRoot={apiRoot} />)
    await ready()
    change('Universe title', 'Conflict universe')
    change('Universe colors', 'red')
    fireEvent.click(screen.getByRole('button', { name: 'Save universe' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Colors must be six-digit hex values')
    expect(screen.queryByText('Universe revision 1')).not.toBeInTheDocument()
    change('Universe colors', '#223344')
    fireEvent.click(screen.getByRole('button', { name: 'Save universe' }))
    await screen.findByText('Universe revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save universe' })).toBeEnabled())
    const first = await current()
    const changed = await fetch(`${apiRoot}/${first.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 1, title: 'Concurrent title' }) })
    expect(changed.status).toBe(200)
    change('Universe title', 'Stale title')
    fireEvent.click(screen.getByRole('button', { name: 'Save universe' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Universe changed')
    expect((await current()).title).toBe('Concurrent title')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByText('Universe revision 2')
    expect(screen.getByLabelText('Universe title')).toHaveValue('Concurrent title')
    expect(screen.getByLabelText('Universe colors')).toHaveValue('#223344')
  })

  it('clears missing and back navigation selections so the previous record cannot be overwritten', async () => {
    render(<Universes apiRoot={apiRoot} />)
    await ready()
    change('Universe title', 'Navigation universe')
    fireEvent.click(screen.getByRole('button', { name: 'Save universe' }))
    await screen.findByText('Universe revision 1')
    const first = await current()
    location.hash = '#/capabilities/creative?view=universes&universe=missing-record'
    fireEvent(window, new HashChangeEvent('hashchange'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Universe not found')
    expect(screen.getByLabelText('Universe title')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Save universe' })).toBeDisabled()
    expect((await (await fetch(`${apiRoot}/${first.id}`)).json()).title).toBe('Navigation universe')
    location.hash = '#/capabilities/creative?view=universes'
    fireEvent(window, new HashChangeEvent('hashchange'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save universe' })).toBeEnabled())
    expect(screen.queryByText('Universe revision 1')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Universe title')).toHaveValue('')
  })

  it('searches and paginates the durable collection and removes draft canon entries', async () => {
    for (let index = 0; index < 26; index++) {
      const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: `Page universe ${index}`, request_id: crypto.randomUUID() }) })
      expect(response.status).toBe(201)
    }
    render(<Universes apiRoot={apiRoot} />)
    await ready()
    change('Search universes', 'Page universe')
    await screen.findByText('26 universes')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled())
    await ready()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled())
    change('Search universes', 'No matches anywhere')
    await screen.findByText('No universes found.')
    expect(screen.getByText('0 universes')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add canon entry' }))
    change('Canon title 1', 'Disposable')
    fireEvent.click(screen.getByRole('button', { name: 'Remove entry 1' }))
    expect(screen.queryByLabelText('Canon title 1')).not.toBeInTheDocument()
    change('Search library references', 'Linked city')
    await screen.findByRole('option', { name: 'Linked city' })
    change('Search library references', 'Nothing')
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Linked city' })).not.toBeInTheDocument())
  })
})
