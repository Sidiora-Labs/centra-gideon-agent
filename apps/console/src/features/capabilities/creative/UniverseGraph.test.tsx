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

async function seed(title: string, body: string, colors: string[]) {
  const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), title, canon: [{ id: 'law', title: 'Law', body }], visual_identity: { colors, style_notes: title } }) })
  expect(response.status).toBe(201)
  return response.json()
}
async function open(id: string) {
  location.hash = `#/capabilities/creative?view=universes&universe=${id}`
  render(<Universes apiRoot={apiRoot} />)
  await screen.findByText('Universe revision 1')
  await screen.findByText('0 ingredients · 0 relationships')
}

describe('Universe relationship and merge journeys', () => {
  it('previews conflicts, requires choices, merges and exposes durable provenance', async () => {
    const target = await seed('Merge target', 'No sunrise.', ['#112233'])
    const source = await seed('Merge source', 'Sunrise allowed.', ['#aabbcc'])
    await open(target.id)
    await screen.findByRole('option', { name: 'Merge source · revision 1' })
    change('Source universe', source.id)
    fireEvent.click(screen.getByRole('button', { name: 'Preview merge' }))
    await screen.findByRole('region', { name: 'Merge preview' })
    expect(screen.getByText('Target: Law — No sunrise.')).toBeInTheDocument()
    expect(screen.getByText('Source: Law — Sunrise allowed.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply merge' })).toBeDisabled()
    expect((await current()).revision).toBe(1)
    change('Resolve canon law', 'source')
    expect(screen.getByRole('button', { name: 'Apply merge' })).toBeDisabled()
    change('Resolve visual identity', 'target')
    expect(screen.getByRole('button', { name: 'Apply merge' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Apply merge' }))
    await screen.findByText('Universe revision 2')
    expect(screen.getByLabelText('Canon text 1')).toHaveValue('Sunrise allowed.')
    expect(screen.getByLabelText('Universe colors')).toHaveValue('#112233')
    const history = await screen.findByRole('region', { name: 'Universe merge history' })
    expect(history).toHaveTextContent(`Source ${source.id} revision 1 → universe revision 2`)
    expect(screen.queryByRole('region', { name: 'Merge preview' })).not.toBeInTheDocument()
    const sourceAfter = await (await fetch(`${apiRoot}/${source.id}`)).json()
    expect(sourceAfter.revision).toBe(1)
    expect(sourceAfter.canon).toEqual(source.canon)
    const targetBefore = await (await fetch(`${apiRoot}/${target.id}/export?revision=1`)).json()
    expect(targetBefore.canon).toEqual(target.canon)
    cleanup()
    render(<Universes apiRoot={apiRoot} />)
    await screen.findByText('Universe revision 2')
    expect(await screen.findByRole('region', { name: 'Universe merge history' })).toHaveTextContent(source.id)
    fireEvent.click(screen.getByRole('button', { name: 'Restore universe revision 1' }))
    await screen.findByText('Universe revision 3')
    expect(screen.getByLabelText('Canon text 1')).toHaveValue('No sunrise.')
    expect(await screen.findByRole('region', { name: 'Universe merge history' })).toHaveTextContent('universe revision 2')
  })

  it('refuses stale source previews and applies only a refreshed explicit decision', async () => {
    const target = await seed('Stale target', 'Old law', ['#112233'])
    const source = await seed('Stale source', 'New law', ['#aabbcc'])
    await open(target.id)
    change('Search merge sources', 'Stale source')
    await screen.findByRole('option', { name: 'Stale source · revision 1' })
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Merge source · revision 1' })).not.toBeInTheDocument())
    change('Source universe', source.id)
    fireEvent.click(screen.getByRole('button', { name: 'Preview merge' }))
    await screen.findByRole('region', { name: 'Merge preview' })
    change('Resolve canon law', 'source')
    change('Resolve visual identity', 'source')
    const changed = await fetch(`${apiRoot}/${source.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 1, canon: [{ id: 'law', title: 'Law', body: 'Newest law' }] }) })
    expect(changed.status).toBe(200)
    fireEvent.click(screen.getByRole('button', { name: 'Apply merge' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Universe changed; preview the merge again')
    expect((await current()).revision).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Preview merge' }))
    await screen.findByText('Source: Law — Newest law')
    expect(screen.getByRole('button', { name: 'Apply merge' })).toBeDisabled()
    change('Resolve canon law', 'target')
    change('Resolve visual identity', 'source')
    fireEvent.click(screen.getByRole('button', { name: 'Apply merge' }))
    await screen.findByText('Universe revision 2')
    expect(screen.getByLabelText('Canon text 1')).toHaveValue('Old law')
    expect(screen.getByLabelText('Universe colors')).toHaveValue('#aabbcc')
  })

  it('shows actual directed ingredient relations and distinguishes external nodes', async () => {
    const ingredientsUrl = apiRoot.replace(/universes$/, 'ingredients')
    const createIngredient = async (payload: object) => {
      const response = await fetch(ingredientsUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), ...payload }) })
      expect(response.status).toBe(201)
      return response.json()
    }
    const place = await createIngredient({ title: 'External city', type: 'place' })
    const character = await createIngredient({ title: 'Graph character', type: 'character', relations: [{ kind: 'related', target_id: place.id }] })
    const universe = await seed('Graph universe', 'Law', ['#112233'])
    const linked = await fetch(`${apiRoot}/${universe.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 1, ingredient_ids: [character.id] }) })
    expect(linked.status).toBe(200)
    location.hash = `#/capabilities/creative?view=universes&universe=${universe.id}`
    render(<Universes apiRoot={apiRoot} />)
    await screen.findByText('Universe revision 2')
    await screen.findByText('2 ingredients · 1 relationships')
    expect(screen.getByRole('img', { name: 'Universe relationship graph' })).toBeInTheDocument()
    expect(screen.getByText('Graph character — Universe member')).toBeInTheDocument()
    expect(screen.getByText('External city — Outside this universe')).toBeInTheDocument()
    expect(screen.getByText('Graph character → related → External city')).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Graph universe · revision 2' })).not.toBeInTheDocument()
    change('Search merge sources', 'Does not exist')
    await waitFor(() => expect(within(screen.getByLabelText('Source universe')).getAllByRole('option')).toHaveLength(1))
  })
})
