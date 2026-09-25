import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import Page from './Page'

let processHandle: ChildProcess
let home: string
let base: string
const root = resolve(process.cwd(), '../..')

beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-experience-ui-'))
  processHandle = spawn('/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_ui.py', home], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  base = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    processHandle.stdout!.on('data', data => { output += String(data); if (output.includes('\n')) accept(output.trim() + '/api/capabilities/experience') })
    processHandle.stderr!.on('data', data => { errors += String(data) })
    processHandle.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    processHandle.on('error', reject)
  })
})

afterAll(async () => {
  processHandle?.kill()
  await rm(home, { recursive: true, force: true })
})

async function read(path: string) {
  const response = await fetch(base + path)
  expect(response.ok).toBe(true)
  return response.json()
}

it('authors a two-ending graph through actual controls and plays both branches over HTTP', async () => {
  location.hash = '/capabilities/experience'
  const mounted = render(<Page baseUrl={base} />)
  await screen.findByText('No stories yet. Write an opening below.')
  expect(screen.getByRole('button', { name: 'Save story' })).toBeEnabled()
  fireEvent.change(screen.getByLabelText('Story title'), { target: { value: 'River journey' } })
  fireEvent.change(screen.getByLabelText('Scene 1 text'), { target: { value: 'Choose the bridge or ferry.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add scene' }))
  fireEvent.change(screen.getByLabelText('Scene 2 text'), { target: { value: 'You reach the sunny hill.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add scene' }))
  fireEvent.change(screen.getByLabelText('Scene 3 text'), { target: { value: 'You reach the quiet harbor.' } })
  fireEvent.change(screen.getByLabelText('Scene 1 kind'), { target: { value: 'scene' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add choice to scene 1' }))
  fireEvent.change(screen.getByLabelText('Choice 1.1'), { target: { value: 'Take the bridge' } })
  const targets = screen.getByLabelText('Target 1.1') as HTMLSelectElement
  fireEvent.change(targets, { target: { value: targets.options[1].value } })
  fireEvent.click(screen.getByRole('button', { name: 'Add choice to scene 1' }))
  fireEvent.change(screen.getByLabelText('Choice 1.2'), { target: { value: 'Take the ferry' } })
  fireEvent.change(screen.getByLabelText('Target 1.2'), { target: { value: targets.options[2].value } })
  fireEvent.click(screen.getByRole('button', { name: 'Save story' }))
  await screen.findByRole('button', { name: 'Play River journey' })
  await waitFor(() => expect(location.hash).toContain('story='))
  const stored = await read('/stories')
  expect(stored.stories).toHaveLength(1)
  expect(stored.stories[0].nodes).toHaveLength(3)
  expect(stored.stories[0].transitions).toHaveLength(2)
  expect(stored.stories[0].nodes[0].choices[0].label).toBe('Take the bridge')
  expect(stored.stories[0].revision).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: 'Play River journey' }))
  await screen.findByRole('region', { name: 'Story player' })
  fireEvent.click(screen.getByRole('button', { name: 'Take the bridge' }))
  await screen.findByText('You reach the sunny hill.')
  expect(screen.getByText('The end')).toBeInTheDocument()
  const firstRoute = location.hash
  const firstId = new URLSearchParams(firstRoute.split('?')[1]).get('session')
  const first = await read(`/sessions/${firstId}`)
  expect(first.session.history).toHaveLength(1)
  expect(first.session.revision).toBe(2)
  mounted.unmount()
  render(<Page baseUrl={base} />)
  await screen.findByText('You reach the sunny hill.')
  expect(location.hash).toBe(firstRoute)
  fireEvent.click(screen.getByRole('button', { name: 'Play River journey' }))
  await screen.findByRole('button', { name: 'Take the ferry' })
  fireEvent.click(screen.getByRole('button', { name: 'Take the ferry' }))
  await screen.findByText('You reach the quiet harbor.')
  const all = await read('/sessions')
  expect(all.sessions).toHaveLength(2)
  expect(new Set(all.sessions.map((s: { current_node: string }) => s.current_node)).size).toBe(2)
  expect((await read(`/sessions/${firstId}`)).node.text).toBe('You reach the sunny hill.')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('keeps an invalid authored graph visible and surfaces the actual server error', async () => {
  location.hash = '/capabilities/experience'
  render(<Page baseUrl={base} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save story' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Story title'), { target: { value: 'Disconnected ending' } })
  fireEvent.change(screen.getByLabelText('Scene 1 text'), { target: { value: 'An ending.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add scene' }))
  fireEvent.change(screen.getByLabelText('Scene 2 text'), { target: { value: 'Cannot be reached.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save story' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('reachable')
  expect(screen.getByLabelText('Story title')).toHaveValue('Disconnected ending')
  expect(screen.getByLabelText('Scene 2 text')).toHaveValue('Cannot be reached.')
  expect((await read('/stories')).stories).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Remove scene 2' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save story' }))
  await screen.findByRole('button', { name: 'Play Disconnected ending' })
  expect((await read('/stories')).stories).toHaveLength(2)
})

it('loads a bookmarked authored story and preserves a draft when a concurrent edit wins', async () => {
  const story = (await read('/stories')).stories.find((s: { title: string }) => s.title === 'River journey')
  location.hash = `/capabilities/experience?story=${story.id}`
  render(<Page baseUrl={base} />)
  await waitFor(() => expect(screen.getByLabelText('Story title')).toHaveValue('River journey'))
  const response = await fetch(`${base}/stories/${story.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'Concurrent edit', start_node: story.start_node, nodes: story.nodes, revision: story.revision }) })
  expect(response.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Story title'), { target: { value: 'My draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save story' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('revision changed')
  expect(screen.getByLabelText('Story title')).toHaveValue('My draft')
  expect((await read(`/stories/${story.id}`)).story.title).toBe('Concurrent edit')
  const scene = screen.getByRole('region', { name: 'Scene 1' })
  expect(within(scene).getByLabelText('Choice 1.1')).toHaveValue('Take the bridge')
})
