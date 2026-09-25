import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Commissions from './Commissions'

let server: ChildProcess
let apiRoot: string
let fixture: { work_id: string; work_revision: number; peer_id: string }
let home: string
const repository = resolve(process.cwd(), '../..')

beforeAll(async () => {
  home = mkdtempSync(`${tmpdir()}/gideon-commissions-ui-`)
  server = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python',
    [resolve(repository, 'checks/runtime/capabilities/creative/commission_server.py'), home],
    { env: { ...process.env, PYTHONPATH: resolve(repository, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
  apiRoot = await new Promise<string>((done, reject) => {
    let output = ''; let errors = ''
    server.stderr?.on('data', chunk => { errors += chunk.toString() })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`Commission server exited ${code}: ${errors}`)))
    server.stdout?.on('data', chunk => {
      output += chunk.toString()
      const port = output.split('\n').find(line => /^\d+$/.test(line.trim()))
      if (port) done(`http://127.0.0.1:${port.trim()}/api/capabilities/creative/commissions`)
    })
  })
  fixture = await fetch(apiRoot.replace('/api/capabilities/creative/commissions', '/fixture')).then(response => response.json())
}, 60_000)

afterEach(cleanup)
afterAll(async () => {
  if (server && server.exitCode === null) {
    const ended = new Promise<void>(done => server.once('exit', () => done()))
    server.kill('SIGTERM'); await ended
  }
  if (home) rmSync(home, { recursive: true, force: true })
})

function fillSource() {
  fireEvent.change(screen.getByLabelText('Source work ID'), { target: { value: fixture.work_id } })
  fireEvent.change(screen.getByLabelText('Source revision'), { target: { value: String(fixture.work_revision) } })
}

