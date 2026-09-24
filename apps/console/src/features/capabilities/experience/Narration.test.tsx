import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Narration from './Narration'

let child: ChildProcess
let home: string
let base: string
let sessionId: string
const root = resolve(process.cwd(), '../..')

beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-narration-ui-'))
  child = spawn('/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_ui.py', home], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  base = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', data => { output += String(data); if (output.includes('\n')) accept(output.trim() + '/api/capabilities/experience') })
    child.stderr!.on('data', data => { errors += String(data) })
    child.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    child.on('error', reject)
  })
  const response = await fetch(base + '/stories', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'Narrated walk', start_node: 'start', nodes: [
    { id: 'start', text: 'Walking through the trees.', kind: 'scene', choices: [{ id: 'walk', label: 'Walk', target: 'end' }] },
    { id: 'end', text: 'At the clearing.', kind: 'ending', choices: [] },
  ] }) })
  expect(response.status).toBe(201)
  const { story } = await response.json()
  const opened = await fetch(base + '/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ story_id: story.id, story_revision: 1, request_id: 'open' }) })
  expect(opened.status).toBe(201)
  sessionId = (await opened.json()).session.id
})

afterAll(async () => {
  child?.kill()
  await rm(home, { recursive: true, force: true })
})

it('reports the real missing-provider state and permits a fresh retry without fake playback', async () => {
  render(<Narration sessionId={sessionId} revision={1} baseUrl={base} />)
  expect(screen.getByRole('button', { name: 'Narrate this scene' })).toBeEnabled()
  expect(screen.queryByLabelText('Scene narration audio')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Narrate this scene' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('unavailable'))
  expect(screen.getByRole('status')).toHaveTextContent('Configure Speech settings')
  expect(screen.getByRole('button', { name: 'Narrate this scene' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: 'Cancel narration' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Scene narration audio')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Narrate this scene' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('unavailable'))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('reports source revision conflicts from the actual server', async () => {
  render(<Narration sessionId={sessionId} revision={99} baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Narrate this scene' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('revision changed')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Scene narration audio')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Narrate this scene' })).toBeEnabled()
})

it('clears old node state when the actual session advances', async () => {
  const mounted = render(<Narration sessionId={sessionId} revision={1} baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Narrate this scene' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('unavailable'))
  const response = await fetch(`${base}/sessions/${sessionId}/choices`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ choice_id: 'walk', revision: 1, request_id: 'walk' }) })
  expect(response.status).toBe(200)
  expect((await response.json()).session.revision).toBe(2)
  mounted.rerender(<Narration sessionId={sessionId} revision={2} baseUrl={base} />)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Narrate this scene' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('unavailable'))
  expect(screen.queryByLabelText('Scene narration audio')).not.toBeInTheDocument()
})

it('rejects a missing session without acquiring any audio source', async () => {
  render(<Narration sessionId="missing" revision={1} baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Narrate this scene' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('session not found')
  expect(screen.queryByLabelText('Scene narration audio')).not.toBeInTheDocument()
})
