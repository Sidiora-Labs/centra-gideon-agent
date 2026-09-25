import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Production from './Production'

let server: ChildProcess
let apiRoot: string
let home: string
const repository = resolve(process.cwd(), '../..')

beforeAll(async () => {
  home = mkdtempSync(`${tmpdir()}/gideon-production-ui-`)
  server = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python',
    [resolve(repository, 'checks/runtime/capabilities/creative/production_server.py'), home],
    { env: { ...process.env, PYTHONPATH: resolve(repository, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
  apiRoot = await new Promise<string>((done, reject) => {
    let output = ''; let errors = ''
    server.stderr?.on('data', chunk => { errors += chunk.toString() })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`Production server exited ${code}: ${errors}`)))
    server.stdout?.on('data', chunk => {
      output += chunk.toString()
      const port = output.split('\n').find(line => /^\d+$/.test(line.trim()))
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/series`)
    })
  })
})
afterEach(cleanup)
afterAll(async () => {
  if (server && server.exitCode === null) {
    const ended = new Promise<void>(done => server.once('exit', () => done()))
    server.kill('SIGTERM'); await ended
  }
  if (home) rmSync(home, { recursive: true, force: true })
})

async function post(path: string, body: unknown) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  expect(response.status).toBeLessThan(300)
  return response.json()
}

async function seed(suffix: string) {
  const series = await post(apiRoot, {
    request_id: `series-${suffix}`, title: `Production ${suffix}`, synopsis: 'Mara follows a key.',
    volumes: [{ id: `volume-${suffix}`, title: 'Volume', chapters: [{ id: `chapter-${suffix}`, title: 'Chapter', prompt: 'Write the chapter.' }] }],
    arcs: [{ id: `arc-${suffix}`, title: 'Arc', summary: 'Follow the key.', chapter_ids: [`chapter-${suffix}`] }],
  })
  const work = await post(`${apiRoot}/${series.id}/chapters/chapter-${suffix}/prepare`, { revision: series.revision })
  return { series, work }
}

describe('bounded series production', () => {
  it('prepares, approves, editorially reviews, and completes an authored chapter', async () => {
    const { series } = await seed('journey')
    render(<Production id={series.id} revision={series.revision} apiRoot={apiRoot} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start production run' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Start production run' }))
    await screen.findByText('Bounded production run started.')
    let region = await screen.findByRole('region', { name: 'Production run' })
    expect(region).toHaveTextContent('Status running')
    fireEvent.click(screen.getByRole('button', { name: 'Advance one stage' }))
    await screen.findByText('Production advance recorded.')
    region = screen.getByRole('region', { name: 'Production run' })
    expect(region).toHaveTextContent('authored_candidate_required')
    fireEvent.change(screen.getByLabelText('Authored chapter candidate'), { target: { value: 'Mara entered the station and checked the brass key before dawn.' } })
    fireEvent.change(screen.getByLabelText('Candidate note'), { target: { value: 'Written by the owner.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Prepare authored candidate' }))
    await screen.findByText(/Authored candidate prepared/)
    expect(screen.getByRole('region', { name: 'Production run' })).toHaveTextContent('Status awaiting_approval')
    expect(screen.getByRole('region', { name: 'Production run' })).toHaveTextContent('artifact')
    fireEvent.click(screen.getByRole('button', { name: 'Approve candidate draft' }))
    await screen.findByText('Production approve recorded.')
    fireEvent.click(screen.getByRole('button', { name: 'Advance one stage' }))
    await screen.findByText('Production advance recorded.')
    expect(screen.getByRole('button', { name: 'Keep generated draft' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Keep generated draft' }))
    await screen.findByText('Production approve recorded.')
    fireEvent.click(screen.getByRole('button', { name: 'Advance one stage' }))
    await screen.findByText('Production advance recorded.')
    expect(screen.getByRole('region', { name: 'Production run' })).toHaveTextContent('Status done')
    const state = await (await fetch(`${apiRoot}/${series.id}/production`)).json()
    expect(state.items[0].status).toBe('done')
    expect(state.items[0].source_pins).toHaveLength(1)
    expect(state.items[0].editorial_runs).toHaveLength(1)
  })

  it('shows explicit pause, resume, cancel request, and terminal cancellation', async () => {
    const { series } = await seed('controls')
    render(<Production id={series.id} revision={series.revision} apiRoot={apiRoot} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Start production run' }))
    await screen.findByText('Bounded production run started.')
    fireEvent.click(screen.getByRole('button', { name: 'Pause after current stage' }))
    await screen.findByText('Production pause recorded.')
    fireEvent.click(screen.getByRole('button', { name: 'Advance one stage' }))
    await screen.findByText('Production advance recorded.')
    expect(screen.getByRole('region', { name: 'Production run' })).toHaveTextContent('user_requested')
    fireEvent.click(screen.getByRole('button', { name: 'Resume production' }))
    await screen.findByText('Production resume recorded.')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel production' }))
    await screen.findByText('Production cancel recorded.')
    fireEvent.click(screen.getByRole('button', { name: 'Advance one stage' }))
    await screen.findByText('Production advance recorded.')
    expect(screen.getByRole('region', { name: 'Production run' })).toHaveTextContent('Status canceled')
    expect(screen.queryByRole('button', { name: 'Resume production' })).not.toBeInTheDocument()
  })

  it('rejects invalid model-attempt bounds before a run is created', async () => {
    const { series } = await seed('bounds')
    render(<Production id={series.id} revision={series.revision} apiRoot={apiRoot} />)
    fireEvent.change(await screen.findByLabelText('Maximum model attempts'), { target: { value: '4' } })
    expect(screen.getByRole('button', { name: 'Start production run' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Maximum model attempts'), { target: { value: '0' } })
    expect(screen.getByRole('button', { name: 'Start production run' })).toBeDisabled()
    const state = await (await fetch(`${apiRoot}/${series.id}/production`)).json()
    expect(state.items).toEqual([])
  })
})
