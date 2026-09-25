import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import NativeCalls from './NativeCalls'
let child: ChildProcess
let home: string
let base: string
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-navigation-ui-'))
  child = spawn('/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_ui.py', home], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  base = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', data => { output += String(data); if (output.includes('\n')) accept(output.trim() + '/api/capabilities/experience') })
    child.stderr!.on('data', data => { errors += String(data) })
    child.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    child.on('error', reject)
  })
})
afterAll(async () => { child?.kill(); await rm(home, { recursive: true, force: true }) })
it('shows actual Linux unavailability and prevents invoking unavailable native controls', async () => {
  render(<NativeCalls baseUrl={base} />)
  await screen.findByText('Native control unavailable')
  expect(screen.getByText('Native FaceTime control requires macOS')).toBeVisible()
  for (const name of ['probe', 'call', 'answer', 'hangup']) {
    expect(screen.getByRole('button', { name })).toBeDisabled()
  }
  expect(screen.getByRole('button', { name: 'Save transcript to conversation' })).toBeDisabled()
  expect(screen.getByText(/Remote desktop execution and automatic audio transcription are not available/)).toBeVisible()
  const response = await fetch(base + '/native-calls')
  expect(response.status).toBe(200)
  const data = await response.json()
  expect(data.requests).toEqual([])
  expect(data.readiness.audio_transport).toBe('unqualified')
  expect(data.readiness.accessibility).toBe('unverified')
})
it('refreshes a persisted unavailable request without suggesting it connected', async () => {
  render(<NativeCalls baseUrl={base} />)
  await screen.findByText('Native control unavailable')
  const response = await fetch(base + '/native-calls', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'call', request_id: 'from-real-http' }) })
  const row = await response.json()
  expect(row.state).toBe('unavailable')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh native readiness' }))
  await screen.findByRole('option', { name: 'call · unavailable' })
  expect(screen.getByText(/call: unavailable/)).toBeVisible()
  expect(screen.queryByText('Connected')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'call' })).toBeDisabled()
  const replay = await fetch(base + '/native-calls', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'call', request_id: 'from-real-http' }) })
  expect(await replay.json()).toEqual(row)
  const reloaded = await (await fetch(base + '/native-calls')).json()
  expect(reloaded.requests).toHaveLength(1)
  expect(reloaded.requests[0].id).toBe(row.id)
})
it('uses human-readable supplied transcript controls and retains entered text after actual missing-session refusal', async () => {
  render(<NativeCalls baseUrl={base} />)
  await screen.findByRole('option', { name: 'call · unavailable' })
  const data = await (await fetch(base + '/native-calls')).json()
  fireEvent.change(screen.getByLabelText('Native request'), { target: { value: data.requests[0].id } })
  expect(screen.getByRole('button', { name: 'Save transcript to conversation' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Existing conversation'), { target: { value: 'missing' } })
  fireEvent.change(screen.getByLabelText('Supplied transcript'), { target: { value: 'My own conversation notes' } })
  const save = screen.getByRole('button', { name: 'Save transcript to conversation' })
  expect(save).toBeEnabled()
  fireEvent.click(save)
  expect(await screen.findByRole('alert')).toHaveTextContent('Existing conversation is unavailable')
  expect(screen.getByLabelText('Supplied transcript')).toHaveValue('My own conversation notes')
  expect(screen.getByLabelText('Existing conversation')).toHaveValue('missing')
  expect(screen.queryByText(/User-supplied transcript saved/)).not.toBeInTheDocument()
  await waitFor(() => expect(save).toBeEnabled())
  expect(screen.getByText(/does not verify that a call connected/)).toBeVisible()
})
