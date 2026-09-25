import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Series from './Series'
import Voice from './Voice'

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

async function send(path: string, data: unknown, method = 'POST') {
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
  expect(response.status).toBe(path === apiRoot ? 201 : 200)
  return response.json()
}
const short = 'The key is red. '.repeat(15)
const long = 'The traveler carried a heavy iron key along the narrow road beside the silent river. '.repeat(5)
async function seed(drafted = true) {
  const series = await send(apiRoot, { request_id: crypto.randomUUID(), title: 'Diagnostic series', volumes: [{ id: 'volume', title: 'Volume', chapters: [{ id: 'one', title: 'First chapter', prompt: 'Begin' }, { id: 'two', title: 'Second chapter', prompt: 'Continue' }] }] })
  const works = []
  for (const [index, chapter] of ['one', 'two'].entries()) {
    const work = await send(`${apiRoot}/${series.id}/chapters/${chapter}/prepare`, { revision: 1 })
    if (drafted) await send(`${apiRoot.replace(/series$/, 'works')}/${work.id}/drafts`, { request_id: crypto.randomUUID(), revision: 1, text: index === 0 ? short : long })
    works.push(work)
  }
  return { series, works }
}
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }) }

describe('Deterministic voice matrix over actual canonical sources', () => {
  it('configures vocabulary and drift, reopens exact history and exports findings', async () => {
    const { series } = await seed()
    const view = render(<Voice id={series.id} revision={1} apiRoot={apiRoot} />)
    await screen.findByRole('table', { name: 'Voice metrics matrix' })
    expect(screen.getByText('No threshold crossings.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Voice sources' })).toHaveTextContent('First chapter: 60 words')
    expect(screen.getByRole('region', { name: 'Voice sources' })).toHaveTextContent('Second chapter: 75 words')
    change('Drift threshold', '0.5')
    change('Vocabulary groups', 'metal: key, iron')
    fireEvent.click(screen.getByRole('button', { name: 'Save voice configuration' }))
    await screen.findByText(/configuration revision 1/)
    expect(screen.getByRole('table', { name: 'Voice metrics matrix' })).toHaveTextContent('Vocabulary: metal per 1000 words')
    expect(screen.getByRole('region', { name: 'Voice drift findings' })).toHaveTextContent('First chapter · Sentence length mean · lower')
    expect(screen.getByRole('region', { name: 'Voice drift findings' })).toHaveTextContent('Second chapter · Sentence length mean · higher')
    fireEvent.click(screen.getByRole('button', { name: 'Export voice diagnostics' }))
    const exported = JSON.parse((await screen.findByLabelText('Voice diagnostics export') as HTMLTextAreaElement).value)
    expect(exported.config.wells).toEqual({ metal: ['iron', 'key'] })
    expect(exported.baseline.sentence_mean).toEqual({ drafted_mean: 9.5, center: 9.5, std: 5.5 })
    expect(exported.config_history).toHaveLength(1)
    expect(exported.rows[0].artifact_version).toBe(1)
    expect(exported.findings.some((f: { metric: string }) => f.metric === 'sentence_mean')).toBe(true)
    view.unmount()
    render(<Voice id={series.id} revision={1} apiRoot={apiRoot} />)
    await screen.findByText(/configuration revision 1/)
    expect(screen.getByLabelText('Vocabulary groups')).toHaveValue('metal: iron, key')
    expect(screen.getByLabelText('Drift threshold')).toHaveValue(0.5)
  })

  it('shows missing baseline without false zeros and retains invalid edits for correction', async () => {
    const { series } = await seed(false)
    render(<Voice id={series.id} revision={1} apiRoot={apiRoot} />)
    await screen.findByRole('table', { name: 'Voice metrics matrix' })
    expect(screen.getByRole('status')).toHaveTextContent('Too few eligible chapters')
    expect(screen.getByRole('region', { name: 'Voice sources' })).toHaveTextContent('Unknown words')
    const firstRow = screen.getByRole('table').querySelectorAll('tbody tr')[0]
    expect(firstRow).toHaveTextContent('Unavailable')
    change('Voice baseline', 'exemplars')
    fireEvent.click(screen.getByRole('button', { name: 'Save voice configuration' }))
    await screen.findByText('Drift unavailable: Pinned author samples are missing or too short')
    change('Vocabulary groups', 'not a valid group')
    fireEvent.click(screen.getByRole('button', { name: 'Save voice configuration' }))
    await screen.findByRole('alert')
    expect(screen.getByLabelText('Vocabulary groups')).toHaveValue('not a valid group')
    const result = await (await fetch(`${apiRoot}/${series.id}/voice`)).json()
    expect(result.config.revision).toBe(1)
    expect(result.baseline.sentence_mean.center).toBeNull()
    expect(result.rows).toHaveLength(2)
  })

  it('refreshes embedded diagnostics immediately after a real staged manuscript save', async () => {
    const { series } = await seed(false)
    location.hash = `#/capabilities/creative?view=series&series=${series.id}`
    render(<Series apiRoot={apiRoot} />)
    await screen.findByRole('table', { name: 'Voice metrics matrix' })
    expect(screen.getByRole('region', { name: 'Voice sources' })).toHaveTextContent('First chapter: Unknown words')
    change('Drafting chapter', 'one')
    const manuscript = await screen.findByLabelText('Chapter manuscript')
    await waitFor(() => expect(manuscript).toBeEnabled())
    change('Chapter manuscript', short)
    fireEvent.click(screen.getByRole('button', { name: 'Save staged chapter draft' }))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Voice sources' })).toHaveTextContent('First chapter: 60 words'))
    expect(screen.getByRole('region', { name: 'Voice sources' })).toHaveTextContent('Second chapter: Unknown words')
    expect(screen.getByRole('table', { name: 'Voice metrics matrix' })).toHaveTextContent('4')
    const current = await (await fetch(`${apiRoot}/${series.id}/voice`)).json()
    expect(current.rows[0].fingerprint.metrics.sentence_mean).toBe(4)
    expect(current.rows[0].draft_id).toBeTruthy()
    expect(current.rows[1].fingerprint).toBeNull()
    expect(current.gate).toBe('below_min_chapters')
  })
})
