import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import ContinuityPage from './ContinuityPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/continuity_ui_server.py'), directory,
  ], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') } })
  endpoint = await new Promise<string>((resolveEndpoint, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error('HTTP server startup timed out: ' + errors)), 10000)
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('http://'))
      if (line) { clearTimeout(timer); resolveEndpoint(line.trim()) }
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

test('actual heartbeat policy and canonical continuity notes survive reload and human removal', async () => {
  render(<ContinuityPage endpoint={endpoint} />)
  await screen.findByText('Allowed by policy')
  expect(screen.getByText('Provider readiness: unknown')).toBeVisible()
  expect(screen.getByRole('region', { name: 'Heartbeat control' })).toHaveTextContent('Scheduled heartbeat turns only')
  fireEvent.click(screen.getByRole('button', { name: 'Pause scheduled turns' }))
  await screen.findByText('Paused')
  expect(screen.getByRole('region', { name: 'Control journal' })).toHaveTextContent('1 · pause')
  let state = await (await fetch(endpoint)).json()
  expect(state.heartbeat_paused).toBe(true)
  expect(state.revision).toBe(1)
  fireEvent.change(screen.getByLabelText('Continuity note'), { target: { value: 'Keep careful evidence.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add continuity note' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Continuity context' })).toHaveTextContent('Keep careful evidence.'))
  expect(screen.getByLabelText('Continuity note')).toHaveValue('')
  state = await (await fetch(endpoint)).json()
  expect(state.slots.persona).toHaveLength(1)
  expect(state.memory_events[0].source).toBe('user_explicit')
  cleanup()
  render(<ContinuityPage endpoint={endpoint} />)
  await screen.findByText('Paused')
  expect(screen.getByRole('region', { name: 'Continuity context' })).toHaveTextContent('Keep careful evidence.')
  fireEvent.click(screen.getByRole('button', { name: 'Remove note' }))
  await screen.findByText('Keep careful evidence. · Removed by human')
  expect(screen.getByRole('region', { name: 'Continuity context' })).not.toHaveTextContent('Keep careful evidence.')
  fireEvent.change(screen.getByLabelText('Continuity note'), { target: { value: 'Keep careful evidence.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add continuity note' }))
  await waitFor(() => expect(screen.getByLabelText('Continuity note')).toHaveValue(''))
  expect(screen.queryByRole('button', { name: 'Remove note' })).not.toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Continuity context' })).not.toHaveTextContent('Keep careful evidence.')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add continuity note' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Memory slot'), { target: { value: 'self_notes' } })
  fireEvent.change(screen.getByLabelText('Continuity note'), { target: { value: 'x'.repeat(501) } })
  fireEvent.click(screen.getByRole('button', { name: 'Add continuity note' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('character limit')
  expect(screen.getByLabelText('Continuity note')).toHaveValue('x'.repeat(501))
  fireEvent.click(screen.getByRole('button', { name: 'Resume scheduled turns' }))
  await screen.findByText('Allowed by policy')
  expect(screen.getByRole('region', { name: 'Control journal' })).toHaveTextContent('2 · resume')
  state = await (await fetch(endpoint)).json()
  expect(state.journal.map((row: { action: string }) => row.action)).toEqual(['pause', 'resume'])
  expect(state.slots.persona[0].tombstoned).toBe(true)
  expect(state.heartbeat_paused).toBe(false)
})