describe('recurring creative commissions', () => {
  it('uses typed controls for every generation ability', () => {
    render(<Commissions apiRoot={apiRoot} />)
    fireEvent.change(screen.getByLabelText('Execution mode'), { target: { value: 'generate' } })
    expect(screen.getByLabelText('Series production mode')).toBeVisible()
    expect(screen.queryByLabelText('Ability dispatch JSON')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Target ability'), { target: { value: 'image' } })
    expect(screen.getByLabelText('Generation prompt')).toBeVisible()
    expect(screen.getByLabelText('Image size')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Target ability'), { target: { value: 'video' } })
    expect(screen.getByLabelText('Duration seconds')).toBeVisible()
    expect(screen.getByLabelText('Aspect ratio')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Target ability'), { target: { value: 'music' } })
    expect(screen.getByLabelText('Track ID')).toBeVisible()
    expect(screen.getByLabelText('Track revision')).toBeVisible()
    expect(screen.getByLabelText('Length milliseconds')).toBeVisible()
    expect(screen.getByLabelText('License statement')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Target ability'), { target: { value: 'music-video' } })
    expect(screen.getByLabelText('Music video project ID')).toBeVisible()
    expect(screen.getByLabelText('Project revision')).toBeVisible()
  })

  it('creates a scheduled brief, runs a real direction project, and records linked feedback', async () => {
    render(<Commissions apiRoot={apiRoot} />)
    fillSource()
    fireEvent.change(screen.getByLabelText('Commission name'), { target: { value: 'UI standing treatment' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create commission' }))
    const detail = await screen.findByRole('region', { name: 'Commission detail' })
    expect(detail).toHaveTextContent('active')
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(detail).toHaveTextContent('planned'))
    expect(detail).toHaveTextContent('planning only')
    expect(detail).toHaveTextContent('1 attempt')
    expect(detail).toHaveTextContent('Output creative-direction-')
    fireEvent.click(screen.getByRole('button', { name: 'Like' }))
    await waitFor(() => expect(detail).toHaveTextContent('dashboard-owner: liked'))
    expect(await fetch(apiRoot.replace('/api/capabilities/creative/commissions', '/fixture/deliveries')).then(response => response.json()))
      .toEqual({ count: 0 })
    const send = screen.getByRole('button', { name: 'Send feedback to peer' })
    expect(send).toBeDisabled()
    fireEvent.click(screen.getByLabelText(/Approve feedback delivery/))
    expect(await fetch(apiRoot.replace('/api/capabilities/creative/commissions', '/fixture/deliveries')).then(response => response.json()))
      .toEqual({ count: 0 })
    fireEvent.click(send)
    expect(await screen.findByRole('status')).toHaveTextContent('Delivered: already_current · revision 1')
    expect(await fetch(apiRoot.replace('/api/capabilities/creative/commissions', '/fixture/deliveries')).then(response => response.json()))
      .toEqual({ count: 1 })
    const state = await fetch(apiRoot).then(response => response.json())
    expect(state.items).toHaveLength(1)
    const stored = await fetch(`${apiRoot}/${state.items[0].id}`).then(response => response.json())
    expect(stored.runs[0].project_id).toBeTruthy()
    expect(stored.runs[0].outputs[0].content_hash).toMatch(/^[a-f0-9]{64}$/)
    expect(stored.feedback[0].output.artifact_id).toBe(stored.runs[0].outputs[0].artifact_id)
  })

  it('disables the shared schedule while retaining project and run history', async () => {
    render(<Commissions apiRoot={apiRoot} />)
    fireEvent.click(await screen.findByRole('button', { name: 'UI standing treatment' }))
    const detail = await screen.findByRole('region', { name: 'Commission detail' })
    expect(detail).toHaveTextContent('planned')
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    await waitFor(() => expect(detail).toHaveTextContent('disabled'))
    expect(detail).toHaveTextContent('planned')
    expect(screen.getByRole('button', { name: 'Enable' })).toBeEnabled()
  })

  it('surfaces canonical source validation instead of creating an empty commission', async () => {
    render(<Commissions apiRoot={apiRoot} />)
    fireEvent.change(screen.getByLabelText('Source work ID'), { target: { value: 'missing-work' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create commission' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Work not found')
    const state = await fetch(apiRoot).then(response => response.json())
    expect(state.items).toHaveLength(1)
  })

  it('creates a guarded calendar recurrence and shows its next shared-clock fire', async () => {
    render(<Commissions apiRoot={apiRoot} />)
    fillSource()
    fireEvent.change(screen.getByLabelText('Commission name'), { target: { value: 'Monthly calendar treatment' } })
    fireEvent.change(screen.getByLabelText('Cadence kind'), { target: { value: 'recurrence' } })
    fireEvent.change(screen.getByLabelText('Recurrence start'), { target: { value: '2027-01-29T09:00:00' } })
    fireEvent.change(screen.getByLabelText('Recurrence rule'), {
      target: { value: 'FREQ=MONTHLY;COUNT=3;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create commission' }))
    const detail = await screen.findByRole('region', { name: 'Commission detail' })
    expect(detail).toHaveTextContent('active')
    expect(detail).toHaveTextContent('next 2027-01-29T09:00:00')
    const state = await fetch(apiRoot).then(response => response.json())
    expect(state.items).toHaveLength(2)
    expect(state.items.find((item: { name: string }) => item.name === 'Monthly calendar treatment')).toMatchObject({
      schedule_state: 'active',
      next_fire_at: '2027-01-29T09:00:00+00:00',
    })
  })

  it('shows an honest unavailable receipt for an unconfigured image provider', async () => {
    render(<Commissions apiRoot={apiRoot} />)
    fillSource()
    fireEvent.change(screen.getByLabelText('Commission name'), { target: { value: 'Image commission' } })
    fireEvent.change(screen.getByLabelText('Target ability'), { target: { value: 'image' } })
    fireEvent.change(screen.getByLabelText('Execution mode'), { target: { value: 'generate' } })
    fireEvent.change(screen.getByLabelText('Generation prompt'), { target: { value: 'A brass key on a quiet platform.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create commission' }))
    const detail = await screen.findByRole('region', { name: 'Commission detail' })
    expect(detail).toHaveTextContent('generation')
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(detail).toHaveTextContent('failed'))
    expect(detail).toHaveTextContent('Dispatch media_jobs/image_generate: external_unavailable image_provider_unavailable')
    const state = await fetch(apiRoot).then(response => response.json())
    expect(state.items).toHaveLength(3)
  })
})
