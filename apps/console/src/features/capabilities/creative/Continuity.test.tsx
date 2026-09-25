import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Continuity from './Continuity'

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

async function post(path: string, data: unknown) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
  expect(response.status).toBe(path === apiRoot ? 201 : 200)
  return response.json()
}
async function seed(text: string) {
  const work = await post(apiRoot, { request_id: crypto.randomUUID(), title: 'Continuity story' })
  return (await post(`${apiRoot}/${work.id}/drafts`, { request_id: crypto.randomUUID(), revision: 1, text })).work
}
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }) }
async function ready() { await waitFor(() => expect(screen.queryByText('Loading continuity…')).not.toBeInTheDocument()) }

describe('Canonical reverse outline and continuity review', () => {
  it('uses emoji codepoint spans, explicitly reviews authored evidence and exports real ledger', async () => {
    const text = '😀 نور carries a red key.'
    const work = await seed(text)
    const { unmount } = render(<Continuity id={work.id} revision={2} text={text} apiRoot={apiRoot} />)
    await screen.findByText('No evidence proposals yet.')
    change('Evidence start', '2')
    change('Evidence end', '5')
    expect(screen.getByLabelText('Evidence passage')).toHaveValue('نور')
    change('Outline summary', 'Noor appears')
    change('Fact subject', 'Traveler')
    change('Fact predicate', 'name')
    change('Fact value', 'Noor')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare evidence for review' }))
    await screen.findByRole('button', { name: 'Accept evidence 1' })
    expect(screen.getByText(/Provenance: authored/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Accepted reverse outline' })).not.toHaveTextContent('Noor appears')
    const proposalResponse = await fetch(`${apiRoot}/${work.id}/continuity`)
    const before = await proposalResponse.json()
    expect(before.revision).toBe(0)
    expect(before.proposals[0].outline[0]).toMatchObject({ start: 2, end: 5, quote: 'نور' })
    expect(screen.getByText(`Source draft ${work.active_draft_id} · version 1`)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open pinned source' })).toHaveAttribute('href', `/api/artifacts/${before.proposals[0].artifact_id}/raw?version=1`)
    fireEvent.click(screen.getByRole('button', { name: 'Accept evidence 1' }))
    await screen.findByText('Accepted at ledger revision 1')
    expect(screen.getByRole('region', { name: 'Accepted reverse outline' })).toHaveTextContent('Noor appears')
    expect(screen.getByRole('region', { name: 'Accepted continuity facts' })).toHaveTextContent('Traveler · name · Noor')
    fireEvent.click(screen.getByRole('button', { name: 'Export continuity ledger' }))
    const exported = JSON.parse((await screen.findByLabelText('Continuity export') as HTMLTextAreaElement).value)
    expect(exported.schema_version).toBe(1)
    expect(exported.facts[0].quote).toBe('نور')
    expect(exported.facts[0].draft_id).toBe(work.active_draft_id)
    unmount()
    render(<Continuity id={work.id} revision={2} text={text} apiRoot={apiRoot} />)
    await screen.findByText('Accepted at ledger revision 1')
    expect(screen.getByRole('region', { name: 'Accepted reverse outline' })).toHaveTextContent('Noor appears')
    const persisted = await (await fetch(`${apiRoot}/${work.id}`)).json()
    expect(persisted.text).toBe(text)
    expect(persisted.revision).toBe(2)
  })

  it('shows recorded conflicting values and source order independent of acceptance order', async () => {
    const text = 'Red key. Blue key.'
    const work = await seed(text)
    render(<Continuity id={work.id} revision={2} text={text} apiRoot={apiRoot} />)
    await ready()
    change('Evidence start', '9')
    change('Evidence end', '18')
    change('Outline summary', 'Blue scene')
    change('Fact subject', 'key')
    change('Fact predicate', 'color')
    change('Fact value', 'blue')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare evidence for review' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Accept evidence 1' }))
    await screen.findByText('Accepted at ledger revision 1')
    change('Evidence start', '0')
    change('Evidence end', '8')
    change('Outline summary', 'Red scene')
    change('Fact value', 'red')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare evidence for review' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Accept evidence 2' }))
    await screen.findByText('Accepted at ledger revision 2')
    expect(screen.getByRole('region', { name: 'Recorded value conflicts' })).toHaveTextContent('key · color: blue / red')
    const ordered = screen.getByRole('region', { name: 'Accepted reverse outline' }).querySelectorAll('li')
    expect(ordered).toHaveLength(2)
    expect(ordered[0]).toHaveTextContent('Red scene')
    expect(ordered[1]).toHaveTextContent('Blue scene')
    const saved = await (await fetch(`${apiRoot}/${work.id}/continuity`)).json()
    expect(saved.proposals).toHaveLength(2)
    expect(saved.conflicts[0].proposal_ids).toHaveLength(2)
  })

  it('retains old evidence after a new saved draft and blocks stale proposal acceptance', async () => {
    const text = 'The door is open.'
    const work = await seed(text)
    const view = render(<Continuity id={work.id} revision={2} text={text} apiRoot={apiRoot} />)
    await ready()
    change('Outline summary', 'Door opens')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare evidence for review' }))
    await screen.findByRole('button', { name: 'Accept evidence 1' })
    const newer = (await post(`${apiRoot}/${work.id}/drafts`, { request_id: crypto.randomUUID(), revision: 2, text: 'The door is closed.' })).work
    view.rerender(<Continuity id={work.id} revision={newer.revision} text="The door is closed." apiRoot={apiRoot} />)
    await screen.findByText('Earlier draft; create a new proposal to use current text.')
    expect(screen.getByRole('button', { name: 'Accept evidence 1' })).toBeDisabled()
    expect(screen.getByText('The door is open.')).toBeInTheDocument()
    expect(screen.getByLabelText('Evidence passage')).toHaveValue('The door is closed.')
    change('Outline summary', 'Door closes')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare evidence for review' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Accept evidence 2' }))
    await screen.findByText('Accepted at ledger revision 1')
    expect(screen.getByRole('region', { name: 'Accepted reverse outline' })).toHaveTextContent('Door closes')
    expect(screen.getByRole('region', { name: 'Accepted reverse outline' })).not.toHaveTextContent('Door opens')
    view.rerender(<Continuity id="missing" revision={1} text="" apiRoot={apiRoot} />)
    await screen.findByRole('alert')
    expect(screen.queryByText('Door closes')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prepare evidence for review' })).toBeDisabled()
  })
})
