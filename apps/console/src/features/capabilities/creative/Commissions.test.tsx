import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Commissions from './Commissions'

let server: ChildProcess
let apiRoot: string
let fixture: { work_id: string; work_revision: number }
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
})

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
  it('creates a scheduled brief, runs a real direction project, and records linked feedback', async () => {
    render(<Commissions apiRoot={apiRoot} />)
    fillSource()
    fireEvent.change(screen.getByLabelText('Commission name'), { target: { value: 'UI standing treatment' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create commission' }))
    const detail = await screen.findByRole('region', { name: 'Commission detail' })
    expect(detail).toHaveTextContent('scheduled')
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(detail).toHaveTextContent('completed'))
    expect(detail).toHaveTextContent('1 attempt')
    expect(detail).toHaveTextContent('Output creative-direction-')
    fireEvent.click(screen.getByRole('button', { name: 'Like' }))
    await waitFor(() => expect(detail).toHaveTextContent('dashboard-owner: liked'))
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
    expect(detail).toHaveTextContent('completed')
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    await waitFor(() => expect(detail).toHaveTextContent('disabled'))
    expect(detail).toHaveTextContent('completed')
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
})
