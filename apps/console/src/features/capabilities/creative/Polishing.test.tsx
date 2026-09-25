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


async function seed(title: string) {
  const created = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, request_id: crypto.randomUUID() }) })
  expect(created.status).toBe(201)
  const work = await created.json()
  const drafted = await fetch(`${apiRoot}/${work.id}/drafts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), revision: 1, text: 'The train arived late.\nAn unchanged second line.\n' }) })
  expect(drafted.status).toBe(200)
  location.hash = `#/capabilities/creative?view=works&work=${work.id}`
  render(<Works apiRoot={apiRoot} />)
  await screen.findByText('Work revision 2')
  await screen.findByRole('region', { name: 'Bounded manuscript polishing' })
  change('Polishing mode', 'authored')
  change('Passage start', '0')
  change('Passage end', '22')
  change('Polishing instruction', 'Correct spelling.')
  change('My replacement passage', 'The train arrived late.')
  return work
}

describe('Reviewed bounded manuscript polishing', () => {
  it('prepares candidate without changing draft, reviews exact diff and promotes explicitly', async () => {
    const work = await seed('Reviewed polish')
    expect(screen.getByLabelText('Selected saved passage')).toHaveValue('The train arived late.')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare polishing candidate' }))
    await screen.findByRole('region', { name: 'Polishing review' })
    expect(screen.getByLabelText('Original passage')).toHaveValue('The train arived late.')
    expect(screen.getByLabelText('Candidate passage')).toHaveValue('The train arrived late.')
    expect(screen.getByRole('region', { name: 'Polishing review' })).toHaveTextContent('-The train arived late.')
    expect((await current()).revision).toBe(2)
    expect((await current()).text).toBe('The train arived late.\nAn unchanged second line.\n')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Promote reviewed candidate' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Promote reviewed candidate' }))
    await screen.findByText('Work revision 3')
    expect(screen.getByLabelText('Manuscript')).toHaveValue('The train arrived late.\nAn unchanged second line.\n')
    const promoted = await current()
    expect(promoted.active_draft.artifact_id).toMatch(/^creative-polish-/)
    const history = await (await fetch(`${apiRoot}/${work.id}/drafts`)).json()
    expect(history.items).toHaveLength(2)
    fireEvent.click(await screen.findByRole('button', { name: 'Review candidate 1' }))
    await screen.findByText('Candidate promoted')
    expect(screen.queryByRole('button', { name: 'Promote reviewed candidate' })).not.toBeInTheDocument()
    cleanup()
    render(<Works apiRoot={apiRoot} />)
    await screen.findByText('Work revision 3')
    fireEvent.click(await screen.findByRole('button', { name: 'Review candidate 1' }))
    await screen.findByText('Candidate promoted')
    fireEvent.click(screen.getByRole('button', { name: 'Restore work revision 2' }))
    await screen.findByText('Work revision 4')
    expect(screen.getByLabelText('Manuscript')).toHaveValue('The train arived late.\nAn unchanged second line.\n')
  })

  it('rejects promotion after concurrent changes and retains candidate for review', async () => {
    const work = await seed('Stale polish')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare polishing candidate' }))
    await screen.findByRole('region', { name: 'Polishing review' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Promote reviewed candidate' })).toBeEnabled())
    const changed = await fetch(`${apiRoot}/${work.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 2, title: 'Concurrent edit' }) })
    expect(changed.status).toBe(200)
    fireEvent.click(screen.getByRole('button', { name: 'Promote reviewed candidate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Work changed; create a fresh polishing proposal')
    expect(screen.getByLabelText('Candidate passage')).toHaveValue('The train arrived late.')
    expect((await current()).text).toBe('The train arived late.\nAn unchanged second line.\n')
    expect((await current()).revision).toBe(3)
    const drafts = await (await fetch(`${apiRoot}/${work.id}/drafts`)).json()
    expect(drafts.items).toHaveLength(1)
  })

  it('uses saved text rather than unsaved edits and bounds passage selection', async () => {
    await seed('Bounded polish')
    change('Manuscript', 'Unsaved editor change')
    expect(screen.getByLabelText('Selected saved passage')).toHaveValue('The train arived late.')
    change('Passage end', '5000')
    expect(screen.getByRole('button', { name: 'Prepare polishing candidate' })).toBeDisabled()
    change('Passage end', '0')
    expect(screen.getByRole('button', { name: 'Prepare polishing candidate' })).toBeDisabled()
    change('Passage end', '22')
    change('My replacement passage', 'The train arived late.')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare polishing candidate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('changed, nonempty bounded manuscript')
    expect(screen.queryByRole('region', { name: 'Polishing review' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Manuscript')).toHaveValue('Unsaved editor change')
  })
})
