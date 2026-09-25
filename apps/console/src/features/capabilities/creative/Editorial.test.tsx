import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Editorial from './Editorial'
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

async function post(path: string, data: unknown) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
  expect(response.status).toBe(path === apiRoot ? 201 : 200)
  return response.json()
}
const original = '😀 Time stood still. She saw the door. The last line stays intact.'
async function seed(text = original) {
  const work = await post(apiRoot, { request_id: crypto.randomUUID(), title: 'Editorial work' })
  return (await post(`${apiRoot}/${work.id}/drafts`, { request_id: crypto.randomUUID(), revision: 1, text })).work
}
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }) }

describe('Editorial checks and explicit canonical repair review', () => {
  it('persists editorial policy and a custom check through real HTTP controls', async () => {
    const work = await seed()
    render(<Editorial id={work.id} revision={2} text={original} apiRoot={apiRoot} onPrepared={() => {}} />)
    await screen.findByText(new RegExp(`Scope: work:${work.id}`))
    change('Readiness gate', 'block_high')
    change('Override check', 'prose.cliches')
    change('Override severity', 'high')
    fireEvent.click(screen.getByRole('button', { name: 'Save editorial policy' }))
    await screen.findByText('Editorial policy saved for the canonical series scope.')
    let state = await (await fetch(`${apiRoot}/${work.id}/editorial`)).json()
    expect(state.controls.policy.revision).toBe(1)
    expect(state.controls.policy.readiness_gate).toBe('block_high')
    expect(state.controls.policy.checks['prose.cliches']).toEqual({ enabled: true, severity: 'high' })
    change('Custom check label', 'Promise audit')
    change('Custom check instruction', 'Find a broken promise and quote exact prose.')
    change('Override severity', 'medium')
    fireEvent.click(screen.getByRole('button', { name: 'Create custom check' }))
    await screen.findByText('Custom editorial check saved.')
    state = await (await fetch(`${apiRoot}/${work.id}/editorial`)).json()
    expect(state.controls.custom_checks).toHaveLength(1)
    expect(state.controls.custom_checks[0].label).toBe('Promise audit')
    expect(state.controls.custom_checks[0].prompt).toBe('Find a broken promise and quote exact prose.')
    expect(state.catalog.find((item: { id: string }) => item.id === state.controls.custom_checks[0].id).enabled).toBe(true)
    const catalog = screen.getByText('Editorial check catalog').parentElement!
    const customRow = Array.from(catalog.querySelectorAll('label')).find(row => row.textContent?.includes('Promise audit'))
    expect(customRow?.querySelector('input')).toBeDisabled()
  })

  it('makes rank insufficiency explicit without creating review evidence', async () => {
    const work = await seed()
    render(<Editorial id={work.id} revision={2} text={original} apiRoot={apiRoot} onPrepared={() => {}} />)
    await screen.findByText(new RegExp(`Scope: work:${work.id}`))
    change('Review mode', 'rank')
    fireEvent.click(screen.getByRole('button', { name: 'Run explicit review' }))
    await screen.findByRole('alert')
    const state = await (await fetch(`${apiRoot}/${work.id}/editorial`)).json()
    expect(state.controls.reviews).toEqual([])
    expect(screen.queryByText(/rank: completed/)).not.toBeInTheDocument()
  })

  it('binds typed canonical context and enables its checks through real HTTP', async () => {
    const work = await seed()
    render(<Editorial id={work.id} revision={2} text={original} apiRoot={apiRoot} onPrepared={() => {}} />)
    await screen.findByText('canon: missing · no_canonical_context_binding')
    change('Canonical JSON', JSON.stringify({
      characters: [{ id: 'hero', name: 'Hero' }, { id: 'hera', name: 'Hera' }],
      objects: [], rules: [],
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Bind canonical context' }))
    await screen.findByText('canon context bound to an immutable JSON artifact.')
    await screen.findByText('canon: available · revision 1')
    const state = await (await fetch(`${apiRoot}/${work.id}/editorial`)).json()
    const canon = state.contexts.find((item: { family: string }) => item.family === 'canon')
    expect(canon.artifact_version).toBe(1)
    expect(canon.data.characters[0].name).toBe('Hero')
    const catalog = screen.getByText('Editorial check catalog').parentElement!
    const naming = Array.from(catalog.querySelectorAll('label')).find(row => row.textContent?.includes('Character name dissimilarity'))?.querySelector('input')
    expect(naming).toBeEnabled()
  })

  it('surfaces malformed context without claiming a binding', async () => {
    const work = await seed()
    render(<Editorial id={work.id} revision={2} text={original} apiRoot={apiRoot} onPrepared={() => {}} />)
    await screen.findByText('canon: missing · no_canonical_context_binding')
    change('Canonical JSON', '{broken')
    fireEvent.click(screen.getByRole('button', { name: 'Bind canonical context' }))
    await screen.findByRole('alert')
    expect(screen.getByText('canon: missing · no_canonical_context_binding')).toBeInTheDocument()
  })

  it('runs exact prose checks, prepares a candidate, promotes and restores through the real Works page', async () => {
    const work = await seed()
    location.hash = `#/capabilities/creative?view=works&work=${work.id}`
    render(<Works apiRoot={apiRoot} />)
    const runButton = await screen.findByRole('button', { name: 'Run selected editorial checks' })
    await waitFor(() => expect(runButton).toBeEnabled())
    fireEvent.click(runButton)
    fireEvent.click(await screen.findByRole('button', { name: 'Review phrase: time stood still' }))
    expect(screen.getByLabelText('Finding quotation')).toHaveValue('Time stood still')
    expect(screen.getByText('Exact source span 2–18')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Editorial run 1' })).toHaveTextContent('prose.cliches: completed')
    expect(screen.getByRole('button', { name: 'Prepare reviewed repair' })).toBeDisabled()
    change('My editorial replacement', 'The clock stopped')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare reviewed repair' }))
    await screen.findByText('Repair candidate prepared. Review and promote it in manuscript polishing.')
    expect(screen.getByLabelText('Manuscript')).toHaveValue(original)
    fireEvent.click(await screen.findByRole('button', { name: 'Review candidate 1' }))
    await screen.findByLabelText('Candidate passage')
    expect(screen.getByLabelText('Original passage')).toHaveValue('Time stood still')
    expect(screen.getByLabelText('Candidate passage')).toHaveValue('The clock stopped')
    fireEvent.click(screen.getByRole('button', { name: 'Promote reviewed candidate' }))
    await waitFor(() => expect(screen.getByLabelText('Manuscript')).toHaveValue(original.replace('Time stood still', 'The clock stopped')))
    await screen.findByText('Run source changed')
    const promoted = await (await fetch(`${apiRoot}/${work.id}`)).json()
    expect(promoted.revision).toBe(3)
    expect(promoted.text.endsWith('The last line stays intact.')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Restore work revision 2' }))
    await waitFor(() => expect(screen.getByLabelText('Manuscript')).toHaveValue(original))
    const restored = await (await fetch(`${apiRoot}/${work.id}`)).json()
    expect(restored.revision).toBe(4)
    expect(restored.active_draft_id).toBe(work.active_draft_id)
    const state = await (await fetch(`${apiRoot}/${work.id}/editorial`)).json()
    expect(state.runs).toHaveLength(1)
    expect(state.runs[0].stale).toBe(true)
    expect(state.runs[0].findings[0].quote).toBe('Time stood still')
  })

  it('shows registry availability and retains historical findings after a source revision', async () => {
    const work = await seed()
    const view = render(<Editorial id={work.id} revision={2} text={original} apiRoot={apiRoot} onPrepared={() => { location.hash = '#prepared' }} />)
    const runButton = await screen.findByRole('button', { name: 'Run selected editorial checks' })
    await waitFor(() => expect(runButton).toBeEnabled())
    const catalog = screen.getByText('Editorial check catalog').parentElement!
    const all = catalog.querySelectorAll('input[type="checkbox"]')
    expect(all).toHaveLength(80)
    const disabled = Array.from(all).filter(input => (input as HTMLInputElement).disabled)
    expect(disabled.length).toBeGreaterThan(0)
    expect(Array.from(all).filter(input => (input as HTMLInputElement).checked).length).toBeGreaterThan(10)
    fireEvent.click(runButton)
    await screen.findByRole('button', { name: 'Review phrase: time stood still' })
    const newer = (await post(`${apiRoot}/${work.id}/drafts`, { request_id: crypto.randomUUID(), revision: 2, text: 'A different saved draft.' })).work
    view.rerender(<Editorial id={work.id} revision={newer.revision} text="A different saved draft." apiRoot={apiRoot} onPrepared={() => { location.hash = '#prepared' }} />)
    await screen.findByText('Run source changed')
    fireEvent.click(screen.getByRole('button', { name: 'Review phrase: time stood still' }))
    change('My editorial replacement', 'New phrase')
    expect(screen.getByRole('button', { name: 'Prepare reviewed repair' })).toBeDisabled()
    expect(screen.getByLabelText('Finding quotation')).toHaveValue('Time stood still')
    expect(screen.getByLabelText('Editorial coverage end')).toHaveValue(24)
    view.rerender(<Editorial id="missing" revision={1} text="" apiRoot={apiRoot} onPrepared={() => { location.hash = '#prepared' }} />)
    await screen.findByRole('alert')
    expect(screen.queryByLabelText('Finding quotation')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run selected editorial checks' })).toBeDisabled()
  })

  it('preserves bounded partial coverage and does not label it a clean manuscript', async () => {
    const work = await seed()
    render(<Editorial id={work.id} revision={2} text={original} apiRoot={apiRoot} onPrepared={() => { location.hash = '#prepared' }} />)
    const runButton = await screen.findByRole('button', { name: 'Run selected editorial checks' })
    await waitFor(() => expect(runButton).toBeEnabled())
    change('Editorial coverage start', '40')
    change('Editorial coverage end', '62')
    fireEvent.click(runButton)
    const region = await screen.findByRole('region', { name: 'Editorial run 1' })
    expect(region).toHaveTextContent('coverage 40–62')
    const state = await (await fetch(`${apiRoot}/${work.id}/editorial`)).json()
    expect(state.runs[0].coverage).toEqual({ start: 40, end: 62, total_characters: Array.from(original).length })
    expect(state.runs[0].readiness).not.toBe('selected_checks_clear')
    change('Editorial coverage end', '30000')
    expect(runButton).toBeDisabled()
    const current = await (await fetch(`${apiRoot}/${work.id}`)).json()
    expect(current.revision).toBe(2)
    expect(current.text).toBe(original)
  })
})
