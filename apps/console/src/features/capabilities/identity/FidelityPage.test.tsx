import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import FidelityPage from './FidelityPage'
import userEvent from '@testing-library/user-event'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/fidelity_ui_server.py'), resolve(directory, 'stories.sqlite3'),
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

test('source-linked case and observation persist through the actual HTTP application', async () => {
  const twinEndpoint = endpoint.replace('/fidelity', '/twin')
  const before = await (await fetch(twinEndpoint)).json()
  const seeded = await fetch(twinEndpoint + '/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'Values', text: 'Curiosity and honesty matter.', expected_revision: before.revision }) })
  expect(seeded.status).toBe(200)
  const state = await seeded.json()
  const source = state.documents[0]
  render(<FidelityPage endpoint={endpoint} />)
  await screen.findByText('No fidelity cases yet.')
  expect(screen.getByText(/Literal checks do not measure semantic fidelity/)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Check supplied observation' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'What matters to me?' } })
  await userEvent.selectOptions(screen.getByLabelText('Identity sources'), source.id)
  fireEvent.change(screen.getByLabelText('Expected text'), { target: { value: 'curiosity' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  await screen.findByRole('button', { name: 'What matters to me?' })
  const cases = await (await fetch(endpoint + '/cases')).json()
  expect(cases).toHaveLength(1)
  expect(cases[0].source_ids).toEqual([source.id])
  expect(window.location.hash).toContain(cases[0].id)
  fireEvent.change(screen.getByLabelText('Supplied answer'), { target: { value: 'Curiosity is central.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Check supplied observation' }))
  await screen.findByText('Supplied observation · completed · Literal rules passed')
  const runs = await (await fetch(endpoint + '/runs')).json()
  expect(runs).toHaveLength(1)
  expect(runs[0].origin).toBe('supplied_observation')
  expect(runs[0].provider).toBeNull()
  expect(runs[0].model).toBeNull()
  expect(runs[0].source_snapshot[0].text).toBe('Curiosity and honesty matter.')
  fireEvent.change(screen.getByLabelText('Expected text'), { target: { value: 'honesty' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save case' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Supplied answer'), { target: { value: 'Only curiosity.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Check supplied observation' }))
  await screen.findByText('Supplied observation · completed · Literal rules failed')
  expect(screen.getByText('Supplied observation · completed · Literal rules passed')).toBeVisible()
  cleanup()
  render(<FidelityPage endpoint={endpoint} />)
  await waitFor(() => expect(screen.getByLabelText('Expected text')).toHaveValue('honesty'))
  expect(screen.getByLabelText('Question')).toHaveValue('What matters to me?')
  expect(screen.getByRole('region', { name: 'Evaluation history' })).toHaveTextContent('Curiosity is central.')
  const current = await (await fetch(endpoint + '/cases')).json()
  const external = await fetch(endpoint + '/cases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: current[0].id, expected_revision: current[0].revision, prompt: 'External question', source_ids: [source.id], rules: current[0].rules }) })
  expect(external.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'Unsaved question' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Case changed; reload before saving')
  expect(screen.getByLabelText('Question')).toHaveValue('Unsaved question')
  fireEvent.click(screen.getByRole('button', { name: 'Reload checks' }))
  await waitFor(() => expect(screen.getByLabelText('Question')).toHaveValue('External question'))
})
